#!/usr/bin/env bash
# Non-interactive RS256 JWT signer for workshop demos.
#
# Usage:
#   generate-jwt.sh <claims.json>    # read claims from file
#   generate-jwt.sh -                # read claims JSON from stdin
#
# Prints the signed JWT to stdout and exits. Uses the committed keypair
# (private.pem / public.pem) next to this script; the matching JWKS lives
# at jwks.json so the gateway can verify tokens this script signs.
#
# Demo-only: the private key is checked into the repo. Do not use these
# keys outside of workshop labs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE_KEY="$SCRIPT_DIR/private.pem"
KID="workshop-jwt-key-001"

if [[ ! -f "$PRIVATE_KEY" ]]; then
  echo "error: private key not found at $PRIVATE_KEY" >&2
  exit 1
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
