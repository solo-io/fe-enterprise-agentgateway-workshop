# OAuth 2.0 Authorization Code + PKCE helpers

Two small, dependency-free Python scripts that let a **public** OAuth client (a CLI with
no client secret) obtain and maintain an access token from an OIDC identity provider.
They are IdP-agnostic; the labs that use them configure Auth0.

| File | Role | Interactive? |
|---|---|---|
| `pkce-login.py` | Runs the Authorization Code + PKCE flow in a browser and caches the tokens | Yes; run once per token lifetime |
| `pkce-token.py` | Prints a valid access token on stdout, refreshing it silently when expired | **No**; safe to invoke repeatedly from a harness |

The split exists because credential helpers are invoked automatically and repeatedly.
A helper that opened a browser or bound a listening port on every call would be
unusable, so all interactivity lives in `pkce-login.py`.

## Configuration

`pkce-login.py` reads its configuration from the environment:

| Variable | Required | Description |
|---|---|---|
| `OIDC_ISSUER` | yes | Issuer URL **with trailing slash**, e.g. `https://tenant.us.auth0.com/` |
| `OIDC_CLIENT_ID` | yes | Client ID of a public client (Auth0: a **Native** application) |
| `OIDC_AUDIENCE` | yes | API identifier. Without it Auth0 issues an **opaque** token that cannot be validated as a JWT |
| `OIDC_PORT` | no | Loopback redirect port, default `8910`. Must match the callback URL registered with the IdP |
| `OIDC_SCOPES` | no | Default `openid profile email offline_access`. `offline_access` is what yields a refresh token |
| `OIDC_CACHE` | no | Cache path, default `~/.agentgateway/pkce-token.json` |

`pkce-token.py` deliberately reads **only the cache**: the IdP issuer, client ID and
audience are written into it at login. A harness that invokes the helper may not
inherit your shell environment, so depending on env vars there would be fragile.
It honours `OIDC_CACHE` and `OIDC_SKEW_SECONDS` (default `120`, how early to renew).

## Usage

```bash
export OIDC_ISSUER="https://your-tenant.us.auth0.com/"
export OIDC_CLIENT_ID="<native app client id>"
export OIDC_AUDIENCE="api://your-api"

./lib/oauth-pkce/pkce-login.py     # browser login, once
./lib/oauth-pkce/pkce-token.py     # prints a token; refreshes if needed
```

Wire the second script into any tool that accepts a credential-helper command. For
Claude Code that is `apiKeyHelper` in `settings.json`, which sends the script's stdout
as the request credential:

```json
{
  "apiKeyHelper": "/absolute/path/to/lib/oauth-pkce/pkce-token.py"
}
```

Test it without modifying your real Claude Code configuration. `--bare` restricts auth
to `ANTHROPIC_API_KEY` or an `apiKeyHelper` passed via `--settings`, and inline JSON
settings are scoped to the single invocation:

```bash
claude --bare -p "Reply with exactly: OK" \
  --settings "{\"apiKeyHelper\":\"$PWD/lib/oauth-pkce/pkce-token.py\"}" < /dev/null
```

Succeeding with no `ANTHROPIC_API_KEY` set proves the helper supplied the credential.
Note that `apiKeyHelper` takes **precedence over** `ANTHROPIC_API_KEY`, so that variable
can neither shadow nor disable it.

## Contract for `pkce-token.py`

Credential helpers have a narrow contract, and this script honours it:

- **stdout carries only the token**: every message goes to stderr
- **never interactive**: it will not open a browser or wait for input
- **exits non-zero with an explanation** when it cannot produce a token, rather than
  hanging or printing something unusable

## Security notes

- The cache holds a refresh token and is written with mode `0600`. Treat it as a
  credential: don't commit it, don't copy it between machines.
- The `code_verifier` never touches disk and never leaves the process.
- Delete the cache to force a fresh login: `rm ~/.agentgateway/pkce-token.json`.
- Revoking the user's grant at the IdP invalidates the refresh token, so the next
  renewal fails closed and the user must log in again.

## Used by

- [`labs/agent-harnesses/claude-code-auth0-pkce.md`](../../labs/agent-harnesses/claude-code-auth0-pkce.md): keeps Claude Code authenticated to an Anthropic route through the gateway
