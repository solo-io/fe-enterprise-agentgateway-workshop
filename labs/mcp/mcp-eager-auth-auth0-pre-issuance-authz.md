# MCP Pre-Issuance Entitlement Gating with Auth0 (Multiplexed Backend)

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

> ⚠ This lab requires enterprise-agentgateway **`v2026.5.x` or newer**. The `pre_issuance` field in `KGW_OAUTH_ISSUER_CONFIG` does not exist on earlier controllers, and `source.principal` is not populated on pre-issuance Checks before that version.

This lab uses the same gateway hostname (`mcp-auth0.glootest.com`) as [`mcp-eager-auth-auth0.md`](mcp-eager-auth-auth0.md). The two labs cannot run concurrently — clean up the other lab before starting this one. The Okta labs use `mcp-okta.glootest.com` and do not collide.

### Auth0 requirements

You need a registered application in Auth0 (Regular Web Application) with the **Authorization Code** grant type enabled, plus an Auth0 API ("audience") configured. Capture these values for Step 1:

| Variable | Description |
|---|---|
| `AUTH0_ISSUER` | Auth0 issuer URL with **trailing slash** (Auth0 emits `iss` with the trailing `/`) |
| `AUTH0_DOMAIN` | Host portion of the issuer (no scheme, no path) — used by the `auth0-jwks` backend |
| `AUTH0_CLIENT_ID` | Client ID of the Auth0 application |
| `AUTH0_CLIENT_SECRET` | Client secret of the Auth0 application |
| `AUTH0_AUDIENCE` | Auth0 API audience the JWT must carry |
| `AUTH0_GATEWAY_HOST` | Public hostname for the gateway (no scheme) — this lab uses `mcp-auth0.glootest.com` |

### Auth0 app callback URLs

Add **both** of these gateway callbacks to the Auth0 application's "Allowed Callback URLs":

```
https://mcp-auth0.glootest.com/oauth-issuer/callback/downstream
https://mcp-auth0.glootest.com/oauth-issuer/callback/upstream
```

The eager-OAuth issuer runs a "dual OAuth flow" and uses different callback paths depending on the client. PKCE-capable MCP clients (e.g., MCP Inspector) trigger `/callback/upstream`; non-PKCE flows trigger `/callback/downstream`. Registering only one yields an Auth0 `invalid_request: callback url not allowed` error after login.

### Two Auth0 users

This lab demonstrates both the allow and deny paths of the pre-issuance hook, so you need credentials for **two Auth0 users** in the same tenant. By default the lab allowlists `jdoe@solo.io` (Solo demo tenant) — log in as `jdoe@solo.io` for the allow path, and as any other tenant user (e.g. `alex.ly@solo.io`) for the deny path. To use your own tenant, see the "Using your own Auth0 tenant" sub-section in Step 7.

### Required tools

- `kubectl` and `helm`
- `openssl` (for the self-signed gateway cert)
- Node 18+ (for MCP Inspector in Steps 10–11)
- `jq` for inspecting JSON responses
- A way to resolve `mcp-auth0.glootest.com` from your workstation to the gateway LoadBalancer — either a real DNS record (production-style clusters) or a local `/etc/hosts` entry (KinD/minikube/local dev clusters; requires sudo)

---

## Lab Objectives

- Stand up the eager-OAuth feature so the gateway acts as the OAuth Authorization Server visible to MCP clients
- Broker the Auth0 authorization code flow through the gateway (`/oauth-issuer/...`)
- Terminate TLS on `agentgateway-proxy` with a self-signed cert for `mcp-auth0.glootest.com`
- **Multiplex two MCP upstreams (in-cluster `server-everything` + remote `search.solo.io`) behind one `EnterpriseAgentgatewayBackend`**
- **Gate OAuth token issuance with a pre-issuance ext_authz hook so only allowlisted Auth0 users receive a token — others are redirected to a configurable deny page**
- **Test both allow and deny paths end-to-end with MCP Inspector**

---

## Background

### Recap: eager-OAuth with Auth0

In the eager-OAuth pattern, agentgateway acts as the OAuth Authorization Server that MCP clients see, and Auth0 sits downstream of the gateway. MCP clients DCR against the gateway and get a single pre-registered Auth0 `client_id` / `client_secret` pair — no Auth0 dashboard churn, no Management API needed at runtime. See [`mcp-eager-auth-auth0.md`](mcp-eager-auth-auth0.md) for the full walk-through of why this matters and how the fake-DCR mechanism works.

```
┌──────────────┐   1. discovery + DCR   ┌─────────────────┐  3. authorize/token  ┌───────┐
│  MCP client  │ ──────────────────────▶│  agentgateway   │ ───────────────────▶ │ Auth0 │
│ (Inspector,  │ ◀──────────────────────│ (OAuth issuer @ │ ◀─────────────────── │       │
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

### New: pre-issuance entitlement gating

JWT claims alone often can't answer entitlement questions ("is *this* user allowed to use *this* MCP gateway right now?"). Many organizations keep that source of truth in a separate authorization system. The pre-issuance ext_authz hook lets the gateway consult that system **before** issuing its own token to the MCP client.

After Auth0 authenticates the user but **before** the agentgateway-issued token reaches the MCP client, the controller calls a gRPC ext_authz service. On `PERMISSION_DENIED` the user's browser is redirected to a configurable URL with `client_id` and `resource` appended as query params — your branded "no access" page can use them to render context-aware copy. The MCP client never receives a token.

```
┌──────────────┐   1. discovery + DCR   ┌─────────────────┐  3. authorize/token  ┌───────┐
│  MCP client  │ ──────────────────────▶│  agentgateway   │ ───────────────────▶ │ Auth0 │
│ (Inspector,  │ ◀──────────────────────│ (OAuth issuer @ │ ◀─────────────────── │       │
│  Claude, …)  │   2. issuer metadata   │ /oauth-issuer)  │  4. authorization    │       │
└──────────────┘     pointing at GW     └─────────────────┘     code → token     └───────┘
                                                │
                              ┌─────────────────┤  5. pre-issuance Check
                              ▼                 │     (source.principal = auth0|<sub>)
                       ┌────────────────┐       │     ALLOW → continue
                       │  grpc-ext-authz│       │     DENY  → 307 to denied_redirect
                       │  (principal    │       │
                       │   mode)        │       │
                       └────────────────┘       │
                                                │
                                                │  6. validate token, fan out to ALL
                                                ▼                          targets
                                ┌───────────────┴──────────────┐
                                ▼                              ▼
                       ┌────────────────┐            ┌──────────────────┐
                       │  in-cluster    │            │ search.solo.io   │
                       │ server-        │            │  (remote, TLS)   │
                       │ everything     │            │                  │
                       └────────────────┘            └──────────────────┘
                            target: everything            target: soloio-docs
```

The hook integrates with the existing `ably7/grpc-ext-authz` image in `AUTH_MODE=principal`. The same image is used in [`mcp-byo-grpc-ext-authz.md`](mcp-byo-grpc-ext-authz.md), but at a different integration point — that lab gates HTTP requests on the data plane (`x-ext-authz: allow` header); this lab gates **token issuance** on the control plane.

---

## Custom Gateway Features Covered

- **OAuth 2.0 Authorization Server**: agentgateway acts as the AS at `/oauth-issuer/...` (recap)
- **Multiplexed MCP backend**: one `EnterpriseAgentgatewayBackend` fronts two upstreams — an in-cluster `server-everything` and the remote `search.solo.io` — letting one OAuth-protected MCP endpoint expose tools from both
- **Pre-issuance ext_authz hook**: `KGW_OAUTH_ISSUER_CONFIG.pre_issuance` calls a gRPC service between Auth0 callback and gateway-issued token; allowlists by Auth0 `sub` via `source.principal`; on deny the browser is redirected to `denied_redirect`
- **Frontend TLS termination**: HTTPS listener on `agentgateway-proxy` for `mcp-auth0.glootest.com` (recap)

---

## Step 1 — Set Environment Variables and DNS

Set these values in your shell so child processes (`kubectl`, `helm`) inherit them. If you keep the Auth0 values in your shell rc, source the rc and run the block below as-is; otherwise replace each `$VAR` with the example value shown in the comment.

```bash
export AUTH0_ISSUER=$AUTH0_ISSUER                 # e.g. https://your-tenant.us.auth0.com/   (trailing slash REQUIRED)
export AUTH0_DOMAIN=$AUTH0_DOMAIN                 # e.g. your-tenant.us.auth0.com
export AUTH0_CLIENT_ID=$AUTH0_CLIENT_ID           # e.g. abc123XYZ...
export AUTH0_CLIENT_SECRET=$AUTH0_CLIENT_SECRET   # long random string from Auth0
export AUTH0_AUDIENCE=$AUTH0_AUDIENCE             # e.g. https://api.example.com  (your Auth0 API identifier)
export AUTH0_GATEWAY_HOST=$AUTH0_GATEWAY_HOST     # this lab uses mcp-auth0.glootest.com

# Controller version (auto-detected from the Lab 001 helm release) + license
export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY   # from Lab 001
```

Notes on these values:

- `AUTH0_ISSUER` **must** end with a trailing slash for Auth0. The `iss` claim Auth0 puts in JWTs includes the slash, and the MCP authentication policy compares the two literally.
- `AUTH0_DOMAIN` is just the hostname (no scheme, no path). It's used by the `auth0-jwks` static backend in Step 8.
- `AUTH0_AUDIENCE` is the API identifier configured in Auth0 → APIs. It must match the `aud` claim on the issued tokens.

### Map the gateway hostname to the LoadBalancer IP

Find the LoadBalancer IP/hostname assigned to `agentgateway-proxy` from Lab 001:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "$GATEWAY_IP"
```

Add an `/etc/hosts` entry so both your terminal and your browser resolve `mcp-auth0.glootest.com` to the gateway:

```bash
echo "$GATEWAY_IP $AUTH0_GATEWAY_HOST" | sudo tee -a /etc/hosts
```

---

## Step 2 — Create a Self-Signed TLS Cert and Add an HTTPS Listener

OAuth requires HTTPS for everything that is not `localhost`, since the browser will redirect to Auth0 and back. This step creates a self-signed cert for `mcp-auth0.glootest.com` and adds a port 443 HTTPS listener to the existing `agentgateway-proxy` Gateway alongside Lab 001's port 8080 HTTP listener.

Create a root certificate for the `glootest.com` domain and a leaf cert signed by that root:

```bash
mkdir -p example_certs
openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 \
  -subj '/O=Solo.io/CN=glootest.com' \
  -keyout example_certs/glootest.com.key \
  -out    example_certs/glootest.com.crt

openssl req -out example_certs/gateway.csr -newkey rsa:2048 -nodes \
  -keyout example_certs/gateway.key \
  -subj  "/CN=mcp-auth0.glootest.com/O=Solo.io"

openssl x509 -req -sha256 -days 365 \
  -CA    example_certs/glootest.com.crt \
  -CAkey example_certs/glootest.com.key \
  -set_serial 0 \
  -in    example_certs/gateway.csr \
  -out   example_certs/gateway.crt \
  -extfile <(printf "subjectAltName=DNS:mcp-auth0.glootest.com")
```

Store the leaf cert in a Kubernetes TLS secret:

```bash
kubectl create secret tls -n agentgateway-system mcp-auth0-tls \
  --key  example_certs/gateway.key \
  --cert example_certs/gateway.crt \
  --dry-run=client -oyaml | kubectl apply -f -
```

Update the `agentgateway-proxy` Gateway to expose **both** listeners — the original HTTP on 8080 (preserved so other labs continue to work) and a new HTTPS listener on 443:

```bash
kubectl apply -f - <<EOF
---
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
      hostname: mcp-auth0.glootest.com
      tls:
        mode: Terminate
        certificateRefs:
          - name: mcp-auth0-tls
            kind: Secret
      allowedRoutes:
        namespaces:
          from: All
EOF
```

Verify both listeners are programmed:

```bash
kubectl get gateway -n agentgateway-system agentgateway-proxy \
  -o jsonpath='{range .status.listeners[*]}{.name}{"\t"}{.conditions[?(@.type=="Programmed")].status}{"\n"}{end}'
```

Expected output:

```
http	True
https	True
```

---

## Step 3 — Deploy Postgres for OAuth State

The eager-OAuth feature stores token-exchange / authorization-code state in a database. This lab uses Postgres (production-realistic). For quick iteration you can skip Postgres and use SQLite in-memory — see the callout below.

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

> **Skip Postgres? Use SQLite in-memory.** Omit Step 3 entirely, then in Step 5 omit the `database:` block from the values. The gateway will use SQLite in-memory. State is lost on pod restart — fine for a lab, not for production.

---

## Step 4 — Add STS Env Vars to the Gateway Config

The eager-OAuth flow needs two env vars on the agentgateway proxy pod so it knows where the in-cluster STS endpoint lives. Patch the existing `agentgateway-config` `EnterpriseAgentgatewayParameters` from Lab 001 — do not recreate it; the patch preserves all other settings.

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

## Step 5 — Helm Upgrade with Eager-OAuth Values and the Pre-Issuance Hook

Re-run `helm upgrade` to enable the eager-OAuth feature in the controller, point it at Postgres + Auth0 JWKS, inject the OAuth issuer config, **and wire the pre-issuance ext_authz hook**.

```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
  -f -<<EOF
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-config
    namespace: agentgateway-system

tokenExchange:
  enabled: true
  issuer: "enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777"
  tokenExpiration: 24h
  subjectValidator:
    validatorType: remote
    remoteConfig:
      url: "https://${AUTH0_DOMAIN}/.well-known/jwks.json"
  apiValidator:
    validatorType: remote
    remoteConfig:
      url: "https://${AUTH0_DOMAIN}/.well-known/jwks.json"
  actorValidator:
    validatorType: k8s
  database:
    type: postgres
    postgres:
      url: postgres://myuser:mypassword@postgres.postgres:5432/mydb

controller:
  extraEnv:
    KGW_OAUTH_ISSUER_CONFIG: |
      {
        "gateway_config": {
          "base_url": "https://${AUTH0_GATEWAY_HOST}/oauth-issuer"
        },
        "client_config": {
          "clients": {
            "${AUTH0_CLIENT_ID}": "${AUTH0_CLIENT_SECRET}"
          }
        },
        "downstream_server": {
          "name": "auth0",
          "client_id": "${AUTH0_CLIENT_ID}",
          "client_secret": "${AUTH0_CLIENT_SECRET}",
          "authorize_url": "${AUTH0_ISSUER}authorize",
          "token_url": "${AUTH0_ISSUER}oauth/token",
          "redirect_uri": "https://${AUTH0_GATEWAY_HOST}/oauth-issuer/callback/downstream",
          "scopes": ["openid", "profile", "email"]
        },
        "pre_issuance": {
          "enabled": true,
          "grpc": {
            "target": "grpc-ext-authz.agentgateway-system.svc.cluster.local:4444",
            "insecure_disable_tls": true
          },
          "denied_redirect": "https://example.com/no-access",
          "failure_policy": "closed"
        }
      }
EOF
```

What each setting does:

| Setting | Purpose |
|---|---|
| `tokenExchange.enabled: true` | Turns the eager-OAuth feature on at the controller level (and starts the controller's port-7777 server that hosts both the AS endpoints and the STS) |
| `tokenExchange.subjectValidator` / `apiValidator` / `actorValidator` | All three required at boot — the controller refuses to start without them, even though only the eager-OAuth issuer (not RFC 8693 token exchange) is being used here. Crash signature if missing: `error creating actor validator: unsupported validator type:` |
| `tokenExchange.database.postgres.url` | Postgres connection string from Step 3; omit for SQLite in-memory |
| `gateway_config.base_url` | Public URL clients use to reach the gateway's AS endpoints (must include `/oauth-issuer`) |
| `client_config.clients` | Pre-registered `client_id`/`client_secret` table — `/oauth-issuer/register` returns one of these |
| `downstream_server` | Credentials and URLs for the gateway to talk to Auth0 during the authorization code flow; `redirect_uri` must match an entry in the Auth0 app's "Allowed Callback URLs" |
| **`pre_issuance.enabled: true`** | Turns the pre-issuance hook on. When `false` (or absent), the controller skips the gRPC Check entirely. |
| **`pre_issuance.grpc.target`** | Cluster DNS + port of the gRPC ext_authz Service. This Service is created in Step 7 — until then, the controller's dial attempts will fail. Combined with `failure_policy: closed` below, that means OAuth flows attempted between Step 5 and Step 7 will fail with a 400 — that's expected. |
| **`pre_issuance.grpc.insecure_disable_tls`** | Talk to the ext_authz Service over plain HTTP/2 (cleartext gRPC). Production deployments should run the ext_authz Service with TLS and remove this flag. |
| **`pre_issuance.denied_redirect`** | URL the gateway 307s to when ext_authz returns `PERMISSION_DENIED`. The controller appends `?client_id=<...>&resource=<...>` so a branded deny page can render context-aware copy. |
| **`pre_issuance.failure_policy`** | `closed` = on gRPC dial/timeout errors, fail the auth flow with 400 (does **not** trigger `denied_redirect`). `open` = silently allow on errors. Always use `closed` for demos and production. |

Wait for the controller and proxy pods to restart cleanly:

```bash
kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
kubectl rollout status -n agentgateway-system deployment/agentgateway-proxy --timeout=180s
```

> **⚠ Don't test login yet.** The controller is now configured with `pre_issuance.enabled: true` pointing at a Service that doesn't exist until Step 7. Any OAuth flow attempted between now and Step 7 will fail closed with a 400. Proceed to Step 6.

> **⚠ Audience injection.** Auth0 requires an `audience` query parameter on `/authorize` to issue access tokens scoped to a specific API. The eager-OAuth `downstream_server` config block as documented does not show an `audience` field. If MCP Inspector successfully completes login but the returned JWT's `aud` claim does not match `${AUTH0_AUDIENCE}`, set `${AUTH0_AUDIENCE}` as the **default audience** for your Auth0 tenant (Auth0 Dashboard → Settings → API Authorization Settings → Default Audience), or drop the `audiences` requirement from the MCP authentication policy in Step 9 and accept any Auth0-issued JWT (issuer-only validation).

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
    - mcp-auth0.glootest.com
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

## Step 7 — Deploy the gRPC ext-authz Service for the Pre-Issuance Hook

The pre-issuance hook configured in Step 5 dials a gRPC ext_authz Service at `grpc-ext-authz.agentgateway-system.svc.cluster.local:4444`. This step deploys that Service.

The implementation is [`ably7/grpc-ext-authz`](https://github.com/ably77/grpc-ext-authz) — the same image used by [`mcp-byo-grpc-ext-authz.md`](mcp-byo-grpc-ext-authz.md), but here it runs in `AUTH_MODE=principal`. In principal mode the service allowlists by `source.principal`, which the controller sets to the downstream user_id (Auth0 `sub`, e.g. `auth0|6a0bec30059b1981ce4674f6`). Any user whose sub is not in `ALLOWED_PRINCIPALS` triggers a `PERMISSION_DENIED` response.

```bash
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grpc-ext-authz
  namespace: agentgateway-system
  labels:
    app: grpc-ext-authz
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grpc-ext-authz
  template:
    metadata:
      labels:
        app: grpc-ext-authz
        app.kubernetes.io/name: grpc-ext-authz
    spec:
      containers:
      - name: grpc-ext-authz
        image: ably7/grpc-ext-authz:0.2.0
        ports:
        - containerPort: 9000
        env:
        - name: PORT
          value: "9000"
        - name: AUTH_MODE
          value: "principal"
        - name: ALLOWED_PRINCIPALS
          # Comma-separated Auth0 subs allowed through the pre-issuance
          # hook. Default: jdoe@solo.io in the Solo demo tenant.
          value: "auth0|6a0bec30059b1981ce4674f6"
---
apiVersion: v1
kind: Service
metadata:
  name: grpc-ext-authz
  namespace: agentgateway-system
  labels:
    app: grpc-ext-authz
spec:
  selector:
    app: grpc-ext-authz
  ports:
  - port: 4444
    targetPort: 9000
    protocol: TCP
    appProtocol: kubernetes.io/h2c
EOF
```

The `appProtocol: kubernetes.io/h2c` annotation tells the gateway that this backend speaks gRPC (HTTP/2 cleartext).

Wait for the Deployment to roll out:

```bash
kubectl rollout status deployment/grpc-ext-authz -n agentgateway-system --timeout=60s
```

Expected output:

```
deployment "grpc-ext-authz" successfully rolled out
```

Check the startup log to confirm the mode and the loaded allowlist:

```bash
kubectl logs -n agentgateway-system deployment/grpc-ext-authz --tail=5
```

You should see lines indicating `AUTH_MODE=principal` and the `ALLOWED_PRINCIPALS` count.

### Using your own Auth0 tenant

`ALLOWED_PRINCIPALS` ships set to the Auth0 `sub` of `jdoe@solo.io` — a UI-generated user in the Solo demo tenant. If you are running this lab against a different Auth0 tenant, you need to substitute the sub of a user **you** want to allow.

To find a user's Auth0 `sub`:

- **In the Auth0 dashboard**: User Management → Users → click the user → Identity tab → `user_id` (subs from database-connection logins look like `auth0|<24-hex>`).
- **From an issued JWT**: decode any JWT that user has received (e.g., at [jwt.io](https://jwt.io)) and read the `sub` claim.

Once you have the sub, edit the `ALLOWED_PRINCIPALS` env var on the Deployment. Comma-separated for multiple allowed users:

```bash
kubectl set env deployment/grpc-ext-authz -n agentgateway-system \
  ALLOWED_PRINCIPALS="auth0|YOUR_USER_A_SUB,auth0|YOUR_USER_B_SUB"
kubectl rollout status deployment/grpc-ext-authz -n agentgateway-system --timeout=60s
```

Leave the user you want to use for the deny path **out** of `ALLOWED_PRINCIPALS`. In Steps 10 and 11 you will log in as each user in turn and see the ext-authz pod logs flip from `ALLOWED` to `DENIED`.

---

## Step 8 — Deploy the MCP Server, Multiplexed Backend, Route, JWKS Backend, and Elicitation Secret

This step deploys five resources in `agentgateway-system`:

| Resource | Kind | Description |
|---|---|---|
| `mcp-server` | Deployment + Service | `@modelcontextprotocol/server-everything` reference server in Streamable HTTP mode (run via `npx` on `node:20-alpine`). Streamable HTTP is per-request stateless, which lets Lab 001's `replicas: 2` proxy stay unchanged. |
| `mcp-backend` | EnterpriseAgentgatewayBackend | **Multiplexed** — wraps two MCP targets behind one backend: the in-cluster `mcp-server` (named `everything`) and the remote `search.solo.io` MCP server (named `soloio-docs`, reached via HTTPS using `policies.tls: {}`). The gateway fans every MCP request out to both targets and merges their tool lists into one view. |
| `mcp-route` | HTTPRoute | Exposes `/mcp` plus the two `.well-known/oauth-*-resource/mcp` discovery paths on the `https` listener |
| `auth0-jwks` | EnterpriseAgentgatewayBackend | Static backend pointing at Auth0 for JWKS lookups during request validation |
| `elicitation-secret` | Secret | **Required** by the eager-OAuth issuer at the start of an auth flow. The controller looks for this exact name in its own namespace and 500s with `secret not found: agentgateway-system/elicitation-secret` on `/oauth-issuer/authorize` if it's missing. |

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
  app_id: "auth0"
  authorize_url: "${AUTH0_ISSUER}authorize"
  access_token_url: "${AUTH0_ISSUER}oauth/token"
  client_id: "${AUTH0_CLIENT_ID}"
  client_secret: "${AUTH0_CLIENT_SECRET}"
  mcp_resource: "/mcp"
  scopes: "openid profile email"
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
      - name: everything
        static:
          host: mcp-server.agentgateway-system.svc.cluster.local
          port: 80
          protocol: StreamableHTTP
      - name: soloio-docs
        static:
          host: search.solo.io
          port: 443
          protocol: StreamableHTTP
          policies:
            tls: {}
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
    - mcp-auth0.glootest.com
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
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: auth0-jwks
  namespace: agentgateway-system
spec:
  static:
    host: ${AUTH0_DOMAIN}
    port: 443
  policies:
    tls: {}
EOF
```

Wait for the test server to come up:

```bash
kubectl rollout status -n agentgateway-system deployment/mcp-server --timeout=120s
```

Expected output:

```
deployment "mcp-server" successfully rolled out
```

> The remote `soloio-docs` target requires DNS resolution of `search.solo.io` and outbound HTTPS from the cluster. If your environment blocks egress, the multiplexed backend will still attach but tool calls against `soloio-docs` will fail at runtime. The in-cluster `everything` target works regardless.

---

## Step 9 — Apply the MCP Authentication Policy

The policy ties everything together:

| Field | Purpose |
|---|---|
| `issuer` | Auth0 is the JWT issuer (`${AUTH0_ISSUER}` — trailing slash matters) |
| `jwks` | Points at the `auth0-jwks` backend created in Step 8. **`jwksPath` must be written without a leading slash** (`.well-known/jwks.json`) — the controller appends `/` between the backend URL and `jwksPath`, so a leading slash produces `https://$AUTH0_DOMAIN//.well-known/jwks.json`, which Auth0 returns 404 for. The controller log signature is `failed resolving jwks ... 404` and the policy goes `PartiallyValid`; `/mcp` then bypasses auth entirely. |
| `audiences` | The Auth0 API audience the JWT must carry |
| `resourceMetadata.agentgateway.dev/issuer-proxy` | Tells the gateway to serve its own AS metadata (from the in-cluster eager-OAuth issuer at `:7777/oauth-issuer`) when an MCP client fetches `.well-known/oauth-authorization-server/mcp`. Without this, the gateway would proxy Auth0's metadata directly. |
| `resourceMetadata.authorizationServers` / `resource` | What shows up in the protected-resource discovery document for clients |

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-auth0-eager
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
        issuer: ${AUTH0_ISSUER}
        audiences:
          - ${AUTH0_AUDIENCE}
        jwks:
          backendRef:
            name: auth0-jwks
            kind: AgentgatewayBackend
            group: agentgateway.dev
          jwksPath: .well-known/jwks.json
        resourceMetadata:
          agentgateway.dev/issuer-proxy: http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/oauth-issuer
          authorizationServers:
            - https://${AUTH0_GATEWAY_HOST}/mcp
          resource: https://${AUTH0_GATEWAY_HOST}/mcp
EOF
```

---

## Step 10 — Test with MCP Inspector — Allow Path

### Trust the self-signed cert in your browser

Before launching Inspector, hit the gateway in your browser once to accept the self-signed cert warning:

```
https://mcp-auth0.glootest.com/.well-known/oauth-protected-resource/mcp
```

Click "Advanced → proceed" (Chrome) / "Accept the risk" (Firefox). You should see the JSON discovery document. Without this step, the browser blocks the OAuth redirect chain silently.

### Launch Inspector locally

The Inspector backend (Node) opens HTTP connections to the gateway and won't accept self-signed certs by default. Disable Node's TLS verification for the Inspector process:

```bash
NODE_TLS_REJECT_UNAUTHORIZED=0 npx @modelcontextprotocol/inspector
```

Inspector binds to `http://localhost:6274` and prints a session token in the terminal. Open the printed URL in a browser.

### Configure the connection

In the Inspector UI:

- **Transport type:** `Streamable HTTP`
- **Server URL:** `https://mcp-auth0.glootest.com/mcp`
- Click **Connect**.

### Walk through the OAuth flow as an allowlisted user

Inspector follows the protected-resource discovery automatically. **Log in as the user whose Auth0 sub IS in `ALLOWED_PRINCIPALS`** (default: `jdoe@solo.io`). You should see:

1. A redirect to Auth0's Universal Login. **Verify the URL bar shows `${AUTH0_DOMAIN}`, not the gateway hostname** — this confirms the eager-OAuth issuer correctly delegated downstream.
2. After completing Auth0 login (with MFA if your tenant requires it), a redirect back to Inspector's local callback.
3. Inspector status flips to **Connected**.

Between Auth0's callback and Inspector receiving its token, the gateway called the `grpc-ext-authz` Service with `source.principal = auth0|<jdoe's sub>`. Because that sub is in `ALLOWED_PRINCIPALS`, ext-authz returned `OK` and the gateway issued the token. Tail the ext-authz logs to see the decision:

```bash
kubectl logs -n agentgateway-system deployment/grpc-ext-authz --tail=10
```

You should see a single `ALLOWED` line that includes the principal.

### Confirm tools from BOTH upstreams are reachable

In the Inspector left panel, click **Tools → List Tools**. You should see tools from both multiplexed upstreams:

- From `everything` (in-cluster `server-everything`): tools like `echo`, `add`, `printEnv`, `sampleLLM`, etc.
- From `soloio-docs` (remote `search.solo.io`): tools for searching Solo.io documentation.

Call one tool from each upstream — for example, `everything/echo` with a test string, and any `soloio-docs` search tool — to prove the multiplexed path works post-auth. Both should return a tool result, not a 401.

### What proves what

| Observation in Inspector | What it proves |
|---|---|
| Redirect lands on `${AUTH0_DOMAIN}` | Eager-OAuth issuer is serving its own AS metadata; `registration_endpoint` was rewritten to point at the gateway |
| Login completes and Inspector shows "Connected" | Pre-issuance hook ALLOWED the principal; the pre-registered `client_id`/`client_secret` matched the Auth0 app; fake-DCR worked |
| Tool list renders without 401 and includes tools from **both** upstreams | Auth0-issued JWT validated against Auth0 JWKS; multiplexed `mcp-backend` is forwarding to both targets |
| `kubectl logs` shows `ALLOWED` line | Pre-issuance hook fired and returned OK |

---

## Step 11 — Test with MCP Inspector — Deny Path

Now repeat the connection as a **different Auth0 user whose sub is NOT in `ALLOWED_PRINCIPALS`**. The pre-issuance hook should refuse to issue a token and redirect the browser to `denied_redirect`.

### Reset the Auth0 session

Auth0's session cookie will silently re-use the previous user's session if you skip this step. In Inspector:

1. Click **Disconnect**.
2. Click **Reset Auth** (clears Inspector's stored OAuth state).

If your Auth0 tenant cookie is still warm and the second login keeps short-circuiting back to the first user, open a fresh **private/incognito** browser window for the deny pass instead.

### Connect again as a denied user

In the (fresh) Inspector window:

- **Server URL:** `https://mcp-auth0.glootest.com/mcp`
- Click **Connect**.
- When redirected to `${AUTH0_DOMAIN}`, log in as a user whose sub is **not** in `ALLOWED_PRINCIPALS` (e.g., `alex.ly@solo.io` against the default Solo demo tenant).

### What you should see

1. Auth0 login completes successfully — Auth0 doesn't know anything about your entitlement decision; it just authenticates the user.
2. Auth0 redirects back to the gateway's `/oauth-issuer/callback/...`.
3. The gateway calls `grpc-ext-authz` with `source.principal = auth0|<this user's sub>`. ext-authz returns `PERMISSION_DENIED`.
4. **The gateway responds 307 Temporary Redirect** with `Location: https://example.com/no-access?client_id=<...>&resource=<...>`.
5. **The browser lands on `https://example.com/no-access?...`** — the placeholder deny page. In production this would be your customer-branded "no access" UI; the `client_id` and `resource` query params let it render context-aware copy.
6. Inspector shows a connection error in its terminal — no token was ever issued.

### Verify the decision in ext-authz logs

```bash
kubectl logs -n agentgateway-system deployment/grpc-ext-authz --tail=20
```

You should see a `DENIED` line that includes the principal of the user you just tried.

### Customizing the deny page

The default `denied_redirect` is `https://example.com/no-access` — a placeholder. To point at your own branded page, re-run the Step 5 helm upgrade with a different `denied_redirect` value:

```yaml
"pre_issuance": {
  "enabled": true,
  "grpc": { ... },
  "denied_redirect": "https://denied.example.com/mcp-no-access",
  "failure_policy": "closed"
}
```

The change takes effect once the controller restarts (helm upgrade triggers a rollout).

---

## Step 12 (Optional) — Verify Postgres-Backed State Survives a Restart

Skip if you opted into SQLite in Step 3 — state is in-memory and **will not** survive restart.

```bash
kubectl rollout restart -n agentgateway-system deployment/enterprise-agentgateway
kubectl rollout restart -n agentgateway-system deployment/agentgateway-proxy
kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
kubectl rollout status -n agentgateway-system deployment/agentgateway-proxy --timeout=180s
```

Reconnect from Inspector (as the allowlisted user from Step 10). If Postgres is wired correctly the gateway should accept the previously-issued client credentials and skip re-registration.

---

## Troubleshooting

If MCP Inspector behaves unexpectedly, this table covers the common breakage modes for an eager-OAuth + Auth0 + pre-issuance-hook setup.

| Symptom in Inspector | Likely Cause | Where to Look |
|---|---|---|
| `/.well-known/oauth-authorization-server/mcp` returns Auth0's metadata (registration endpoint = Auth0) | The `agentgateway.dev/issuer-proxy` annotation under `resourceMetadata` is missing, or the `oauth-issuer` HTTPRoute (Step 6) is misrouted | Step 9 — confirm `agentgateway.dev/issuer-proxy` is set; Step 6 — `kubectl get httproute -n agentgateway-system oauth-issuer` |
| `/oauth-issuer/register` returns 404 or 501 | Step 5 helm upgrade did not apply `tokenExchange.enabled` + the issuer config, or the `/oauth-issuer` HTTPRoute (Step 6) is missing | `kubectl get httproute -n agentgateway-system oauth-issuer`; gateway pod logs |
| `GET /mcp` without a token returns **406** instead of 401, and `/.well-known/oauth-*-resource/mcp` returns 404 | The MCP authentication policy is `PartiallyValid` because the controller can't fetch JWKS. Most often caused by a leading slash on `jwksPath` (`/.well-known/jwks.json`), which produces `https://$AUTH0_DOMAIN//.well-known/jwks.json` (404 from Auth0) | `kubectl get enterpriseagentgatewaypolicy -n agentgateway-system mcp-auth0-eager -o jsonpath='{.status.ancestors[*].conditions[*].message}'` should say `Policy accepted Attached to all targets`. Controller logs: `kubectl logs -n agentgateway-system deployment/enterprise-agentgateway \| grep jwks`. Fix per Step 9 — `jwksPath: .well-known/jwks.json` (no leading slash) |
| Controller pod CrashLoopBackOff with `error creating actor validator: unsupported validator type:` | Step 5 helm values are missing `tokenExchange.actorValidator` (and/or `apiValidator`) — all three validators are required at boot even though only the eager-OAuth issuer is being used | Re-run Step 5 with the validator block matching this lab |
| Inspector errors immediately (no Auth0 redirect) and controller logs show `failed to start auth flow ... secret not found: agentgateway-system/elicitation-secret` | The `elicitation-secret` Secret from Step 8 wasn't created or is in the wrong namespace | `kubectl get secret -n agentgateway-system elicitation-secret`; recreate per Step 8 |
| Auth0 error page after login (`callback url not allowed` / `invalid redirect_uri`) **even though the URI is in the app's allowlist** | The eager-OAuth issuer uses two callback paths (`/callback/upstream` for PKCE/MCP-client flows, `/callback/downstream` otherwise). Registering only one yields a rejection on whichever flow the client triggers | Confirm **both** `https://${AUTH0_GATEWAY_HOST}/oauth-issuer/callback/upstream` and `.../callback/downstream` are present in the Auth0 app's "Allowed Callback URLs" |
| Auth0 error page after login (`client not found` / `invalid_client`) | `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` don't match the Auth0 app, or the app is disabled / not assigned to the Auth0 connection | Auth0 admin → Applications → *your app* → Settings (Client ID, Client Secret), and Connections tab |
| 401 after browser flow with a valid-looking JWT | `mcp.authentication.audiences` doesn't include the `aud` claim Auth0 actually issued, or the `issuer` value's trailing slash doesn't match | Decode the JWT at `jwt.io`; compare `iss` to `${AUTH0_ISSUER}` (trailing slash) and `aud` to `${AUTH0_AUDIENCE}`. See the audience-injection callout in Step 5. |
| Inspector shows "fetch failed" or `unable to verify the first certificate` | Inspector's Node process rejected the self-signed gateway cert | Restart Inspector with `NODE_TLS_REJECT_UNAUTHORIZED=0` (Step 10) |
| Inspector loops on connect with no Auth0 redirect; browser DevTools console (F12) shows `Access to fetch at '.../.well-known/oauth-*-resource/mcp' has been blocked by CORS policy` or `mcp-protocol-version is not allowed by Access-Control-Allow-Headers` | OAuth metadata discovery runs in the **browser** (Inspector UI), not through Inspector's `localhost:6277` proxy. Inspector sends `mcp-protocol-version` on the preflight, but agentgateway's internal handler hardcodes `Access-Control-Allow-Headers: content-type` and rejects it | The Step 9 `mcp-route` HTTPRoute must attach a Gateway API `CORS` filter to both `/.well-known/oauth-*/mcp` rules that allows `mcp-protocol-version` (and `Authorization`). Confirm with `kubectl get httproute -n agentgateway-system mcp-route -o yaml \| grep -A6 'type: CORS'` |
| Browser shows `ERR_CERT_AUTHORITY_INVALID` and the OAuth flow stops | Browser hasn't accepted the self-signed cert yet | Visit `https://mcp-auth0.glootest.com/.well-known/oauth-protected-resource/mcp` and click through the warning |
| `mcp-auth0.glootest.com` doesn't resolve | `/etc/hosts` entry missing or DNS cache stale | Re-run the `echo "$GATEWAY_IP $AUTH0_GATEWAY_HOST" \| sudo tee -a /etc/hosts` step; on macOS flush DNS |
| **Every login redirects to `https://example.com/no-access`** (or your custom deny page) | The Auth0 sub of the user you logged in as is not in `ALLOWED_PRINCIPALS`. This is the expected deny-path behavior — but if you meant to be on the allow path, the allowlist needs to be updated. | `kubectl logs -n agentgateway-system deployment/grpc-ext-authz --tail=20` — every Check prints one line including the `source.principal` it saw. Edit `ALLOWED_PRINCIPALS` per Step 7's BYO sub-section. |
| **Inspector shows "failed to process downstream callback" 400 instead of redirecting** | The ext-authz pod is unreachable or timing out. With `failure_policy: closed`, gRPC dial/timeout errors do NOT trigger the deny redirect — the redirect only fires on an explicit `PERMISSION_DENIED`. | `kubectl get pods -n agentgateway-system -l app=grpc-ext-authz`; `kubectl get svc -n agentgateway-system grpc-ext-authz`; controller logs |
| **`source.principal=""` in ext-authz logs** | The agentgateway controller is too old to populate `source.principal` on the pre-issuance Check | `kubectl get deploy -n agentgateway-system enterprise-agentgateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="controller")].image}'` — verify the image tag is `v2026.5.x` or later |
| **Inspector connects despite the user being absent from `ALLOWED_PRINCIPALS`** | `pre_issuance.enabled` is not actually true in `KGW_OAUTH_ISSUER_CONFIG` in the running controller | `kubectl get deploy -n agentgateway-system enterprise-agentgateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="controller")].env[?(@.name=="KGW_OAUTH_ISSUER_CONFIG")].value}' \| jq .pre_issuance` |

Useful commands:

```bash
# Confirm the discovery endpoints respond from the public URL
curl -sk "https://${AUTH0_GATEWAY_HOST}/.well-known/oauth-protected-resource/mcp" | jq .
curl -sk "https://${AUTH0_GATEWAY_HOST}/.well-known/oauth-authorization-server/mcp" | jq .

# Verify registration_endpoint points at the gateway, not Auth0
curl -sk "https://${AUTH0_GATEWAY_HOST}/.well-known/oauth-authorization-server/mcp" | jq .registration_endpoint

# Tail gateway logs during an Inspector connection attempt
kubectl logs -n agentgateway-system deployment/agentgateway-proxy -f

# Tail ext-authz logs to watch pre-issuance decisions
kubectl logs -n agentgateway-system deployment/grpc-ext-authz -f
```

---

## Cleanup

Fully revert to the Lab 001 baseline. Run these in order — the helm revert is **required**, not optional. Skipping it leaves the controller running with `tokenExchange.enabled` and a postgres URL pointing at a deleted DB; a future re-run of this lab will hit `relation "oauth_flow_states" does not exist` because the controller pod never restarts to migrate against a fresh postgres.

```bash
# 1. Delete lab-specific resources
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-auth0-eager --ignore-not-found
kubectl delete httproute -n agentgateway-system mcp-route oauth-issuer --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-backend --ignore-not-found
kubectl delete agentgatewaybackend -n agentgateway-system auth0-jwks --ignore-not-found
kubectl delete deployment -n agentgateway-system mcp-server --ignore-not-found
kubectl delete service -n agentgateway-system mcp-server --ignore-not-found
kubectl delete secret -n agentgateway-system elicitation-secret mcp-auth0-tls --ignore-not-found

# Pre-issuance hook: remove the ext-authz Deployment + Service
kubectl delete deployment -n agentgateway-system grpc-ext-authz --ignore-not-found
kubectl delete service -n agentgateway-system grpc-ext-authz --ignore-not-found

# 2. Roll back the EnterpriseAgentgatewayParameters env vars added in Step 4
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type=json \
  -p='[{"op":"remove","path":"/spec/env"}]' || true

# 3. Restore the Lab 001 Gateway (HTTP-only on port 8080, no HTTPS listener)
kubectl apply -f - <<EOF
---
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

# 4. Re-run Lab 001's helm upgrade to drop tokenExchange + KGW_OAUTH_ISSUER_CONFIG
#    (including the pre_issuance block). This restarts the controller and
#    clears its stale postgres connection.
#    Re-detect ENTERPRISE_AGW_VERSION in case this cleanup runs in a fresh shell.
export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
  -f -<<EOF
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-config
    namespace: agentgateway-system
EOF

kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s

# 5. Drop the Postgres namespace
kubectl delete namespace postgres --ignore-not-found

# 6. Remove local cert files and the /etc/hosts entry
rm -rf example_certs
sudo sed -i '' "/${AUTH0_GATEWAY_HOST}/d" /etc/hosts   # macOS; on Linux drop the empty '' arg
```

If helm reports the upgrade as a no-op (identical revision), force a controller restart manually so any stale DB state is cleared:

```bash
kubectl rollout restart -n agentgateway-system deployment/enterprise-agentgateway
kubectl rollout status   -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
```
