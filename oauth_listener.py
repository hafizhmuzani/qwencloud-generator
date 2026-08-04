#!/usr/bin/env python3
"""Local OAuth listener: captures redirect on :8085 and exchanges code instantly."""
import http.server
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

CLIENT_FILE = Path(__file__).parent / "client_secret.json"
TOKEN_FILE = Path(__file__).parent / "gmail_tokens.json"
EMAIL = "earlyright22@gmail.com"

creds = json.loads(CLIENT_FILE.read_text())["installed"]
CLIENT_ID = creds["client_id"]
CLIENT_SECRET = creds["client_secret"]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"no code")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>OK! Code captured. You can close this tab.</h1>")
        threading.Thread(target=exchange, args=(code,), daemon=True).start()

    def log_message(self, *a):
        pass


def exchange(code):
    print(f"[captured] code={code[:30]}...", flush=True)
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": "http://localhost:8085/callback",
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        result = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read().decode())
        print(f"[FAIL] {e.code}: {err}", flush=True)
        return

    data = json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else {"accounts": {}}
    data["default_client"] = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    data.setdefault("accounts", {})[EMAIL] = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": result["refresh_token"],
        "access_token": result["access_token"],
        "expires_in": result.get("expires_in", 3600),
        "expires_at": int(time.time()) + result.get("expires_in", 3600),
        "token_type": result.get("token_type", "Bearer"),
    }
    tmp = TOKEN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(TOKEN_FILE)
    print(f"[SUCCESS] tokens saved for {EMAIL}", flush=True)
    print(f"[SUCCESS] refresh_token={result['refresh_token'][:30]}...", flush=True)

    # verify refresh works
    vbody = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "refresh_token": result["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    vreq = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=vbody,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
    )
    vr = json.loads(urllib.request.urlopen(vreq, timeout=30).read().decode())
    print(f"[VERIFY] refresh OK, access_token={vr['access_token'][:30]}...", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", 8085), Handler)
    print("listening on http://localhost:8085/callback ...", flush=True)
    server.serve_forever()
