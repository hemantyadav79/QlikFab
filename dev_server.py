"""
Static file server for the migration UI, plus narrow proxies for Qlik Cloud and
Microsoft Fabric.

Why the proxies exist: a Qlik Cloud tenant only answers cross-origin browser
requests whose origin it has been configured to allow, so calling the tenant
straight from the page returns 401 with an empty body even when the API key is
valid (the same key works fine from curl). Routing through this server makes the
call same-origin from the browser's point of view, and the tenant sees an
ordinary server-to-server request.

Microsoft Fabric is the same story with an extra step: api.fabric.microsoft.com
sends no CORS headers at all, and the Entra client-credentials token endpoint
refuses a client secret sent from a browser origin (that flow is server-only by
design). So the Fabric side gets two routes — one that exchanges the service
principal for a token, one that relays the Fabric REST call.

Credentials are forwarded from the browser for the one call they belong to and
are never logged or written to disk.

    python dev_server.py [port]
"""

import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request

import engine_runner
import fabric_publisher

# Serve the project regardless of where the launcher happened to be standing.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
PROXY_PATH = "/qlik-proxy"
FABRIC_PROXY_PATH = "/fabric-proxy"
FABRIC_TOKEN_PATH = "/fabric-token"
# Real engine runs: start one, poll it, download what it produced.
RUNS_PATH = "/api/runs"
# Exports a live Qlik Cloud app and runs the engine on it, all server-side, so
# the .qvf never travels out to the browser and back.
RUNS_FROM_QLIK_PATH = "/api/runs/from-qlik"

# Kept deliberately narrow: each relay must forward to its own service and
# nothing else, so a stray link cannot turn the dev server into an open relay.
ALLOWED_HOST_SUFFIXES = (".qlikcloud.com", ".qlik.com")
FABRIC_HOST_SUFFIXES = (
    "api.fabric.microsoft.com",
    "api.powerbi.com",
    ".analysis.windows.net",
)
ENTRA_LOGIN_HOST = "login.microsoftonline.com"
# The Fabric data-plane audience. The Power BI audience
# (https://analysis.windows.net/powerbi/api/.default) is also accepted by
# api.fabric.microsoft.com, but the Fabric one is what the docs specify.
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
UPSTREAM_TIMEOUT_SECONDS = 30
# An app export materialises the whole .qvf on the tenant before it can be
# downloaded, which is minutes for a large app rather than seconds.
EXPORT_TIMEOUT_SECONDS = 300
MAX_BODY_BYTES = 64 * 1024


class MigrationUIHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == PROXY_PATH:
            self.handle_proxy(ALLOWED_HOST_SUFFIXES, "a Qlik tenant")
            return
        if path == FABRIC_PROXY_PATH:
            self.handle_proxy(FABRIC_HOST_SUFFIXES, "a Microsoft Fabric endpoint")
            return
        if path.startswith(RUNS_PATH + "/"):
            self.handle_run_get(path[len(RUNS_PATH) + 1:])
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == FABRIC_TOKEN_PATH:
            self.handle_fabric_token()
            return
        if path == RUNS_FROM_QLIK_PATH:
            self.handle_run_from_qlik()
            return
        if path == RUNS_PATH:
            self.handle_run_start()
            return
        # /api/runs/<id>/publish — pushes what the run produced into a Fabric
        # workspace. Server-side, so the PBIP never travels to the browser.
        if path.startswith(RUNS_PATH + "/") and path.endswith("/publish"):
            self.handle_run_publish(path[len(RUNS_PATH) + 1:-len("/publish")])
            return
        # Exporting a Qlik app is a POST; the same relay handles it so the browser
        # never talks to the tenant directly.
        if path == PROXY_PATH:
            self.handle_proxy(ALLOWED_HOST_SUFFIXES, "a Qlik tenant", method="POST")
            return
        # Answering without consuming the body makes the client see a reset
        # connection ("Failed to fetch") instead of this 404, which sends anyone
        # debugging it looking in the wrong place.
        self.drain_body()
        self.send_json(404, {"proxyError": "No POST route at %r." % path})

    def drain_body(self):
        try:
            remaining = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                return
            remaining -= len(chunk)

    # ------------------------------------------------------------------
    # Real engine runs
    # ------------------------------------------------------------------

    def handle_run_start(self):
        """Accepts a .qvf body and starts the real engine on it."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self.send_json(400, {"error": "Expected a .qvf file as the request body."})
            return
        if length > engine_runner.MAX_UPLOAD_BYTES:
            self.send_json(413, {
                "error": "That file is %.1f MB; the limit is %d MB."
                         % (length / 1048576.0, engine_runner.MAX_UPLOAD_BYTES // 1048576)
            })
            return

        name = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("name") or ["upload.qvf"])[0]
        payload = self.rfile.read(length)
        run = engine_runner.STORE.create(name, payload)
        run.start()
        self.send_json(202, run.snapshot())

    def handle_run_from_qlik(self):
        """Starts a run whose source is a live Qlik Cloud app.

        The API key arrives in the Authorization header, is used for the two
        export calls, and is not stored or logged.
        """
        self.drain_body()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        tenant = (query.get("tenant") or [""])[0]
        app_id = (query.get("appId") or [""])[0]
        name = (query.get("name") or [""])[0] or app_id
        authorization = self.headers.get("Authorization")

        missing = [label for label, value in
                   (("tenant", tenant), ("appId", app_id), ("Authorization header", authorization))
                   if not value]
        if missing:
            self.send_json(400, {"error": "Missing %s." % ", ".join(missing)})
            return

        run = engine_runner.STORE.create(name, payload=None)
        run.start_from_qlik(tenant, app_id, authorization)
        self.send_json(202, run.snapshot())

    def handle_run_publish(self, run_id):
        """Publishes a finished run into a Fabric workspace.

        The access token arrives in the Authorization header — the browser
        already holds one from /fabric-token — is used for the Fabric calls, and
        is not stored or logged.
        """
        self.drain_body()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        workspace_id = (query.get("workspace") or [""])[0]
        display_name = (query.get("name") or [""])[0]
        authorization = self.headers.get("Authorization") or ""
        token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""

        run = engine_runner.STORE.get(run_id)
        if not run:
            self.send_json(404, {"error": "No run %r. Runs are kept in memory and are lost when the server restarts." % run_id})
            return
        if not workspace_id:
            self.send_json(400, {"error": "No target workspace was given."})
            return
        if not token:
            self.send_json(401, {"error": "No Fabric access token. Run Test Fabric Connection first."})
            return
        # Publishing what a failed run left behind would put a half-built model
        # in the workspace, so only a completed run is publishable.
        if run.status != "completed":
            self.send_json(409, {
                "error": "This run is %s, so there is nothing complete to publish." % run.status
            })
            return
        if not os.path.isdir(run.output_dir):
            self.send_json(409, {"error": "The run produced no output directory to publish."})
            return

        name = display_name or engine_runner.safe_stem(run.filename)
        try:
            result = fabric_publisher.publish(
                run.output_dir, workspace_id, token, name, note=run.note
            )
        except Exception as err:                  # noqa: BLE001 - shown to the user verbatim
            run.note("[publish] FAILED: %s" % err)
            self.send_json(502, {"error": str(err)})
            return

        run.note("[publish] Published '%s' to workspace %s." % (name, workspace_id))
        result["workspaceId"] = workspace_id
        self.send_json(200, result)

    def handle_run_get(self, rest):
        parts = [p for p in rest.split("/") if p]
        if not parts:
            self.send_json(404, {"error": "No run id given."})
            return

        run = engine_runner.STORE.get(parts[0])
        if not run:
            self.send_json(404, {"error": "No run %r. Runs are kept in memory and are lost when the server restarts." % parts[0]})
            return

        if len(parts) == 1:
            try:
                since = int((urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("since") or ["0"])[0])
            except ValueError:
                since = 0
            self.send_json(200, run.snapshot(since=max(0, since)))
            return

        if parts[1] == "download":
            self.send_run_artifact(run)
            return

        self.send_json(404, {"error": "No route %r on a run." % parts[1]})

    def send_run_artifact(self, run):
        """Streams the bundle the engine actually wrote. There is nothing to send
        until it has written one, and none is synthesised in its place."""
        path = run.artifact_path
        if not path or not os.path.exists(path):
            self.send_json(409, {
                "error": "This run has produced no bundle to download (status: %s)." % run.status
            })
            return
        with open(path, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(path))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_proxy(self, host_suffixes, service_label, method="GET"):
        query = urllib.parse.urlparse(self.path).query
        target = (urllib.parse.parse_qs(query).get("target") or [""])[0]

        problem = self.validate_target(target, host_suffixes, service_label)
        if problem:
            self.drain_body()
            self.send_json(400, {"proxyError": problem})
            return

        request = urllib.request.Request(target, method=method)
        # The browser holds the credential; this server only relays it upstream.
        authorization = self.headers.get("Authorization")
        if authorization:
            request.add_header("Authorization", authorization)
        request.add_header("Accept", "application/json")

        # An app export is a POST with no body; reading it anyway keeps the
        # connection clean for whatever the browser sends next.
        self.drain_body()

        # Exporting an app materialises the whole .qvf upstream, which takes far
        # longer than a metadata read.
        timeout = EXPORT_TIMEOUT_SECONDS if "/export" in target or "/temp-contents/" in target else UPSTREAM_TIMEOUT_SECONDS

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                # Qlik answers an export with 201 and the download URL in Location.
                # Without forwarding that header the browser cannot find the file.
                self.relay(
                    response.status,
                    response.headers.get("Content-Type"),
                    response.read(),
                    upstream=True,
                    location=response.headers.get("Location"),
                )
        except urllib.error.HTTPError as err:
            # Pass the service's own status and error body straight through, so the
            # UI reports what the tenant said rather than what the proxy assumed.
            self.relay(err.code, err.headers.get("Content-Type"), err.read(), upstream=True,
                       location=err.headers.get("Location"))
        except urllib.error.URLError as err:
            self.send_json(502, {"proxyError": "Could not reach the tenant: %s" % err.reason})
        except Exception as err:  # noqa: BLE001 - the dev server must not die on one bad call
            self.send_json(502, {"proxyError": "Proxy failure: %s" % err})

    def handle_fabric_token(self):
        """Client-credentials exchange for a Fabric service principal.

        This cannot happen in the page: Entra rejects a confidential-client
        secret presented from a browser origin, and would not send the CORS
        headers for the response either way. The secret is used for this one
        request and is not retained.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(400, {"proxyError": "Expected a small JSON body with the credentials."})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            self.send_json(400, {"proxyError": "The credential body was not valid JSON."})
            return

        tenant_id = str(payload.get("tenantId") or "").strip()
        client_id = str(payload.get("clientId") or "").strip()
        client_secret = str(payload.get("clientSecret") or "")
        scope = str(payload.get("scope") or FABRIC_SCOPE).strip() or FABRIC_SCOPE

        missing = [
            label for label, value in (
                ("Tenant (Directory) ID", tenant_id),
                ("Client (Application) ID", client_id),
                ("Client secret", client_secret),
            ) if not value
        ]
        if missing:
            self.send_json(400, {"proxyError": "Missing: %s." % ", ".join(missing)})
            return
        # The tenant id lands inside a URL path, so refuse anything that could
        # steer the request somewhere other than the Entra token endpoint.
        if not all(ch.isalnum() or ch in "-._" for ch in tenant_id):
            self.send_json(400, {"proxyError": "The tenant ID contains characters that are not allowed."})
            return
        if not scope.startswith("https://"):
            self.send_json(400, {"proxyError": "The scope must be an https resource identifier."})
            return

        token_url = "https://%s/%s/oauth2/v2.0/token" % (
            ENTRA_LOGIN_HOST, urllib.parse.quote(tenant_id, safe="")
        )
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }).encode("utf-8")

        request = urllib.request.Request(token_url, data=body, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
                self.relay(response.status, "application/json", response.read())
        except urllib.error.HTTPError as err:
            # Entra's own error body names the real cause (wrong secret, wrong
            # tenant, consent missing), which is far more useful than a guess.
            self.relay(err.code, "application/json", err.read())
        except urllib.error.URLError as err:
            self.send_json(502, {"proxyError": "Could not reach Microsoft Entra ID: %s" % err.reason})
        except Exception as err:  # noqa: BLE001
            self.send_json(502, {"proxyError": "Token request failure: %s" % err})

    @staticmethod
    def validate_target(target, host_suffixes, service_label):
        if not target:
            return "No target URL was supplied."
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme != "https":
            return "Only https targets are proxied (got %r)." % parsed.scheme
        host = parsed.hostname or ""
        if not host.endswith(host_suffixes):
            return "Host %r is not %s; this proxy only forwards to %s." % (
                host, service_label, " or ".join(host_suffixes)
            )
        return None

    def relay(self, status, content_type, body, upstream=False, location=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Carried through so the page can follow an export to its download URL.
        if location:
            self.send_header("X-Upstream-Location", location)
        # Marks a status that came from the upstream service rather than from this
        # relay. A 404 is otherwise ambiguous: it reads the same whether the tenant
        # has no such resource or whether a plain static server has no proxy route,
        # and the page must not report one as the other.
        if upstream:
            self.send_header("X-Relay-Source", "upstream")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        self.relay(status, "application/json", json.dumps(payload).encode("utf-8"))

    def log_message(self, fmt, *args):
        # The default logger would print the full request line. Query strings here
        # carry the tenant URL, so only the path is logged.
        sys.stderr.write("%s - %s\n" % (self.address_string(), (fmt % args).split("?")[0]))


class ReusableServer(socketserver.ThreadingTCPServer):
    # Threaded on purpose: a browser holds keep-alive connections open, and a
    # single-threaded server would stall every later request behind them —
    # including the proxy call the page is waiting on.
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with ReusableServer(("localhost", PORT), MigrationUIHandler) as httpd:
        print("Migration UI on  http://localhost:%d" % PORT)
        print("Qlik proxy at    http://localhost:%d%s?target=<tenant url>" % (PORT, PROXY_PATH))
        print("Fabric proxy at  http://localhost:%d%s?target=<fabric url>" % (PORT, FABRIC_PROXY_PATH))
        print("Fabric token at  http://localhost:%d%s (POST)" % (PORT, FABRIC_TOKEN_PATH))
        print("Engine runs at   http://localhost:%d%s (POST a .qvf)" % (PORT, RUNS_PATH))
        print("Engine script    %s" % engine_runner.ENGINE_SCRIPT)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
