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

# Serve the project regardless of where the launcher happened to be standing.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
PROXY_PATH = "/qlik-proxy"
FABRIC_PROXY_PATH = "/fabric-proxy"
FABRIC_TOKEN_PATH = "/fabric-token"

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
        super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] == FABRIC_TOKEN_PATH:
            self.handle_fabric_token()
            return
        self.send_json(404, {"proxyError": "No POST route at %r." % self.path.split("?")[0]})

    def handle_proxy(self, host_suffixes, service_label):
        query = urllib.parse.urlparse(self.path).query
        target = (urllib.parse.parse_qs(query).get("target") or [""])[0]

        problem = self.validate_target(target, host_suffixes, service_label)
        if problem:
            self.send_json(400, {"proxyError": problem})
            return

        request = urllib.request.Request(target, method="GET")
        # The browser holds the credential; this server only relays it upstream.
        authorization = self.headers.get("Authorization")
        if authorization:
            request.add_header("Authorization", authorization)
        request.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
                self.relay(response.status, response.headers.get("Content-Type"), response.read())
        except urllib.error.HTTPError as err:
            # Pass the service's own status and error body straight through, so the
            # UI reports what the tenant said rather than what the proxy assumed.
            self.relay(err.code, err.headers.get("Content-Type"), err.read())
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

    def relay(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
    with ReusableServer(("", PORT), MigrationUIHandler) as httpd:
        print("Migration UI on  http://localhost:%d" % PORT)
        print("Qlik proxy at    http://localhost:%d%s?target=<tenant url>" % (PORT, PROXY_PATH))
        print("Fabric proxy at  http://localhost:%d%s?target=<fabric url>" % (PORT, FABRIC_PROXY_PATH))
        print("Fabric token at  http://localhost:%d%s (POST)" % (PORT, FABRIC_TOKEN_PATH))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
