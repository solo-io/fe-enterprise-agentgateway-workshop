#!/usr/bin/env python3
"""Non-interactive credential helper: print a valid access token on stdout.

Designed for Claude Code's `apiKeyHelper` setting, which executes this script and
sends whatever it prints on stdout as the request credential. It must therefore be
silent, fast, and never interactive:

  - stdout carries ONLY the token
  - all messages go to stderr
  - if the token is expired it is renewed with the cached refresh token
  - if renewal is impossible it exits non-zero rather than blocking on a browser

Reads everything from the cache written by pkce-login.py, so it needs no
environment of its own. That matters because the harness that invokes it may not
inherit your shell.

Optional environment:
  OIDC_CACHE          Cache file path (default ~/.agentgateway/pkce-token.json)
  OIDC_SKEW_SECONDS   Renew this many seconds before actual expiry (default 120)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CACHE = Path(os.environ.get("OIDC_CACHE", Path.home() / ".agentgateway" / "pkce-token.json"))
SKEW = int(os.environ.get("OIDC_SKEW_SECONDS", "120"))

if not CACHE.exists():
    sys.exit(f"ERROR: no cached credentials at {CACHE}. Run pkce-login.py first.")

try:
    data = json.loads(CACHE.read_text())
except (json.JSONDecodeError, OSError) as error:
    sys.exit(f"ERROR: cannot read {CACHE}: {error}. Re-run pkce-login.py.")

access_token = data.get("access_token", "")
expires_at = int(data.get("expires_at", 0))

# Still valid (with a safety margin)? Emit it and stop - no network call.
if access_token and time.time() < expires_at - SKEW:
    remaining = int(expires_at - time.time())
    print(f"token valid for another {remaining}s", file=sys.stderr)
    print(access_token)
    sys.exit(0)

refresh_token = data.get("refresh_token", "")
if not refresh_token:
    sys.exit("ERROR: access token expired and no refresh token is cached.\n"
             "  Run pkce-login.py again (and request 'offline_access' so renewals are silent).")

print("access token expired - refreshing", file=sys.stderr)
request = urllib.request.Request(
    data["issuer"].rstrip("/") + "/oauth/token",
    data=urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": data["client_id"],
        "refresh_token": refresh_token,
    }).encode(),
    headers={"content-type": "application/x-www-form-urlencoded"},
)
try:
    with urllib.request.urlopen(request) as response:
        token = json.load(response)
except urllib.error.HTTPError as error:
    body = error.read().decode()
    sys.exit(f"ERROR: refresh failed ({error.code}): {body}\n"
             "  The refresh token may be revoked, expired, or already rotated.\n"
             "  Run pkce-login.py to re-authenticate.")

new_access = token.get("access_token", "")
if not new_access:
    sys.exit(f"ERROR: refresh response contained no access_token: {token}")

data["access_token"] = new_access
data["expires_at"] = int(time.time()) + int(token.get("expires_in", 0))
# With refresh token rotation enabled (an Auth0 per-application setting, off by
# default) the response carries a NEW refresh token and invalidates the old one.
# Persist it whenever present, otherwise the next refresh fails.
if token.get("refresh_token"):
    data["refresh_token"] = token["refresh_token"]

CACHE.write_text(json.dumps(data, indent=2))
CACHE.chmod(0o600)

print(f"refreshed - valid for {token.get('expires_in')}s", file=sys.stderr)
print(new_access)
