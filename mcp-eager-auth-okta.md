# MCP Authentication with Okta via Eager OAuth

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

### Okta requirements

You need a registered application in Okta with the **Authorization Code** grant type enabled, plus an Okta authorization server with an audience configured. Most labs use the built-in `default` custom authorization server. Capture these values for Step 1:

| Variable | Description |
|---|---|
| `OKTA_DOMAIN` | Your tenant domain, e.g. `dev-12345.okta.com` (no scheme, no path) — used by the `okta-jwks` backend |
| `OKTA_AUTH_SERVER_ID` | Authorization server ID; `default` for the built-in custom authz server |
| `OKTA_ISSUER` | `https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>` — **no trailing slash** (Okta emits `iss` without one) |
| `OKTA_CLIENT_ID` | Client ID of the Okta application |
| `OKTA_CLIENT_SECRET` | Client secret of the Okta application |
| `OKTA_AUDIENCE` | Audience configured on the Okta authz server (e.g. `api://default`) — must match the `aud` claim on issued tokens |
| `GATEWAY_HOST` | Public hostname for the gateway (no scheme) — this lab uses `mcp-okta.glootest.com` |

### Okta app callback URLs

Add **both** of these gateway callbacks to the Okta application's "Sign-in redirect URIs":

```
https://mcp-okta.glootest.com/oauth-issuer/callback/downstream
https://mcp-okta.glootest.com/oauth-issuer/callback/upstream
```

The eager-OAuth issuer runs a "dual OAuth flow" and uses different callback paths depending on the client. PKCE-capable MCP clients (e.g., MCP Inspector) trigger `/callback/upstream`; non-PKCE flows trigger `/callback/downstream`. Registering only one yields an Okta `The 'redirect_uri' parameter must be a Login redirect URI` error after login — even though the URI you configured for `downstream_server.redirect_uri` *is* in the allowlist.

### Required tools

- `kubectl` and `helm`
- `openssl` (for the self-signed gateway cert)
- Node 18+ (for MCP Inspector in Step 9)
- `jq` for inspecting JSON responses
- Sudo access to edit `/etc/hosts`

---

## Lab Objectives

- Stand up the eager-OAuth feature so the gateway acts as the OAuth Authorization Server visible to MCP clients
- Use a single pre-registered Okta `client_id` / `client_secret` for all MCP clients (no Okta admin-UI churn from per-client DCR)
- Broker the Okta authorization code flow through the gateway (`/oauth-issuer/...`)
- Validate Okta-issued JWTs at the MCP backend against Okta JWKS
- Terminate TLS on `agentgateway-proxy` with a self-signed cert for `mcp-okta.glootest.com`
- Test end-to-end with MCP Inspector against an `mcp-server-everything` test server

---

## Background

Why eager OAuth with Okta?

Okta supports Dynamic Client Registration (RFC 7591) natively, but it has practical drawbacks for MCP at scale:

- Every DCR call creates a new application entry in the Okta admin UI. With many MCP clients (Claude Code, Cursor, VS Code, ChatGPT, Inspector, …) per developer, the admin UI fills up quickly.
- DCR requires the Okta Management API and per-tenant configuration that some orgs don't want to expose to gateway components.
- Operationally, most teams want a single "MCP Gateway" app registered in Okta, not one per client.

**Eager OAuth** with pre-registered client_ids fixes this. agentgateway becomes the OAuth Authorization Server that MCP clients see, and Okta sits downstream of the gateway. MCP clients DCR against the gateway and get a single pre-registered Okta `client_id` / `client_secret` pair — no Okta admin-UI churn, no Management API needed at runtime.

```
┌──────────────┐   1. discovery + DCR   ┌─────────────────┐  3. authorize/token  ┌──────┐
│  MCP client  │ ──────────────────────▶│  agentgateway   │ ───────────────────▶ │ Okta │
│ (Inspector,  │ ◀──────────────────────│ (OAuth issuer @ │ ◀─────────────────── │      │
│  Claude, …)  │   2. issuer metadata   │ /oauth-issuer)  │  4. authorization    │      │
└──────────────┘     pointing at GW     └─────────────────┘     code → token     └──────┘
                                                │
                                                │  5. validate token, forward
                                                ▼
                                         ┌────────────────┐
                                         │   MCP server   │
                                         │ (test target)  │
                                         └────────────────┘
```

Three things make this work:

1. **Issuer metadata is served by the gateway** (`/.well-known/oauth-authorization-server/...`), so `registration_endpoint` points at the gateway, not Okta.
2. **The gateway implements `/oauth-issuer/register`** and returns the pre-registered Okta client_id from the issuer config's `client_config.clients`.
3. **The gateway brokers the authorization code flow** to Okta using the issuer config's `downstream_server`. The browser still opens to Okta's hosted login; the resulting JWT is what reaches the MCP backend.

---

## Custom Gateway Features Covered

- **OAuth 2.0 Authorization Server**: agentgateway acts as the AS at `/oauth-issuer/...`. MCP clients see the gateway as their OAuth provider, not Okta.
- **Pre-registered "fake DCR"**: `/oauth-issuer/register` returns the Okta `client_id`/`client_secret` pair you provide. MCP clients believe they did Dynamic Client Registration; in reality they got pre-registered credentials.
- **Authorization code flow brokering**: the gateway proxies the authorization code flow downstream to Okta (`authorize`, callback handling, `token` exchange).
- **JWT validation**: Okta-issued JWTs are validated at the MCP backend against Okta's JWKS (`/oauth2/<authz-server-id>/v1/keys`).
- **Frontend TLS termination**: the existing `agentgateway-proxy` Gateway gains an HTTPS listener on port 443 alongside lab 001's HTTP listener on 8080.

---

## Step 1 — Set Environment Variables and DNS

The Okta values and `GATEWAY_HOST` are expected to live in your shell rc (e.g. `~/.zshrc`). Re-export them so child processes (`kubectl`, `helm`) inherit the values — some shells write the rc entries without `export`, in which case they won't be inherited:

```bash
export OKTA_DOMAIN=$OKTA_DOMAIN
export OKTA_AUTH_SERVER_ID=$OKTA_AUTH_SERVER_ID
export OKTA_ISSUER=$OKTA_ISSUER
export OKTA_CLIENT_ID=$OKTA_CLIENT_ID
export OKTA_CLIENT_SECRET=$OKTA_CLIENT_SECRET
export OKTA_AUDIENCE=$OKTA_AUDIENCE
export GATEWAY_HOST=$GATEWAY_HOST

# Controller version and license (from Lab 001)
export ENTERPRISE_AGW_VERSION=v2.3.2
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY
```

This lab uses `mcp-okta.glootest.com` as the gateway hostname. If `GATEWAY_HOST` is not already set in your rc, add `GATEWAY_HOST=mcp-okta.glootest.com` and reload your shell.

Notes on these values:

- `OKTA_ISSUER` **must not** end with a trailing slash. The `iss` claim Okta puts in JWTs is `https://<domain>/oauth2/<authz-server-id>` (no trailing `/`), and the MCP authentication policy compares the two literally.
- `OKTA_AUTH_SERVER_ID` is `default` for the built-in custom authorization server. If you're using a different custom authz server, substitute its ID. (The Okta org auth server has a different shape — see the troubleshooting table.)
- `OKTA_AUDIENCE` is whatever you configured in the authz server's "Audience" field (Okta admin → Security → API → Authorization Servers → *your-server* → Settings). The default authz server uses `api://default`.

### Map the gateway hostname to the LoadBalancer IP

Find the LoadBalancer IP/hostname assigned to `agentgateway-proxy` from Lab 001:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "$GATEWAY_IP"
```

Add an `/etc/hosts` entry so both your terminal and your browser resolve `mcp-okta.glootest.com` to the gateway:

```bash
echo "$GATEWAY_IP $GATEWAY_HOST" | sudo tee -a /etc/hosts
```

> **macOS DNS cache.** If the hostname doesn't resolve after the edit, flush DNS: `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`.

---

## Step 2 — Create a Self-Signed TLS Cert and Add an HTTPS Listener

OAuth requires HTTPS for everything that is not `localhost`, since the browser will redirect to Okta and back. This step creates a self-signed cert for `mcp-okta.glootest.com` and adds a port 443 HTTPS listener to the existing `agentgateway-proxy` Gateway alongside Lab 001's port 8080 HTTP listener.

Create a root certificate for the `glootest.com` domain and a leaf cert signed by that root:

```bash
mkdir -p example_certs
openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 \
  -subj '/O=Solo.io/CN=glootest.com' \
  -keyout example_certs/glootest.com.key \
  -out    example_certs/glootest.com.crt

openssl req -out example_certs/gateway.csr -newkey rsa:2048 -nodes \
  -keyout example_certs/gateway.key \
  -subj  "/CN=mcp-okta.glootest.com/O=Solo.io"

openssl x509 -req -sha256 -days 365 \
  -CA    example_certs/glootest.com.crt \
  -CAkey example_certs/glootest.com.key \
  -set_serial 0 \
  -in    example_certs/gateway.csr \
  -out   example_certs/gateway.crt \
  -extfile <(printf "subjectAltName=DNS:mcp-okta.glootest.com")
```

Store the leaf cert in a Kubernetes TLS secret:

```bash
kubectl create secret tls -n agentgateway-system mcp-okta-tls \
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
      hostname: mcp-okta.glootest.com
      tls:
        mode: Terminate
        certificateRefs:
          - name: mcp-okta-tls
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

## Step 5 — Helm Upgrade with Eager-OAuth Values

Re-run `helm upgrade` to enable the eager-OAuth feature in the controller, point it at Postgres + Okta JWKS, and inject the OAuth issuer config.

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
      url: "https://${OKTA_DOMAIN}/oauth2/${OKTA_AUTH_SERVER_ID}/v1/keys"
  apiValidator:
    validatorType: remote
    remoteConfig:
      url: "https://${OKTA_DOMAIN}/oauth2/${OKTA_AUTH_SERVER_ID}/v1/keys"
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
          "base_url": "https://${GATEWAY_HOST}/oauth-issuer"
        },
        "client_config": {
          "clients": {
            "${OKTA_CLIENT_ID}": "${OKTA_CLIENT_SECRET}"
          }
        },
        "downstream_server": {
          "name": "okta",
          "client_id": "${OKTA_CLIENT_ID}",
          "client_secret": "${OKTA_CLIENT_SECRET}",
          "authorize_url": "${OKTA_ISSUER}/v1/authorize",
          "token_url": "${OKTA_ISSUER}/v1/token",
          "redirect_uri": "https://${GATEWAY_HOST}/oauth-issuer/callback/downstream",
          "scopes": ["openid", "profile", "email"]
        }
      }
EOF
```

What each piece does:

| Setting | Purpose |
|---|---|
| `tokenExchange.enabled: true` | Turns the eager-OAuth feature on at the controller level (and starts the controller's port-7777 server that hosts both the AS endpoints and the STS) |
| `tokenExchange.subjectValidator` / `apiValidator` / `actorValidator` | All three required at boot — the controller refuses to start without them, even though only the eager-OAuth issuer (not RFC 8693 token exchange) is being used here. Crash signature if missing: `error creating actor validator: unsupported validator type:` |
| `tokenExchange.database.postgres.url` | Postgres connection string from Step 3; omit for SQLite in-memory |
| `gateway_config.base_url` | Public URL clients use to reach the gateway's AS endpoints (must include `/oauth-issuer`) |
| `client_config.clients` | Pre-registered `client_id`/`client_secret` table — `/oauth-issuer/register` returns one of these |
| `downstream_server` | Credentials and URLs for the gateway to talk to Okta during the authorization code flow; `redirect_uri` must match an entry in the Okta app's "Sign-in redirect URIs" |

Wait for the controller and proxy pods to restart cleanly:

```bash
kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
kubectl rollout status -n agentgateway-system deployment/agentgateway-proxy --timeout=180s
```

> **⚠ Audience handling.** Unlike Auth0, Okta sets the `aud` claim on issued tokens from the authorization server's "Audience" setting — no `audience` query parameter on `/authorize` is required. If MCP Inspector successfully completes login but the returned JWT's `aud` claim does not match `${OKTA_AUDIENCE}`, fix the audience in Okta admin → Security → API → Authorization Servers → *your-server* → Settings, or update `OKTA_AUDIENCE` to match what's already there. If you're using the Okta org auth server (issuer = `https://${OKTA_DOMAIN}` with no `/oauth2/...` suffix) instead of a custom authz server, all paths shift — JWKS becomes `/oauth2/v1/keys`, authorize/token become `/oauth2/v1/authorize` and `/oauth2/v1/token` — adjust accordingly.

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
    - mcp-okta.glootest.com
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
| `mcp-backend` | AgentgatewayBackend | Wraps the MCP server as an MCP target |
| `mcp-route` | HTTPRoute | Exposes `/mcp` plus the two `.well-known/oauth-*-resource/mcp` discovery paths on the `https` listener |
| `okta-jwks` | AgentgatewayBackend | Static backend pointing at Okta for JWKS lookups during request validation |
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
  app_id: "okta"
  authorize_url: "${OKTA_ISSUER}/v1/authorize"
  access_token_url: "${OKTA_ISSUER}/v1/token"
  client_id: "${OKTA_CLIENT_ID}"
  client_secret: "${OKTA_CLIENT_SECRET}"
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
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
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
    - mcp-okta.glootest.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp
      backendRefs:
        - name: mcp-backend
          group: agentgateway.dev
          kind: AgentgatewayBackend
    - matches:
        - path:
            type: PathPrefix
            value: /.well-known/oauth-protected-resource/mcp
      backendRefs:
        - name: mcp-backend
          group: agentgateway.dev
          kind: AgentgatewayBackend
    - matches:
        - path:
            type: PathPrefix
            value: /.well-known/oauth-authorization-server/mcp
      backendRefs:
        - name: mcp-backend
          group: agentgateway.dev
          kind: AgentgatewayBackend
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: okta-jwks
  namespace: agentgateway-system
spec:
  static:
    host: ${OKTA_DOMAIN}
    port: 443
  policies:
    tls: {}
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
| `issuer` | Okta is the JWT issuer (`${OKTA_ISSUER}` — **no** trailing slash) |
| `jwks` | Points at the `okta-jwks` backend created in Step 7. **`jwksPath` must be written without a leading slash** (`oauth2/${OKTA_AUTH_SERVER_ID}/v1/keys`) — the controller appends `/` between the backend URL and `jwksPath`, so a leading slash produces `https://$OKTA_DOMAIN//oauth2/...`, which Okta returns 404 for. The controller log signature is `failed resolving jwks ... 404` and the policy goes `PartiallyValid`; `/mcp` then bypasses auth entirely. |
| `audiences` | The audience configured on your Okta authz server |
| `resourceMetadata.agentgateway.dev/issuer-proxy` | Tells the gateway to serve its own AS metadata (from the in-cluster eager-OAuth issuer at `:7777/oauth-issuer`) when an MCP client fetches `.well-known/oauth-authorization-server/mcp`. Without this, the gateway would proxy Okta's metadata directly. |
| `resourceMetadata.authorizationServers` / `resource` | What shows up in the protected-resource discovery document for clients |

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-okta-eager
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: agentgateway.dev
      kind: AgentgatewayBackend
      name: mcp-backend
  backend:
    mcp:
      authentication:
        mode: Strict
        issuer: ${OKTA_ISSUER}
        audiences:
          - ${OKTA_AUDIENCE}
        jwks:
          backendRef:
            name: okta-jwks
            kind: AgentgatewayBackend
            group: agentgateway.dev
          jwksPath: oauth2/${OKTA_AUTH_SERVER_ID}/v1/keys
        resourceMetadata:
          agentgateway.dev/issuer-proxy: http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/oauth-issuer
          authorizationServers:
            - https://${GATEWAY_HOST}/mcp
          resource: https://${GATEWAY_HOST}/mcp
EOF
```

---

## Step 9 — Test with MCP Inspector

### Trust the self-signed cert in your browser

Before launching Inspector, hit the gateway in your browser once to accept the self-signed cert warning:

```
https://mcp-okta.glootest.com/.well-known/oauth-protected-resource/mcp
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
- **Server URL:** `https://mcp-okta.glootest.com/mcp`
- Click **Connect**.

### Walk through the OAuth flow

Inspector follows the protected-resource discovery automatically. You should see:

1. A redirect to Okta's hosted login. **Verify the URL bar shows `${OKTA_DOMAIN}`, not the gateway hostname** — this confirms the eager-OAuth issuer correctly delegated downstream.
2. After completing Okta login (with MFA if your tenant requires it), a redirect back to Inspector's local callback.
3. Inspector status flips to **Connected**.

### Confirm tools are reachable

In the Inspector left panel, click **Tools → List Tools**. The `mcp-server-everything` tools should render (`echo`, `add`, `printEnv`, `longRunningOperation`, `getTinyImage`, …). Run one (`echo` with `{"message":"hi"}`) — you should get a tool result, not a 401.

### What proves what

| Observation in Inspector | What it proves |
|---|---|
| Redirect lands on `${OKTA_DOMAIN}` | Eager-OAuth issuer is serving its own AS metadata; `registration_endpoint` was rewritten to point at the gateway |
| Login completes and Inspector shows "Connected" | The pre-registered `client_id`/`client_secret` from `client_config.clients` matched the Okta app — fake-DCR worked end-to-end |
| Tool list renders without 401 | Okta-issued JWT validated against Okta JWKS at the MCP backend; `mcp.authentication` is configured correctly |
| Tool execution succeeds | Full request path through the gateway works; the downstream MCP server received the bearer token |

### (Optional) Verify Postgres-backed state survives a restart

Skip if you opted into SQLite in Step 3 — state is in-memory and **will not** survive restart.

```bash
kubectl rollout restart -n agentgateway-system deployment/enterprise-agentgateway
kubectl rollout restart -n agentgateway-system deployment/agentgateway-proxy
kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
kubectl rollout status -n agentgateway-system deployment/agentgateway-proxy --timeout=180s
```

Reconnect from Inspector. If Postgres is wired correctly the gateway should accept the previously-issued client credentials and skip re-registration.

---

## Troubleshooting

If MCP Inspector behaves unexpectedly, this table covers the common breakage modes for an eager-OAuth + Okta setup.

| Symptom in Inspector | Likely Cause | Where to Look |
|---|---|---|
| `/.well-known/oauth-authorization-server/mcp` returns Okta's metadata (registration endpoint = Okta) | The `agentgateway.dev/issuer-proxy` annotation under `resourceMetadata` is missing, or the `oauth-issuer` HTTPRoute (Step 6) is misrouted | Step 8 — confirm `agentgateway.dev/issuer-proxy` is set; Step 6 — `kubectl get httproute -n agentgateway-system oauth-issuer` |
| `/oauth-issuer/register` returns 404 or 501 | Step 5 helm upgrade did not apply `tokenExchange.enabled` + the issuer config, or the `/oauth-issuer` HTTPRoute (Step 6) is missing | `kubectl get httproute -n agentgateway-system oauth-issuer`; gateway pod logs |
| `GET /mcp` without a token returns **406** instead of 401, and `/.well-known/oauth-*-resource/mcp` returns 404 | The MCP authentication policy is `PartiallyValid` because the controller can't fetch JWKS. Most often caused by a leading slash on `jwksPath` (`/oauth2/...`), which produces `https://$OKTA_DOMAIN//oauth2/...` (404 from Okta) | `kubectl get enterpriseagentgatewaypolicy -n agentgateway-system mcp-okta-eager -o jsonpath='{.status.ancestors[*].conditions[*].message}'` should say `Policy accepted Attached to all targets`. Controller logs: `kubectl logs -n agentgateway-system deployment/enterprise-agentgateway \| grep jwks`. Fix per Step 8 — `jwksPath: oauth2/${OKTA_AUTH_SERVER_ID}/v1/keys` (no leading slash) |
| `invalid_token` with `KID not found` | `jwksPath` doesn't match your authz server's keys endpoint | Step 8 — `jwksPath` should be `oauth2/<your-authz-server-id>/v1/keys`. The `default` authz server uses `oauth2/default/v1/keys`; the Okta org auth server uses `oauth2/v1/keys` (no `<id>/`) |
| Controller pod CrashLoopBackOff with `error creating actor validator: unsupported validator type:` | Step 5 helm values are missing `tokenExchange.actorValidator` (and/or `apiValidator`) — all three validators are required at boot even though only the eager-OAuth issuer is being used | Re-run Step 5 with the validator block matching this lab |
| Inspector errors immediately (no Okta redirect) and controller logs show `failed to start auth flow ... secret not found: agentgateway-system/elicitation-secret` | The `elicitation-secret` Secret from Step 7 wasn't created or is in the wrong namespace | `kubectl get secret -n agentgateway-system elicitation-secret`; recreate per Step 7 |
| Okta error page after login (`The 'redirect_uri' parameter must be a Login redirect URI`) **even though the URI is in the app's allowlist** | The eager-OAuth issuer uses two callback paths (`/callback/upstream` for PKCE/MCP-client flows, `/callback/downstream` otherwise). Registering only one yields a rejection on whichever flow the client triggers | Confirm **both** `https://${GATEWAY_HOST}/oauth-issuer/callback/upstream` and `.../callback/downstream` are present in the Okta app's "Sign-in redirect URIs" |
| Okta error page (`Application not assigned` or similar) | The Okta user isn't assigned to the app, or the app doesn't have the Authorization Code grant enabled | Okta admin → Applications → *your-app* → Assignments tab and General → Grant Types |
| 401 after browser flow with a valid-looking JWT | `mcp.authentication.audiences` doesn't include the `aud` claim Okta issued, or the `issuer` value has a trailing-slash mismatch | Decode the JWT at `jwt.io`; compare `iss` to `${OKTA_ISSUER}` (**no** trailing slash) and `aud` to `${OKTA_AUDIENCE}`. Adjust the authz server's audience in the Okta admin console if needed. |
| Inspector shows "fetch failed" or `unable to verify the first certificate` | Inspector's Node process rejected the self-signed gateway cert | Restart Inspector with `NODE_TLS_REJECT_UNAUTHORIZED=0` (Step 9) |
| Browser shows `ERR_CERT_AUTHORITY_INVALID` and the OAuth flow stops | Browser hasn't accepted the self-signed cert yet | Visit `https://mcp-okta.glootest.com/.well-known/oauth-protected-resource/mcp` and click through the warning |
| `mcp-okta.glootest.com` doesn't resolve | `/etc/hosts` entry missing or DNS cache stale | Re-run the `echo "$GATEWAY_IP $GATEWAY_HOST" \| sudo tee -a /etc/hosts` step; on macOS flush DNS |

Useful commands:

```bash
# Confirm the discovery endpoints respond from the public URL
curl -sk "https://${GATEWAY_HOST}/.well-known/oauth-protected-resource/mcp" | jq .
curl -sk "https://${GATEWAY_HOST}/.well-known/oauth-authorization-server/mcp" | jq .

# Verify registration_endpoint points at the gateway, not Okta
curl -sk "https://${GATEWAY_HOST}/.well-known/oauth-authorization-server/mcp" | jq .registration_endpoint

# Sanity-check Okta's own discovery doc for comparison
curl -s "${OKTA_ISSUER}/.well-known/openid-configuration" | jq .

# Tail gateway logs during an Inspector connection attempt
kubectl logs -n agentgateway-system deployment/agentgateway-proxy -f
```

---

## Cleanup

Fully revert to the Lab 001 baseline. Run these in order — the helm revert is **required**, not optional. Skipping it leaves the controller running with `tokenExchange.enabled` and a postgres URL pointing at a deleted DB; a future re-run of this lab will hit `relation "oauth_flow_states" does not exist` because the controller pod never restarts to migrate against a fresh postgres.

```bash
# 1. Delete lab-specific resources
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-okta-eager --ignore-not-found
kubectl delete httproute -n agentgateway-system mcp-route oauth-issuer --ignore-not-found
kubectl delete agentgatewaybackend -n agentgateway-system mcp-backend okta-jwks --ignore-not-found
kubectl delete deployment -n agentgateway-system mcp-server --ignore-not-found
kubectl delete service -n agentgateway-system mcp-server --ignore-not-found
kubectl delete secret -n agentgateway-system elicitation-secret mcp-okta-tls --ignore-not-found

# 2. Roll back the EnterpriseAgentgatewayParameters env vars added in Step 4.
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

# 4. Re-run Lab 001's helm upgrade to drop tokenExchange + KGW_OAUTH_ISSUER_CONFIG.
#    This restarts the controller and clears its stale postgres connection.
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
sudo sed -i '' "/${GATEWAY_HOST}/d" /etc/hosts   # macOS; on Linux drop the empty '' arg
```

If helm reports the upgrade as a no-op (identical revision), force a controller restart manually so any stale DB state is cleared:

```bash
kubectl rollout restart -n agentgateway-system deployment/enterprise-agentgateway
kubectl rollout status   -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
```
