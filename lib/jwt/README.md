# JWT Helper

A small RS256 JWT signer for workshop demos that need to mint tokens with arbitrary claims.

## Files

- `generate-jwt.sh` — non-interactive signer. Prints a JWT to stdout.
- `private.pem` / `public.pem` — committed RSA keypair. **Demo-only — do not use outside this workshop.**
- `jwks.json` — JWKS document encoding `public.pem`, ready to paste into an `EnterpriseAgentgatewayPolicy.spec.traffic.jwtAuthentication.providers[0].jwks.inline` block.
- `claims/` — sample claims files used by [`mcp-tool-federation.md`](../../labs/mcp/mcp-tool-federation.md) (academic / economist / analyst / admin personas).

## Usage

```bash
# Sign a claims file
TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/admin.json)

# Or pipe claims in
TOKEN=$(echo '{"iss":"workshop.solo.io","sub":"me","persona":"admin","exp":4070908800}' \
  | ./lib/jwt/generate-jwt.sh -)
```

The header is fixed to `{"alg":"RS256","typ":"JWT","kid":"workshop-jwt-key-001"}`. The `kid` matches the single key in `jwks.json`, so the gateway will pick the right key when validating.

Requires `openssl` and `base64` on `PATH`.

## Used by

- [`mcp-tool-federation.md`](../../labs/mcp/mcp-tool-federation.md) — persona-based tool filtering across a federated MCP backend.
