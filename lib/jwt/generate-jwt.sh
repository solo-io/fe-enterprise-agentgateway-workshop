#!/usr/bin/env bash
# Non-interactive RS256 JWT signer for workshop demos.
#
# Usage:
#   generate-jwt.sh <claims.json>    # read claims from file
#   generate-jwt.sh -                # read claims JSON from stdin
#
# Prints the signed JWT to stdout and exits. Uses the keypair
# (private.pem / public.pem) next to this script, generating it on first
# run; the matching JWKS lives at jwks.json so the gateway can verify
# tokens this script signs.
#
# Demo-only: do not use these keys outside of workshop labs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE_KEY="$SCRIPT_DIR/private.pem"
KID="workshop-jwt-key-001"
PUBLIC_KEY="$SCRIPT_DIR/public.pem"
JWKS_FILE="$SCRIPT_DIR/jwks.json"

# First run generates a demo-only keypair and the matching JWKS document.
# The keys are gitignored: every clone mints its own, and jwks.json always
# matches the private key that signs.
if [[ ! -f "$PRIVATE_KEY" ]]; then
  echo "generating demo keypair at $SCRIPT_DIR (first run)" >&2
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$PRIVATE_KEY" 2>/dev/null
  openssl rsa -in "$PRIVATE_KEY" -pubout -out "$PUBLIC_KEY" 2>/dev/null
fi

if [[ ! -f "$JWKS_FILE" || "$PRIVATE_KEY" -nt "$JWKS_FILE" ]]; then
  MODULUS_HEX="$(openssl rsa -in "$PRIVATE_KEY" -noout -modulus | cut -d= -f2)"
  python3 - "$MODULUS_HEX" "$KID" >"$JWKS_FILE" <<'PYEOF'
import base64, json, sys
n = base64.urlsafe_b64encode(bytes.fromhex(sys.argv[1])).rstrip(b"=").decode()
print(json.dumps({"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256",
                            "kid": sys.argv[2], "n": n, "e": "AQAB"}]}, indent=2))
PYEOF
fi

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <claims.json | ->" >&2
  exit 1
fi

if [[ "$1" == "-" ]]; then
  CLAIMS="$(cat)"
elif [[ -f "$1" ]]; then
  CLAIMS="$(cat "$1")"
else
  echo "error: claims file not found: $1" >&2
  exit 1
fi

b64url() {
  base64 | tr -d '=\n' | tr '/+' '_-'
}

HEADER_JSON="{\"alg\":\"RS256\",\"typ\":\"JWT\",\"kid\":\"$KID\"}"
HEADER_B64="$(printf '%s' "$HEADER_JSON" | b64url)"
PAYLOAD_B64="$(printf '%s' "$CLAIMS"     | b64url)"
SIGNING_INPUT="$HEADER_B64.$PAYLOAD_B64"
SIGNATURE_B64="$(printf '%s' "$SIGNING_INPUT" \
  | openssl dgst -sha256 -sign "$PRIVATE_KEY" \
  | b64url)"

printf '%s.%s\n' "$SIGNING_INPUT" "$SIGNATURE_B64"
