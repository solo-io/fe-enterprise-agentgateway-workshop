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
| `ENTRA_AUDIENCE` | The Application ID URI, normally `api://${ENTRA_CLIENT_ID}`; the policy also accepts the bare client ID because Entra can use either `aud` format |
| `ENTRA_API_SCOPE` | The exposed scope, e.g. `api://${ENTRA_CLIENT_ID}/agentgateway` |
| `ENTRA_ISSUER` | Depends on the app's access token version setting. See the warning below before you set it |
| `ENTRA_GATEWAY_HOST` | Public hostname for the gateway (no scheme); this lab uses `mcp-entra.try-solo.io` |

> **⚠ Pick your issuer before anything else.** Entra mints two different `iss` values depending on
> the app manifest's `api.requestedAccessTokenVersion` (called `accessTokenAcceptedVersion` in the
> older Azure AD Graph manifest), and the MCP authentication policy compares the
> value literally.
>
> | Access token version | `iss` on issued tokens |
> |---|---|
> | `null` or `1` (the default) | `https://sts.windows.net/${ENTRA_TENANT_ID}/` **with** a trailing slash |
> | `2` | `https://login.microsoftonline.com/${ENTRA_TENANT_ID}/v2.0` with **no** trailing slash |
>
> In the current Entra manifest, set `"api": { "requestedAccessTokenVersion": 2 }` to select v2.
> A new app registration defaults to v1 even though you call the v2.0 `/authorize` and `/token`
> endpoints, which surprises most people. This is deviation 2 in
> [How Entra Deviates](#how-entra-deviates-from-the-other-eager-oauth-labs). Decide which you want, set the manifest to match, and
> use the corresponding `ENTRA_ISSUER`. If a valid-looking token still returns `401`, inspect its
> `iss` claim and compare it against what you configured.

### Expose an API scope

To get an access token for your API, the authorization request must ask for a scope exposed by that API. The resulting `aud` can be the Application ID URI or the bare client ID.

1. **Expose an API → Set the Application ID URI.** Accept the default `api://${ENTRA_CLIENT_ID}`.
2. **Add a scope**, e.g. `agentgateway`. For an easy lab sign-in, set **Who can consent?** to **Admins and users**. If you choose **Admins only**, grant admin consent to the app before testing with a non-admin user.
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
- Claude Code, if you want to complete Step 10
- `jq` for inspecting JSON responses
- A way to resolve `mcp-entra.try-solo.io` from your workstation to the gateway LoadBalancer: either a real DNS record (production-style clusters) or a local `/etc/hosts` entry (KinD/minikube/local dev clusters; requires sudo)

---

## Lab Objectives

- Stand up the eager-OAuth feature so the gateway acts as the OAuth Authorization Server visible to MCP clients
- Give MCP clients a single pre-registered Entra `client_id` / `client_secret` through this eager-OAuth issuer, which bridges Entra's lack of a Dynamic Client Registration endpoint
- Broker the Entra authorization code flow through the gateway (`/oauth-issuer/...`)
- Validate Entra-issued JWTs at the MCP backend against Entra JWKS
- Terminate TLS on `agentgateway-proxy` with a self-signed cert for `mcp-entra.try-solo.io`
- Test end-to-end with MCP Inspector against an `mcp-server-everything` test server

---

## Background

Why eager OAuth with Entra?

With Okta and Auth0, eager OAuth is a convenience: both support Dynamic Client Registration (RFC 7591), and the gateway spares you an admin-UI entry per MCP client. Entra does not publish an RFC 7591 registration endpoint, so MCP clients need a gateway bridge or statically configured clients. This lab demonstrates the controller-hosted eager-OAuth issuer. Solo also documents a [native Entra provider](https://docs.solo.io/agentgateway/latest/mcp/auth/entra/) in the gateway proxy; it is a different configuration path. For background on Entra's registration behavior, see:

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
| 1 | Dynamic Client Registration | Supported (RFC 7591). Eager OAuth is a convenience that avoids admin-UI churn | **Not supported. No `registration_endpoint` is published at all.** A gateway bridge, such as this eager-OAuth issuer or the native Entra provider, is needed for clients that expect DCR |
| 2 | Issuer (`iss`) | One value per tenant | **Two**, chosen by the app manifest's access token version setting. v1 (the default) emits `https://sts.windows.net/<tenant>/`; v2 emits `https://login.microsoftonline.com/<tenant>/v2.0` |
| 3 | Where `aud` comes from | An authz-server "Audience" setting (Okta) or a tenant default-audience setting (Auth0) | **The resource that owns the requested scope.** Requesting only `openid profile email` can produce a Microsoft Graph token. Tokens for your API can use its Application ID URI or bare client ID as `aud` |
| 4 | JWKS path | Fixed well-known path (`/.well-known/jwks.json`, `/oauth2/<id>/v1/keys`) | **Tenant-scoped**: `<tenant-id>/discovery/v2.0/keys` |
| 5 | Scope combination | Scopes from multiple resources can be requested together | **One resource per request.** Reserved OIDC scopes may accompany a single resource's scope; two custom APIs in one request is an Entra error |
| 6 | RFC 8707 `resource` parameter | Accepted, and MCP clients send it to name the target resource | **Rejected.** A client that sends `resource` on `/authorize` gets an Entra error unless something strips it first |

**1. No DCR endpoint.** This is the reason the lab exists. With the other three providers you could
skip eager OAuth and let clients register themselves. Against Entra that path does not exist, so
this lab has the gateway act as the Authorization Server. `agentgateway.dev/issuer-proxy` is required for this eager-OAuth path: proxy Entra's own metadata and
the client receives a document with no `registration_endpoint` in it.

**2. Two issuers, and the default is the surprising one.** A new app registration defaults to v1
even though this lab calls the v2.0 `/authorize` and `/token` endpoints. So the endpoints say v2.0
and the token says `sts.windows.net`. The MCP authentication policy compares `iss` literally, so
guessing wrong is a `401` on a token that is otherwise valid. Nothing in the Okta or
Auth0 labs prepares you for this.

**3. Audience follows the scope, not a setting.** Okta lets you set the audience on the
authorization server and Auth0 lets you set a tenant default. Entra has neither. The requested
scope selects the API that receives the token, which is why `${ENTRA_API_SCOPE}` must appear in
`downstream_server.scopes`. Without it, Entra can issue a token for Microsoft Graph
(`00000003-0000-0000-c000-000000000000`). Depending on the access token version, the `aud`
claim for your API can be its Application ID URI or the bare client ID. Step 8 accepts both.

**4. Tenant-scoped JWKS.** The `jwksPath` carries the tenant GUID. Combined with the gateway's
no-leading-slash rule, the correct value is `${ENTRA_TENANT_ID}/discovery/v2.0/keys`. A leading
slash yields a double slash, Entra 404s, the policy goes `PartiallyValid`, and `/mcp` stops
enforcing auth rather than failing closed.

**5. One resource per authorization request.** Relevant if you later extend this lab to broker a
second downstream API. You cannot ask for scopes on two custom APIs in a single `/authorize` call;
you need a separate token acquisition per resource.

**6. Entra rejects the RFC 8707 `resource` parameter.** MCP clients send it to name the resource
they want a token for. Entra returns an error. The eager-OAuth issuer sidesteps this because it
builds the downstream `/authorize` URL itself from `downstream_server` rather than forwarding the
client's parameters, so the client's `resource` never reaches Entra. The native provider below
strips it explicitly.

---

## Two Ways to Do This, and When to Pick Each

agentgateway offers a second, lighter path for Entra: a **native Entra provider**
(`provider: Entra`), documented at
[Set up Microsoft Entra ID](https://docs.solo.io/agentgateway/kubernetes/latest/documentation/mcp/auth/entra/).
It solves the same problem this lab solves. Know both before you build either.

| | This lab (eager OAuth) | Native `provider: Entra` |
|---|---|---|
| Who answers DCR | The controller's OAuth issuer on `:7777` | The proxy short-circuits it with your pre-registered client ID |
| AS metadata | Gateway serves its own via `issuer-proxy` | Proxy serves RFC 8414 metadata derived from Entra's OIDC discovery |
| RFC 8707 `resource` | Never forwarded, because the issuer builds the downstream URL itself | Stripped explicitly before the request reaches Entra |
| Postgres | Required, for OAuth state | Not required |
| `tokenExchange` helm values | Required, all three validators, or the controller will not boot | Not required |
| `/oauth-issuer` HTTPRoute to `:7777` | Required | Not required |
| Entra app shape | Confidential client with a secret | Public client using PKCE |
| Client secret in cluster | Yes | No |

**Pick the native provider** when Entra is the only IdP in front of these MCP servers and you want
the smallest moving-parts count. No database, no controller-side OAuth server, no client secret
stored in the cluster.

**Pick eager OAuth**, which is what this lab builds, when you want one mechanism across several
IdPs (the Okta, Auth0 and Keycloak labs configure the same issuer), when you need the issuer's
pre-registered client table to hand different `client_id`s to different MCP clients, or when you
are heading toward the entitlement gating in
[MCP Pre-Issuance Entitlement Gating](./mcp-eager-auth-auth0-pre-issuance-authz.md), which hooks
the issuer's token endpoint.

Everything below builds the eager-OAuth path.

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

# Issuer: pick ONE, matching the app's requestedAccessTokenVersion (see Pre-requisites)
export ENTRA_ISSUER="https://sts.windows.net/${ENTRA_TENANT_ID}/"            # v1 (default), trailing slash
# export ENTRA_ISSUER="${ENTRA_AUTHORITY}/v2.0"                              # v2, no trailing slash

# Controller version (auto-detected from the Lab 001 helm release) + license
export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY   # from Lab 001
```

Notes on these values:

- `ENTRA_CLIENT_SECRET` is the secret **Value** column in Certificates & secrets, not the **Secret ID**. Entra shows the value once, at creation.
- `ENTRA_API_SCOPE` selects the API that the issued token targets. Entra can put either the Application ID URI or the bare client ID in `aud`, depending on the token version. Step 8 accepts both forms; after sign-in, inspect the actual `aud` claim if validation returns 401.
- The authorize and token endpoints stay on the **v2.0** paths (`/oauth2/v2.0/authorize`, `/oauth2/v2.0/token`) regardless of which access token version you chose. The `iss` and potentially the `aud` claim change.

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

Create a self-signed certificate for the Entra gateway hostname and add an HTTPS listener alongside Lab 001's HTTP listener. Run these commands in a lab working directory:

```bash
mkdir -p example_certs
openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 \
  -subj '/O=Solo.io/CN=try-solo.io' \
  -keyout example_certs/try-solo.io.key \
  -out    example_certs/try-solo.io.crt

openssl req -out example_certs/gateway.csr -newkey rsa:2048 -nodes \
  -keyout example_certs/gateway.key \
  -subj  "/CN=${ENTRA_GATEWAY_HOST}/O=Solo.io"

openssl x509 -req -sha256 -days 365 \
  -CA    example_certs/try-solo.io.crt \
  -CAkey example_certs/try-solo.io.key \
  -set_serial 0 \
  -in    example_certs/gateway.csr \
  -out   example_certs/gateway.crt \
  -extfile <(printf 'subjectAltName=DNS:%s' "$ENTRA_GATEWAY_HOST")

kubectl create secret tls -n agentgateway-system mcp-entra-tls \
  --key example_certs/gateway.key \
  --cert example_certs/gateway.crt \
  --dry-run=client -oyaml | kubectl apply -f -
```

Update the existing Gateway, preserving its HTTP listener:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
    - name: https
      port: 443
      protocol: HTTPS
      hostname: ${ENTRA_GATEWAY_HOST}
      tls:
        mode: Terminate
        certificateRefs:
          - name: mcp-entra-tls
            kind: Secret
      allowedRoutes:
        namespaces:
          from: All
EOF

kubectl get gateway -n agentgateway-system agentgateway-proxy \
  -o jsonpath='{range .status.listeners[*]}{.name}{"\t"}{.conditions[?(@.type=="Programmed")].status}{"\n"}{end}'
```

Both `http` and `https` should report `True`.

---

## Step 3 — Deploy Postgres for OAuth State

The eager-OAuth feature stores token-exchange / authorization-code state in a database. This lab uses Postgres (production-realistic). For quick iteration you can skip Postgres and use SQLite in-memory; see the callout below.

```bash
kubectl apply -f - <<'EOF'
---
apiVersion: v1
kind: Namespace
metadata:
  name: postgres
---
apiVersion: v1
kind: Secret
metadata:
  name: postgres-secret
  namespace: postgres
type: Opaque
stringData:
  POSTGRES_DB: mydb
  POSTGRES_USER: myuser
  POSTGRES_PASSWORD: mypassword
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
  namespace: postgres
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:18
          envFrom:
            - secretRef:
                name: postgres-secret
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: postgres-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: postgres
spec:
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432
EOF
```

Wait for the pod to become ready:

```bash
kubectl rollout status -n postgres deployment/postgres --timeout=120s
```

Expected Output:

```
deployment "postgres" successfully rolled out
```

> **Skip Postgres? Use SQLite in-memory.** Omit Step 3 entirely, then in Step 5 omit the `database:` block from the values. The gateway will use SQLite in-memory. State is lost on pod restart: fine for a lab, not for production.

---

## Step 4 — Add STS Env Vars to the Gateway Config

The eager-OAuth flow needs two env vars on the agentgateway proxy pod so it knows where the in-cluster STS endpoint lives. Patch the existing `agentgateway-config` `EnterpriseAgentgatewayParameters` from Lab 001. Do not recreate it; the patch preserves all other settings.

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type=merge \
  -p='
spec:
  env:
    - name: STS_URI
      value: http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/elicitations/oauth2/token
    - name: STS_AUTH_TOKEN
      value: /var/run/secrets/xds-tokens/xds-token
'
```

Verify the patch landed:

```bash
kubectl get enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system -o jsonpath='{.spec.env}' | jq .
```

Expected Output:

```json
[
  {
    "name": "STS_URI",
    "value": "http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/elicitations/oauth2/token"
  },
  {
    "name": "STS_AUTH_TOKEN",
    "value": "/var/run/secrets/xds-tokens/xds-token"
  }
]
```

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
> requested scope belongs to. Ask for only `openid profile email` and you can get a token for
> Microsoft Graph, which the MCP authentication policy in Step 8 will reject. Including
> `api://<client-id>/agentgateway` requests a token for your API. Its `aud` can be the Application
> ID URI or the bare client ID, so Step 8 accepts both. The reserved OIDC
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

First apply the Entra-specific Secret and JWKS backend:

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

Then apply only the four shared resources below. The Okta lab's full Step 7 manifest includes an
Okta `elicitation-secret`; applying that whole block here would replace the Entra secret above.

```bash
kubectl apply -f - <<EOF
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-server
  namespace: agentgateway-system
spec:
  selector:
    matchLabels:
      app: mcp-server
  template:
    metadata:
      labels:
        app: mcp-server
    spec:
      containers:
        - name: mcp-server
          image: node:20-alpine
          command:
            - sh
            - -c
            - |
              export NODE_OPTIONS="--max-old-space-size=10240 --max-semi-space-size=64"
              npx -y @modelcontextprotocol/server-everything streamableHttp
          ports:
            - name: mcp-http
              containerPort: 3001
          env:
            - name: PORT
              value: "3001"
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-server
  namespace: agentgateway-system
spec:
  selector:
    app: mcp-server
  ports:
    - port: 80
      targetPort: 3001
      appProtocol: agentgateway.dev/mcp
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
      - name: mcp-target
        static:
          host: mcp-server.agentgateway-system.svc.cluster.local
          port: 80
          protocol: StreamableHTTP
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-route
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
      sectionName: https
  hostnames:
    - ${ENTRA_GATEWAY_HOST}
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp
      backendRefs:
        - name: mcp-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
    - matches:
        - path:
            type: PathPrefix
            value: /.well-known/oauth-protected-resource/mcp
      filters:
        - type: CORS
          cors:
            allowOrigins:
              - "*"
            allowMethods: ["GET", "OPTIONS"]
            allowHeaders:
              - "Content-Type"
              - "Authorization"
              - "Accept"
              - "mcp-protocol-version"
            maxAge: 86400
      backendRefs:
        - name: mcp-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
    - matches:
        - path:
            type: PathPrefix
            value: /.well-known/oauth-authorization-server/mcp
      filters:
        - type: CORS
          cors:
            allowOrigins:
              - "*"
            allowMethods: ["GET", "OPTIONS"]
            allowHeaders:
              - "Content-Type"
              - "Authorization"
              - "Accept"
              - "mcp-protocol-version"
            maxAge: 86400
      backendRefs:
        - name: mcp-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
EOF
```

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
| `audiences` | Both Entra formats for this API: its Application ID URI, `api://${ENTRA_CLIENT_ID}`, and the bare `${ENTRA_CLIENT_ID}` |
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
          - ${ENTRA_CLIENT_ID}
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

Before opening an MCP client, verify that the policy is accepted and the unauthenticated endpoint
is protected. The request should return `401 Unauthorized` and a `WWW-Authenticate` header pointing
to the protected-resource metadata. If it does not, inspect policy status before continuing.

```bash
kubectl get enterpriseagentgatewaypolicy -n agentgateway-system mcp-entra-eager -o yaml

curl -ski -X POST "https://${ENTRA_GATEWAY_HOST}/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}},"id":1}'
```

Check both discovery documents. The protected resource should advertise the Entra API scope and
the gateway as its authorization server. The authorization-server document's
`registration_endpoint` should point to `/oauth-issuer/register` on this gateway; its authorization
and token endpoints should point to the gateway too.

```bash
curl -sk "https://${ENTRA_GATEWAY_HOST}/.well-known/oauth-protected-resource/mcp" | jq .
curl -sk "https://${ENTRA_GATEWAY_HOST}/.well-known/oauth-authorization-server/mcp" \
  | jq '{issuer, authorization_endpoint, token_endpoint, registration_endpoint, code_challenge_methods_supported}'
```

---

## Step 9 — Test with MCP Inspector

First visit `https://mcp-entra.try-solo.io/.well-known/oauth-protected-resource/mcp` in your
browser and accept the self-signed certificate warning. Then start Inspector with TLS verification
disabled for this process only:

```bash
NODE_TLS_REJECT_UNAUTHORIZED=0 npx @modelcontextprotocol/inspector
```

Open the URL printed by Inspector. Set **Transport type** to **Streamable HTTP**, set **Server URL**
to `https://mcp-entra.try-solo.io/mcp`, and click **Connect**. Complete the Microsoft sign-in and
any consent prompt. In **Tools → List Tools**, run `echo` with `{"message":"hi"}` and confirm it
returns a result without a 401.

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

Register the Entra endpoint and launch Claude Code with the self-signed certificate workaround
scoped to that process:

```bash
claude mcp add mcp-entra-gateway --transport http https://mcp-entra.try-solo.io/mcp
claude mcp list
NODE_TLS_REJECT_UNAUTHORIZED=0 claude
```

Trigger MCP tool discovery, finish the Microsoft sign-in in the browser, and ask Claude Code to use
`mcp-entra-gateway`'s `echo` tool. A successful tool result confirms the stored token works on a
subsequent MCP request. Remove the local entry during cleanup with
`claude mcp remove mcp-entra-gateway`. If your gateway uses a trusted certificate, launch Claude
Code normally without `NODE_TLS_REJECT_UNAUTHORIZED=0`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401` after a successful login, token looks valid | `iss` mismatch between the token and the policy. v1 apps emit `https://sts.windows.net/<tenant>/`; v2 apps emit `https://login.microsoftonline.com/<tenant>/v2.0` | Inspect the token's `iss`, set `ENTRA_ISSUER` to match, and re-apply Step 8. Or set `api.requestedAccessTokenVersion` to `2` in the current app manifest and use the v2 issuer |
| `401`, token `aud` is `00000003-0000-0000-c000-000000000000` | That is Microsoft Graph. The authorization request did not select your API | Add `${ENTRA_API_SCOPE}` to `downstream_server.scopes` in Step 5 |
| `401`, token `aud` is your app's bare client ID | The applied policy does not match that claim | Confirm Step 8's `audiences` list includes `${ENTRA_CLIENT_ID}` and re-apply the policy |
| `GET /mcp` returns **406** (not 401), well-known returns 404 | MCP auth policy is `PartiallyValid`: the controller could not fetch JWKS. Almost always a **leading slash** on `jwksPath` | Use `jwksPath: ${ENTRA_TENANT_ID}/discovery/v2.0/keys` with no leading slash. Check `kubectl logs -n agentgateway-system deploy/enterprise-agentgateway \| grep -i jwks` |
| `AADSTS50011: redirect URI does not match` after login | Only one of the two callbacks is registered on the app | Register both `/oauth-issuer/callback/downstream` and `/oauth-issuer/callback/upstream` under Authentication → Web |
| `AADSTS7000215: Invalid client secret provided` | `ENTRA_CLIENT_SECRET` holds the secret **ID** rather than the secret **Value** | Copy the Value column in Certificates & secrets. If it was never captured, create a new secret; Entra cannot show an existing one |
| `AADSTS650053: The application asked for scope ... that doesn't exist` | The exposed API scope name does not match `ENTRA_API_SCOPE` | Check Expose an API → Scopes and copy the full `api://<client-id>/<scope>` string |
| Consent prompt or consent-required error blocks a non-admin user | The exposed scope allows only admin consent and an admin has not granted it | Grant admin consent before the lab, or configure **Who can consent?** as **Admins and users** |
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

Return to the Lab 001 baseline. Remove the Entra resources, revert the proxy environment and
Gateway, then reset the controller's eager-OAuth Helm values before deleting Postgres. If you used
Claude Code in Step 10, remove its local MCP entry too.

```bash
claude mcp remove mcp-entra-gateway  # only if you completed Step 10

kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-entra-eager --ignore-not-found
kubectl delete httproute -n agentgateway-system mcp-route oauth-issuer --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-backend entra-jwks --ignore-not-found
kubectl delete deployment -n agentgateway-system mcp-server --ignore-not-found
kubectl delete service -n agentgateway-system mcp-server --ignore-not-found
kubectl delete secret -n agentgateway-system elicitation-secret mcp-entra-tls --ignore-not-found

kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type=json \
  -p='[{"op":"remove","path":"/spec/env"}]' || true

kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
EOF

export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY

kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
kubectl delete namespace postgres --ignore-not-found
```

If Helm reports a no-op, restart the controller before recreating Postgres for another run:

```bash
kubectl rollout restart -n agentgateway-system deployment/enterprise-agentgateway
kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
```

Remove the files created in Step 2 and the hosts entry added in Step 1:

```bash
rm -f example_certs/try-solo.io.key example_certs/try-solo.io.crt \
  example_certs/gateway.key example_certs/gateway.csr example_certs/gateway.crt
rmdir example_certs 2>/dev/null || true
sudo sed -i '' "/${ENTRA_GATEWAY_HOST}/d" /etc/hosts  # macOS; on Linux omit the empty '' argument
```
