# MCP Authentication with Microsoft Entra ID via Eager OAuth

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

### Entra requirements

You need an app registration in Microsoft Entra ID with the **Authorization Code** grant enabled, an **Application ID URI**, and at least one exposed API scope. Capture these values for Step 1:

| Variable | Description |
|---|---|
| `ENTRA_TENANT_ID` | Directory (tenant) ID GUID from the app registration Overview blade |
| `ENTRA_CLIENT_ID` | Application (client) ID GUID |
| `ENTRA_CLIENT_SECRET` | Client secret value from Certificates & secrets (copy it at creation; Entra never shows it again) |
| `ENTRA_AUTHORITY` | `https://login.microsoftonline.com/${ENTRA_TENANT_ID}` |
| `ENTRA_AUDIENCE` | The Application ID URI, normally `api://${ENTRA_CLIENT_ID}` |
| `ENTRA_API_SCOPE` | The exposed scope, e.g. `api://${ENTRA_CLIENT_ID}/agentgateway` |
| `ENTRA_ISSUER` | Depends on the app's `accessTokenAcceptedVersion`. See the warning below before you set it |
| `ENTRA_GATEWAY_HOST` | Public hostname for the gateway (no scheme); this lab uses `mcp-entra.try-solo.io` |

> **⚠ Pick your issuer before anything else.** Entra mints two different `iss` values depending on
> the app manifest's `accessTokenAcceptedVersion`, and the MCP authentication policy compares the
> value literally.
>
> | `accessTokenAcceptedVersion` | `iss` on issued tokens |
> |---|---|
> | `null` or `1` (the default) | `https://sts.windows.net/${ENTRA_TENANT_ID}/` **with** a trailing slash |
> | `2` | `https://login.microsoftonline.com/${ENTRA_TENANT_ID}/v2.0` with **no** trailing slash |
>
> A new app registration defaults to v1 even though you call the v2.0 `/authorize` and `/token`
> endpoints, which surprises most people. This is deviation 2 in
> [How Entra Deviates](#how-entra-deviates-from-the-other-eager-oauth-labs). Decide which you want, set the manifest to match, and
> use the corresponding `ENTRA_ISSUER`. If a valid-looking token still returns `401`, decode it at
> [jwt.io](https://jwt.io) and compare `iss` against what you configured.

### Expose an API scope

Entra will not mint an access token carrying `aud: api://<client-id>` unless the authorization request asks for a scope on that API.

1. **Expose an API → Set the Application ID URI.** Accept the default `api://${ENTRA_CLIENT_ID}`.
2. **Add a scope**, e.g. `agentgateway`, admin-consent-only is fine for a lab.
3. Note the full scope string `api://${ENTRA_CLIENT_ID}/agentgateway`; it becomes `ENTRA_API_SCOPE`.

### Entra app callback URLs

Under **Authentication → Add a platform → Web**, register **both** gateway callbacks:

```
https://mcp-entra.try-solo.io/oauth-issuer/callback/downstream
https://mcp-entra.try-solo.io/oauth-issuer/callback/upstream
```

The eager-OAuth issuer runs a "dual OAuth flow" and uses different callback paths depending on the client. PKCE-capable MCP clients (e.g., MCP Inspector) trigger `/callback/upstream`; non-PKCE flows trigger `/callback/downstream`. Registering only one yields an Entra `AADSTS50011: The redirect URI ... does not match the redirect URIs configured for the application` error after login, even though the URI you configured for `downstream_server.redirect_uri` *is* in the allowlist.

### Required tools

- `kubectl` and `helm`
- `openssl` (for the self-signed gateway cert)
- Node 18+ (for MCP Inspector in Step 9)
- `jq` for inspecting JSON responses
- A way to resolve `mcp-entra.try-solo.io` from your workstation to the gateway LoadBalancer: either a real DNS record (production-style clusters) or a local `/etc/hosts` entry (KinD/minikube/local dev clusters; requires sudo)

---

## Lab Objectives

- Stand up the eager-OAuth feature so the gateway acts as the OAuth Authorization Server visible to MCP clients
- Give MCP clients a single pre-registered Entra `client_id` / `client_secret`, which is the only workable option because Entra has no Dynamic Client Registration endpoint
- Broker the Entra authorization code flow through the gateway (`/oauth-issuer/...`)
- Validate Entra-issued JWTs at the MCP backend against Entra JWKS
- Terminate TLS on `agentgateway-proxy` with a self-signed cert for `mcp-entra.try-solo.io`
- Test end-to-end with MCP Inspector against an `mcp-server-everything` test server

---

## Background

Why eager OAuth with Entra?

With Okta and Auth0, eager OAuth is a convenience: both support Dynamic Client Registration (RFC 7591), and the gateway spares you an admin-UI entry per MCP client. **With Entra it is the only option.** Microsoft Entra ID does not publish an RFC 7591 registration endpoint, and Microsoft's own guidance is to pre-register clients statically:

- [Does Azure AD support Dynamic Client Registration?](https://learn.microsoft.com/en-us/answers/questions/1328487/does-azure-ad-supports-dynamic-client-registration)
- [Building MCP servers with Entra ID and pre-authorized clients](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/building-mcp-servers-with-entra-id-and-pre-authorized-clients/4508453)

An MCP client that expects to DCR against its authorization server therefore cannot onboard itself to Entra at all. Eager OAuth closes that gap: agentgateway becomes the Authorization Server the client sees and answers the registration call itself, while Entra stays the identity authority behind it.

```
┌──────────────┐   1. discovery + DCR   ┌─────────────────┐  3. authorize/token  ┌───────┐
│  MCP client  │ ──────────────────────▶│  agentgateway   │ ───────────────────▶ │ Entra │
│ (Inspector,  │ ◀──────────────────────│ (OAuth issuer @ │ ◀─────────────────── │  ID   │
│  Claude, …)  │   2. issuer metadata   │ /oauth-issuer)  │  4. authorization    │       │
└──────────────┘     pointing at GW     └─────────────────┘     code → token     └───────┘
                                                │
                                                │  5. validate token, forward
                                                ▼
                                         ┌────────────────┐
                                         │   MCP server   │
                                         │ (test target)  │
                                         └────────────────┘
```

Three things make this work:

1. **Issuer metadata is served by the gateway** (`/.well-known/oauth-authorization-server/...`), so `registration_endpoint` points at the gateway. Entra publishes no such endpoint, so without this the client has nowhere to register.
2. **The gateway implements `/oauth-issuer/register`** and returns the pre-registered Entra client_id from the issuer config's `client_config.clients`.
3. **The gateway brokers the authorization code flow** to Entra using the issuer config's `downstream_server`. The browser still opens to the Microsoft sign-in page; the resulting JWT is what reaches the MCP backend.

---

## How Entra Deviates From the Other Eager-OAuth Labs

Read this before adapting the Okta, Auth0 or Keycloak lab. Entra behaves differently in five
places, and four of them produce a `401` or `404` that looks like a gateway misconfiguration.

| # | Behavior | Okta / Auth0 / Keycloak | Entra |
|---|---|---|---|
| 1 | Dynamic Client Registration | Supported (RFC 7591). Eager OAuth is a convenience that avoids admin-UI churn | **Not supported. No `registration_endpoint` is published at all.** Eager OAuth is the only way an MCP client can onboard |
| 2 | Issuer (`iss`) | One value per tenant | **Two**, chosen by the app manifest's `accessTokenAcceptedVersion`. v1 (the default) emits `https://sts.windows.net/<tenant>/`; v2 emits `https://login.microsoftonline.com/<tenant>/v2.0` |
| 3 | Where `aud` comes from | An authz-server "Audience" setting (Okta) or a tenant default-audience setting (Auth0) | **The resource that owns the requested scope.** Request only `openid profile email` and you get a Microsoft Graph token, not yours |
| 4 | JWKS path | Fixed well-known path (`/.well-known/jwks.json`, `/oauth2/<id>/v1/keys`) | **Tenant-scoped**: `<tenant-id>/discovery/v2.0/keys` |
| 5 | Scope combination | Scopes from multiple resources can be requested together | **One resource per request.** Reserved OIDC scopes may accompany a single resource's scope; two custom APIs in one request is an Entra error |

**1. No DCR endpoint.** This is the reason the lab exists. With the other three providers you could
skip eager OAuth and let clients register themselves. Against Entra that path does not exist, so
without the gateway acting as the Authorization Server an MCP client has nowhere to register. `agentgateway.dev/issuer-proxy` is required for the same reason: proxy Entra's own metadata and
the client receives a document with no `registration_endpoint` in it.

**2. Two issuers, and the default is the surprising one.** A new app registration defaults to v1
even though this lab calls the v2.0 `/authorize` and `/token` endpoints. So the endpoints say v2.0
and the token says `sts.windows.net`. The MCP authentication policy compares `iss` literally, so
guessing wrong is a `401` on a token that is otherwise valid. Nothing in the Okta or
Auth0 labs prepares you for this.

**3. Audience follows the scope, not a setting.** Okta lets you set the audience on the
authorization server and Auth0 lets you set a tenant default. Entra has neither. The `aud` claim is
determined by which API the requested scope belongs to, which is why `${ENTRA_API_SCOPE}` must
appear in `downstream_server.scopes`. Omit it and every token comes back audienced to Microsoft
Graph (`00000003-0000-0000-c000-000000000000`).

**4. Tenant-scoped JWKS.** The `jwksPath` carries the tenant GUID. Combined with the gateway's
no-leading-slash rule, the correct value is `${ENTRA_TENANT_ID}/discovery/v2.0/keys`. A leading
slash yields a double slash, Entra 404s, the policy goes `PartiallyValid`, and `/mcp` stops
enforcing auth rather than failing closed.

**5. One resource per authorization request.** Relevant if you later extend this lab to broker a
second downstream API. You cannot ask for scopes on two custom APIs in a single `/authorize` call;
you need a separate token acquisition per resource.

---

## Custom Gateway Features Covered

- **OAuth 2.0 Authorization Server**: agentgateway acts as the AS at `/oauth-issuer/...`. MCP clients see the gateway as their OAuth provider, not Entra.
- **Pre-registered "fake DCR"**: `/oauth-issuer/register` returns the Entra `client_id`/`client_secret` pair you provide. MCP clients believe they did Dynamic Client Registration against a provider that has no such endpoint.
- **Authorization code flow brokering**: the gateway proxies the authorization code flow downstream to Entra (`authorize`, callback handling, `token` exchange).
- **JWT validation**: Entra-issued JWTs are validated at the MCP backend against Entra's JWKS (`/<tenant>/discovery/v2.0/keys`).
- **Frontend TLS termination**: the existing `agentgateway-proxy` Gateway gains an HTTPS listener on port 443 alongside lab 001's HTTP listener on 8080.

---

## Step 1 — Set Environment Variables and DNS

Set these values in your shell so child processes (`kubectl`, `helm`) inherit them. If you keep the Entra values in your shell rc, source the rc and run the block below as-is; otherwise replace each `$VAR` with the example value shown in the comment.

```bash
export ENTRA_TENANT_ID=$ENTRA_TENANT_ID           # e.g. 5e7d8166-7876-4755-a1a4-b476d4a344f6
export ENTRA_CLIENT_ID=$ENTRA_CLIENT_ID           # e.g. faf0d33e-a042-4add-b1e1-8a58a07493dc
export ENTRA_CLIENT_SECRET=$ENTRA_CLIENT_SECRET   # secret VALUE, not the secret ID
export ENTRA_AUTHORITY="https://login.microsoftonline.com/${ENTRA_TENANT_ID}"
export ENTRA_AUDIENCE="api://${ENTRA_CLIENT_ID}"
export ENTRA_API_SCOPE="api://${ENTRA_CLIENT_ID}/agentgateway"
export ENTRA_GATEWAY_HOST=mcp-entra.try-solo.io

# Issuer: pick ONE, matching the app's accessTokenAcceptedVersion (see Pre-requisites)
export ENTRA_ISSUER="https://sts.windows.net/${ENTRA_TENANT_ID}/"            # v1 (default), trailing slash
# export ENTRA_ISSUER="${ENTRA_AUTHORITY}/v2.0"                              # v2, no trailing slash

# Controller version (auto-detected from the Lab 001 helm release) + license
export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY   # from Lab 001
```

Notes on these values:

- `ENTRA_CLIENT_SECRET` is the secret **Value** column in Certificates & secrets, not the **Secret ID**. Entra shows the value once, at creation.
- `ENTRA_AUDIENCE` must match the `aud` claim on issued tokens. Entra sets `aud` from the API the requested scope belongs to, which is why `ENTRA_API_SCOPE` has to be in the authorization request (Step 5).
- The authorize and token endpoints stay on the **v2.0** paths (`/oauth2/v2.0/authorize`, `/oauth2/v2.0/token`) regardless of which issuer you chose. Only the `iss` claim changes.

### Map the gateway hostname to the LoadBalancer IP

Find the LoadBalancer IP/hostname assigned to `agentgateway-proxy` from Lab 001:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "$GATEWAY_IP"
```

Add an `/etc/hosts` entry so both your terminal and your browser resolve `mcp-entra.try-solo.io` to the gateway:

```bash
echo "$GATEWAY_IP $ENTRA_GATEWAY_HOST" | sudo tee -a /etc/hosts
```

---

## Step 2 — Create a Self-Signed TLS Cert and Add an HTTPS Listener

Identical to the Okta and Auth0 labs; nothing here is IdP-specific. Follow
[`mcp-eager-auth-okta.md` Step 2](./mcp-eager-auth-okta.md#step-2--create-a-self-signed-tls-cert-and-add-an-https-listener),
substituting `$ENTRA_GATEWAY_HOST` for `$OKTA_GATEWAY_HOST` throughout.

---

## Step 3 — Deploy Postgres for OAuth State

Identical to the Okta and Auth0 labs. Follow
[`mcp-eager-auth-okta.md` Step 3](./mcp-eager-auth-okta.md#step-3--deploy-postgres-for-oauth-state).

---

## Step 4 — Add STS Env Vars to the Gateway Config

Identical to the Okta and Auth0 labs. Follow
[`mcp-eager-auth-okta.md` Step 4](./mcp-eager-auth-okta.md#step-4--add-sts-env-vars-to-the-gateway-config).

---

## Step 5 — Helm Upgrade with Eager-OAuth Values

Re-run `helm upgrade` to enable the eager-OAuth feature in the controller, point it at Postgres + Entra JWKS, and inject the OAuth issuer config.

```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
  -f -<<EOF

tokenExchange:
  enabled: true
  issuer: "enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777"
  tokenExpiration: 24h
  subjectValidator:
    validatorType: remote
    remoteConfig:
      url: "${ENTRA_AUTHORITY}/discovery/v2.0/keys"
  apiValidator:
    validatorType: remote
    remoteConfig:
      url: "${ENTRA_AUTHORITY}/discovery/v2.0/keys"
  actorValidator:
    validatorType: k8s
  database:
    type: postgres
    postgres:
      url: postgres://myuser:mypassword@postgres.postgres:5432/mydb

controller:
  extraEnv:
    # KGW_OAUTH_ISSUER_CONFIG is the required env var name the controller reads
    KGW_OAUTH_ISSUER_CONFIG: |
      {
        "gateway_config": {
          "base_url": "https://${ENTRA_GATEWAY_HOST}/oauth-issuer"
        },
        "client_config": {
          "clients": {
            "${ENTRA_CLIENT_ID}": "${ENTRA_CLIENT_SECRET}"
          }
        },
        "downstream_server": {
          "name": "entra",
          "client_id": "${ENTRA_CLIENT_ID}",
          "client_secret": "${ENTRA_CLIENT_SECRET}",
          "authorize_url": "${ENTRA_AUTHORITY}/oauth2/v2.0/authorize",
          "token_url": "${ENTRA_AUTHORITY}/oauth2/v2.0/token",
          "redirect_uri": "https://${ENTRA_GATEWAY_HOST}/oauth-issuer/callback/downstream",
          "scopes": ["openid", "profile", "email", "${ENTRA_API_SCOPE}"]
        }
      }
EOF
```

What each piece does:

| Setting | Purpose |
|---|---|
| `tokenExchange.enabled: true` | Turns the eager-OAuth feature on at the controller level (and starts the controller's port-7777 server that hosts both the AS endpoints and the STS) |
| `tokenExchange.subjectValidator` / `apiValidator` / `actorValidator` | All three required at boot: the controller refuses to start without them, even though only the eager-OAuth issuer (not RFC 8693 token exchange) is being used here. Crash signature if missing: `error creating actor validator: unsupported validator type:` |
| `tokenExchange.database.postgres.url` | Postgres connection string from Step 3; omit for SQLite in-memory |
| `gateway_config.base_url` | Public URL clients use to reach the gateway's AS endpoints (must include `/oauth-issuer`) |
| `client_config.clients` | Pre-registered `client_id`/`client_secret` table; `/oauth-issuer/register` returns one of these |
| `downstream_server` | Credentials and URLs for the gateway to talk to Entra during the authorization code flow; `redirect_uri` must match a Web platform redirect URI on the app registration |

Wait for the controller and proxy pods to restart cleanly:

```bash
kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
kubectl rollout status -n agentgateway-system deployment/agentgateway-proxy --timeout=180s
```

> **⚠ Why `${ENTRA_API_SCOPE}` is in `scopes`.** Entra decides the `aud` claim from the API that the
> requested scope belongs to. Ask for only `openid profile email` and you get a token audienced to
> Microsoft Graph, which the MCP authentication policy in Step 8 will reject. Including
> `api://<client-id>/agentgateway` is what produces `aud: api://<client-id>`. The reserved OIDC
> scopes can be combined with a single resource's scope in one request; asking for scopes from two
> different resources in one request is an Entra error, not a gateway one. This is deviations 3 and
> 5 in [How Entra Deviates](#how-entra-deviates-from-the-other-eager-oauth-labs).

> **⚠ Entra's authorize URL carries no query string**, so the eager-OAuth issuer's URL builder
> appends `?client_id=...` cleanly. The double-`?` bug in
> [issue #7382](https://github.com/solo-io/agentgateway-enterprise/issues/7382) only affects
> providers whose `authorize_url` already has query parameters.

---

## Step 6 — Apply the OAuth Issuer Route

Expose the gateway's eager-OAuth endpoints (`/oauth-issuer/register`, `/oauth-issuer/authorize`, `/oauth-issuer/token`, `/oauth-issuer/callback/...`) by routing the `/oauth-issuer` path prefix to the `enterprise-agentgateway` controller service on port 7777. The route attaches to the `https` listener on `agentgateway-proxy` via `sectionName`.

```bash
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: oauth-issuer
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
      sectionName: https
  hostnames:
    - mcp-entra.try-solo.io
  rules:
    - backendRefs:
        - name: enterprise-agentgateway
          namespace: agentgateway-system
          port: 7777
      matches:
        - path:
            type: PathPrefix
            value: /oauth-issuer
EOF
```

Both the route and the backend service live in `agentgateway-system`, so no `ReferenceGrant` is required.

Verify the route attached cleanly:

```bash
kubectl get httproute -n agentgateway-system oauth-issuer \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}'
```

Expected Output:

```
True
```

---

## Step 7 — Deploy the MCP Server, Backend, Route, JWKS Backend, and Elicitation Secret

This step deploys five resources in `agentgateway-system`:

| Resource | Kind | Description |
|---|---|---|
| `mcp-server` | Deployment + Service | `@modelcontextprotocol/server-everything` reference server in Streamable HTTP mode (run via `npx` on `node:20-alpine`). Streamable HTTP is per-request stateless, which lets Lab 001's `replicas: 2` proxy stay unchanged. |
| `mcp-backend` | EnterpriseAgentgatewayBackend | Wraps the MCP server as an MCP target |
| `mcp-route` | HTTPRoute | Exposes `/mcp` plus the two `.well-known/oauth-*-resource/mcp` discovery paths on the `https` listener |
| `entra-jwks` | EnterpriseAgentgatewayBackend | Static backend pointing at `login.microsoftonline.com` for JWKS lookups during request validation |
| `elicitation-secret` | Secret | **Required** by the eager-OAuth issuer at the start of an auth flow. The controller looks for this exact name in its own namespace and 500s with `secret not found: agentgateway-system/elicitation-secret` on `/oauth-issuer/authorize` if it's missing. |

Apply everything except the MCP server, which is identical to the Okta lab:

```bash
kubectl apply -f - <<EOF
---
apiVersion: v1
kind: Secret
type: Opaque
metadata:
  name: elicitation-secret
  namespace: agentgateway-system
stringData:
  app_id: "entra"
  authorize_url: "${ENTRA_AUTHORITY}/oauth2/v2.0/authorize"
  access_token_url: "${ENTRA_AUTHORITY}/oauth2/v2.0/token"
  client_id: "${ENTRA_CLIENT_ID}"
  client_secret: "${ENTRA_CLIENT_SECRET}"
  mcp_resource: "/mcp"
  scopes: "openid profile email ${ENTRA_API_SCOPE}"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: entra-jwks
  namespace: agentgateway-system
spec:
  static:
    host: login.microsoftonline.com
    port: 443
  policies:
    tls: {}
EOF
```

For the `mcp-server` Deployment, `mcp-server` Service, `mcp-backend` and `mcp-route`, apply the
same manifests as
[`mcp-eager-auth-okta.md` Step 7](./mcp-eager-auth-okta.md#step-7--deploy-the-mcp-server-backend-route-jwks-backend-and-elicitation-secret),
changing only the two `hostnames:` entries on `mcp-route` from `mcp-okta.try-solo.io` to
`mcp-entra.try-solo.io`.

Wait for the test server to come up:

```bash
kubectl rollout status -n agentgateway-system deployment/mcp-server --timeout=120s
```

Expected Output:

```
deployment "mcp-server" successfully rolled out
```

---

## Step 8 — Apply the MCP Authentication Policy

The policy ties everything together:

| Field | Purpose |
|---|---|
| `issuer` | Entra is the JWT issuer (`${ENTRA_ISSUER}`). Trailing slash for v1 (`https://sts.windows.net/<tenant>/`), none for v2 (`https://login.microsoftonline.com/<tenant>/v2.0`). Compared literally. |
| `jwks` | Points at the `entra-jwks` backend created in Step 7. **`jwksPath` must be written without a leading slash** (`${ENTRA_TENANT_ID}/discovery/v2.0/keys`): the controller appends `/` between the backend URL and `jwksPath`, so a leading slash produces `https://login.microsoftonline.com//<tenant>/...`, which Entra returns 404 for. The controller log signature is `failed resolving jwks ... 404` and the policy goes `PartiallyValid`; `/mcp` then bypasses auth entirely. |
| `audiences` | The Application ID URI, `api://${ENTRA_CLIENT_ID}` |
| `resourceMetadata.agentgateway.dev/issuer-proxy` | Tells the gateway to serve its own AS metadata (from the in-cluster eager-OAuth issuer at `:7777/oauth-issuer`) when an MCP client fetches `.well-known/oauth-authorization-server/mcp`. Without this, the gateway would proxy Entra's metadata directly, and Entra's metadata advertises no `registration_endpoint`. |
| `resourceMetadata.scopesSupported` | The custom API scope exposed on the Entra app; clients read this from the protected-resource document |
| `resourceMetadata.authorizationServers` / `resource` | What shows up in the protected-resource discovery document for clients |

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-entra-eager
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: mcp-backend
  backend:
    mcp:
      authentication:
        mode: Strict
        issuer: ${ENTRA_ISSUER}
        audiences:
          - ${ENTRA_AUDIENCE}
        jwks:
          backendRef:
            name: entra-jwks
            kind: EnterpriseAgentgatewayBackend
            group: enterpriseagentgateway.solo.io
          cacheDuration: 5m
          jwksPath: ${ENTRA_TENANT_ID}/discovery/v2.0/keys
        resourceMetadata:
          agentgateway.dev/issuer-proxy: http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/oauth-issuer
          authorizationServers:
            - https://${ENTRA_GATEWAY_HOST}/mcp
          resource: https://${ENTRA_GATEWAY_HOST}/mcp
          scopesSupported:
            - ${ENTRA_API_SCOPE}
EOF
```

---

## Step 9 — Test with MCP Inspector

Identical to the Okta lab apart from the hostname. Follow
[`mcp-eager-auth-okta.md` Step 9](./mcp-eager-auth-okta.md#step-9--test-with-mcp-inspector),
connecting to `https://mcp-entra.try-solo.io/mcp`.

The browser will redirect to the Microsoft sign-in page rather than Okta's. If your workstation
already holds an active Entra session for this tenant, Entra may complete the flow without
prompting, which looks like the redirect was skipped.

### What proves what

| Observation | What it proves |
|---|---|
| Inspector completed registration without an Entra admin creating an app | The gateway answered `/oauth-issuer/register`. Entra has no DCR endpoint, so this could not have come from Entra |
| The discovery document's `registration_endpoint` points at `mcp-entra.try-solo.io` | `issuer-proxy` is serving the gateway's own AS metadata |
| The browser opened `login.microsoftonline.com` | The gateway brokered the code flow downstream to Entra rather than issuing its own identity |
| `tools/list` returns the `everything` server's tools | The Entra JWT validated against Entra JWKS at the MCP backend |

---

## Step 10 — Test with Claude Code

Identical to the Okta lab apart from the hostname. Follow
[`mcp-eager-auth-okta.md` Step 10](./mcp-eager-auth-okta.md#step-10--test-with-claude-code),
registering `https://mcp-entra.try-solo.io/mcp`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401` after a successful login, token looks valid | `iss` mismatch between the token and the policy. v1 apps emit `https://sts.windows.net/<tenant>/`; v2 apps emit `https://login.microsoftonline.com/<tenant>/v2.0` | Decode the token at [jwt.io](https://jwt.io), set `ENTRA_ISSUER` to match, re-apply Step 8. Or set `accessTokenAcceptedVersion: 2` in the app manifest and use the v2 issuer |
| `401`, token `aud` is `00000003-0000-0000-c000-000000000000` | That is Microsoft Graph. The authorization request asked only for reserved OIDC scopes | Add `${ENTRA_API_SCOPE}` to `downstream_server.scopes` in Step 5 |
| `GET /mcp` returns **406** (not 401), well-known returns 404 | MCP auth policy is `PartiallyValid`: the controller could not fetch JWKS. Almost always a **leading slash** on `jwksPath` | Use `jwksPath: ${ENTRA_TENANT_ID}/discovery/v2.0/keys` with no leading slash. Check `kubectl logs -n agentgateway-system deploy/enterprise-agentgateway \| grep -i jwks` |
| `AADSTS50011: redirect URI does not match` after login | Only one of the two callbacks is registered on the app | Register both `/oauth-issuer/callback/downstream` and `/oauth-issuer/callback/upstream` under Authentication → Web |
| `AADSTS7000215: Invalid client secret provided` | `ENTRA_CLIENT_SECRET` holds the secret **ID** rather than the secret **Value** | Copy the Value column in Certificates & secrets. If it was never captured, create a new secret; Entra cannot show an existing one |
| `AADSTS650053: The application asked for scope ... that doesn't exist` | The exposed API scope name does not match `ENTRA_API_SCOPE` | Check Expose an API → Scopes and copy the full `api://<client-id>/<scope>` string |
| Controller crashloops with `error creating actor validator: unsupported validator type:` | One of the three `tokenExchange` validators is missing | All three of `subjectValidator`, `apiValidator`, `actorValidator` are required at boot |
| `secret not found: agentgateway-system/elicitation-secret` on `/oauth-issuer/authorize` | The elicitation Secret was not applied | Apply it from Step 7. The name is fixed; the controller looks for this exact name in its own namespace |

Useful checks:

```bash
# Confirm the discovery endpoints respond from the public URL
curl -sk https://$ENTRA_GATEWAY_HOST/.well-known/oauth-protected-resource/mcp | jq
curl -sk https://$ENTRA_GATEWAY_HOST/.well-known/oauth-authorization-server/mcp | jq

# Verify registration_endpoint points at the gateway, not Entra
curl -sk https://$ENTRA_GATEWAY_HOST/.well-known/oauth-authorization-server/mcp | jq -r .registration_endpoint

# Entra's own metadata for comparison; note it has NO registration_endpoint
curl -s "$ENTRA_AUTHORITY/v2.0/.well-known/openid-configuration" | jq 'has("registration_endpoint")'

# Tail gateway logs during an Inspector connection attempt
kubectl logs -n agentgateway-system deploy/agentgateway-proxy -f
```

---

## Cleanup

Identical to the Okta lab apart from resource names. Follow
[`mcp-eager-auth-okta.md` Cleanup](./mcp-eager-auth-okta.md#cleanup), substituting
`mcp-entra-eager` for `mcp-okta-eager`, `entra-jwks` for `okta-jwks`, and
`$ENTRA_GATEWAY_HOST` for `$OKTA_GATEWAY_HOST`.
