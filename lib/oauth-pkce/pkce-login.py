#!/usr/bin/env python3
"""Interactive OAuth 2.0 Authorization Code + PKCE login for a public client.

Run this once per token lifetime. It opens a browser, completes the login against
your IdP, and caches the resulting tokens (plus the IdP config needed to refresh
them) so that pkce-token.py can renew silently.

Required environment:
  OIDC_ISSUER      Issuer URL with trailing slash, e.g. https://tenant.us.auth0.com/
  OIDC_CLIENT_ID   Client ID of a PUBLIC client (Auth0: a "Native" application)
  OIDC_AUDIENCE    API identifier; without it Auth0 returns an opaque token

Optional environment:
  OIDC_PORT        Loopback port for the redirect (default 8910). Must match the
                   callback URL registered with the IdP.
  OIDC_SCOPES      Space-separated scopes (default "openid profile email offline_access").
                   offline_access is what yields a refresh token.
  OIDC_CACHE       Cache file path (default ~/.agentgateway/pkce-token.json)

The cache is written with mode 0600 and holds a refresh token. Treat it as a
credential: do not commit it, and do not copy it between machines.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ISSUER = os.environ.get("OIDC_ISSUER", "").rstrip("/") + "/"
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
AUDIENCE = os.environ.get("OIDC_AUDIENCE", "")
PORT = int(os.environ.get("OIDC_PORT", "8910"))
SCOPES = os.environ.get("OIDC_SCOPES", "openid profile email offline_access")
CACHE = Path(os.environ.get("OIDC_CACHE", Path.home() / ".agentgateway" / "pkce-token.json"))
REDIRECT = f"http://localhost:{PORT}/callback"

if ISSUER == "/" or not CLIENT_ID or not AUDIENCE:
    sys.exit("ERROR: OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_AUDIENCE must all be set")

# RFC 7636: the verifier stays in memory; only its SHA-256 hash goes over the wire.
verifier = base64.urlsafe_b64encode(os.urandom(40)).decode().rstrip("=")
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
state = secrets.token_urlsafe(16)

authorize = ISSUER + "authorize?" + urllib.parse.urlencode({
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT,
    "scope": SCOPES,
    "audience": AUDIENCE,
    "code_challenge": challenge,
    "code_challenge_method": "S256",
    "state": state,
})

received = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        received.update({k: v[0] for k, v in
                         urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()})
        ok = "code" in received and received.get("state") == state
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Login complete. Return to your terminal.</h2>" if ok
                         else b"<h2>Login failed. Check your terminal.</h2>")

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.handle_request, daemon=True).start()

print(f"Opening browser for login...\n  {authorize}\n", file=sys.stderr)
webbrowser.open(authorize)

for _ in range(900):  # 15 minutes: enough for login, MFA and consent
    if received:
        break
    time.sleep(1)

if "code" not in received:
    sys.exit(f"ERROR: no authorization code received. Got: {received or '(nothing)'}")
if received.get("state") != state:
    sys.exit("ERROR: state mismatch - possible CSRF. Aborting.")

# The code_verifier takes the place of a client secret. None is sent.
request = urllib.request.Request(
    ISSUER + "oauth/token",
    data=urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": received["code"],
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
    }).encode(),
    headers={"content-type": "application/x-www-form-urlencoded"},
)
try:
    with urllib.request.urlopen(request) as response:
        token = json.load(response)
except urllib.error.HTTPError as error:
    sys.exit(f"ERROR: token exchange failed ({error.code}): {error.read().decode()}")

# Persist the IdP config alongside the tokens so the helper needs no environment.
CACHE.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "issuer": ISSUER,
    "client_id": CLIENT_ID,
    "audience": AUDIENCE,
    "access_token": token.get("access_token", ""),
    "refresh_token": token.get("refresh_token", ""),
    "expires_at": int(time.time()) + int(token.get("expires_in", 0)),
}
CACHE.write_text(json.dumps(payload, indent=2))
CACHE.chmod(0o600)

print(f"Cached credentials in {CACHE} (mode 0600)", file=sys.stderr)
print(f"  access_token is a JWT: {payload['access_token'].count('.') == 2}", file=sys.stderr)
print(f"  expires_in: {token.get('expires_in')}s", file=sys.stderr)
if payload["refresh_token"]:
    print("  refresh_token: present - renewals will be silent", file=sys.stderr)
else:
    print("  refresh_token: MISSING. Request the 'offline_access' scope and enable\n"
          "    'Allow Offline Access' on the API, or every expiry needs a new browser login.",
          file=sys.stderr)
