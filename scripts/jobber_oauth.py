"""
Jobber OAuth2 flow — run this to get JOBBER_ACCESS_TOKEN.
Starts a local listener on port 8080, opens the browser auth URL,
captures the code, exchanges for access + refresh tokens, prints them.
"""
import http.server
import json
import os
import threading
import urllib.parse
import urllib.request
import webbrowser

CLIENT_ID     = os.environ.get("JOBBER_CLIENT_ID", "e7fbb80b-d38f-45bc-9b83-46721dc5ab4e")
CLIENT_SECRET = os.environ.get("JOBBER_CLIENT_SECRET")
if not CLIENT_SECRET:
    raise SystemExit(
        "ERROR: environment variable JOBBER_CLIENT_SECRET is not set. "
        "Export it before running this script (see docs/SECRETS_ROTATION.md)."
    )
REDIRECT_URI  = "http://localhost:8080/callback"
AUTH_URL      = "https://api.getjobber.com/api/oauth/authorize"
TOKEN_URL     = "https://api.getjobber.com/api/oauth/token"

code_captured = threading.Event()
auth_code = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"""
                    <html><body style="font-family:sans-serif;text-align:center;padding:50px">
                    <h2 style="color:green">&#10003; Jobber authorized! You can close this window.</h2>
                    </body></html>
                """)
                code_captured.set()
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code parameter")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress server logs


def exchange_code(code):
    data = urllib.parse.urlencode({
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


if __name__ == "__main__":
    server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print("Listening on http://localhost:8080 ...")

    auth_params = urllib.parse.urlencode({
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
    })
    url = f"{AUTH_URL}?{auth_params}"
    print(f"Opening: {url}")
    webbrowser.open(url)

    print("Waiting for Jobber authorization...")
    code_captured.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("ERROR: No code received within 120s")
        raise SystemExit(1)

    print(f"Got code: {auth_code[:12]}...")
    tokens = exchange_code(auth_code)
    print("\n=== TOKENS ===")
    print(json.dumps(tokens, indent=2))
    print(f"\nPut this in .env:\nJOBBER_ACCESS_TOKEN={tokens.get('access_token', 'NOT_FOUND')}")
