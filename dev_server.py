"""
Static file server for the migration UI, plus a narrow proxy for Qlik Cloud.

Why the proxy exists: a Qlik Cloud tenant only answers cross-origin browser
requests whose origin it has been configured to allow, so calling the tenant
straight from the page returns 401 with an empty body even when the API key is
valid (the same key works fine from curl). Routing through this server makes the
call same-origin from the browser's point of view, and the tenant sees an
ordinary server-to-server request.

The API key is forwarded from the browser's Authorization header and is never
logged or written to disk.

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

# Kept deliberately narrow: this must forward to Qlik tenants and nothing else,
# so a stray link cannot turn the dev server into an open relay.
ALLOWED_HOST_SUFFIXES = (".qlikcloud.com", ".qlik.com")
UPSTREAM_TIMEOUT_SECONDS = 30


class MigrationUIHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == PROXY_PATH:
            self.handle_proxy()
            return
        super().do_GET()

    def handle_proxy(self):
        query = urllib.parse.urlparse(self.path).query
        target = (urllib.parse.parse_qs(query).get("target") or [""])[0]

        problem = self.validate_target(target)
        if problem:
            self.send_json(400, {"proxyError": problem})
            return

        request = urllib.request.Request(target, method="GET")
        # The browser holds the key; this server only relays it upstream.
        authorization = self.headers.get("Authorization")
        if authorization:
            request.add_header("Authorization", authorization)
        request.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
                self.relay(response.status, response.headers.get("Content-Type"), response.read())
        except urllib.error.HTTPError as err:
            # Pass the tenant's own status and error body straight through, so the
            # UI reports what Qlik said rather than what the proxy assumed.
            self.relay(err.code, err.headers.get("Content-Type"), err.read())
        except urllib.error.URLError as err:
            self.send_json(502, {"proxyError": "Could not reach the tenant: %s" % err.reason})
        except Exception as err:  # noqa: BLE001 - the dev server must not die on one bad call
            self.send_json(502, {"proxyError": "Proxy failure: %s" % err})

    @staticmethod
    def validate_target(target):
        if not target:
            return "No target URL was supplied."
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme != "https":
            return "Only https targets are proxied (got %r)." % parsed.scheme
        host = parsed.hostname or ""
        if not host.endswith(ALLOWED_HOST_SUFFIXES):
            return "Host %r is not a Qlik tenant; this proxy only forwards to %s." % (
                host, " or ".join(ALLOWED_HOST_SUFFIXES)
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
        print("Migration UI on http://localhost:%d" % PORT)
        print("Qlik proxy at  http://localhost:%d%s?target=<tenant url>" % (PORT, PROXY_PATH))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
