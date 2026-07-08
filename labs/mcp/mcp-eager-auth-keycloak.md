# MCP Authentication with Keycloak via Eager OAuth

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

### Keycloak requirements

You do **not** need an external IdP account or dashboard for this lab. It deploys its own Keycloak **in-cluster** (Step 0), importing a realm, a confidential `mcp-gateway` client, its audience mapper, and a test user declaratively on first boot. All the values below are fixed by that import — you set them as shell variables in Step 1; there is no admin UI to click through.

| Variable | Description |
|---|---|
| `KC_REALM` | Realm imported by this lab — `mcp-enterprise` |
| `KC_CLIENT_ID` | Confidential client id — `mcp-gateway` |
| `KC_CLIENT_SECRET` | Client secret baked into the realm import — `mcpGatewayWorkshopSecret` |
| `KC_AUDIENCE` | Audience the client's mapper stamps on issued tokens — `mcp-gateway` — must match the `aud` claim |
| `KC_GATEWAY_HOST` | Public hostname for the gateway (no scheme) — this lab uses `mcp-keycloak.glootest.com` |
| `KC_IP` | LoadBalancer address of the in-cluster Keycloak Service (captured in Step 0) |
| `KC_ISSUER` | `http://<KC_IP>:8080/realms/<KC_REALM>` — **no trailing slash** (Keycloak emits `iss` without one) |

The `mcp-gateway` client is imported with `redirectUris: ["*"]`, so there is **no** per-callback registration to do — the two-callback gotcha some IdPs impose does not apply to Keycloak.

### Required tools

- `kubectl` and `helm`
- `openssl` (for the self-signed gateway cert)
- Node 18+ (for MCP Inspector in Step 9)
- `jq` for inspecting JSON responses
- A way to resolve `mcp-keycloak.glootest.com` from your workstation to the gateway LoadBalancer — either a real DNS record (production-style clusters) or a local `/etc/hosts` entry (KinD/minikube/local dev clusters; requires sudo). Note the `/etc/hosts` entry maps the **gateway** host only; Keycloak is reached directly at its LoadBalancer IP.

---

## Lab Objectives

- Stand up the eager-OAuth feature so the gateway acts as the OAuth Authorization Server visible to MCP clients
- Use a single pre-registered Keycloak `client_id` / `client_secret` for all MCP clients (no per-client DCR churn)
- Broker the Keycloak authorization code flow through the gateway (`/oauth-issuer/...`)
- Validate Keycloak-issued JWTs at the MCP backend against Keycloak JWKS
- Terminate TLS on `agentgateway-proxy` with a self-signed cert for `mcp-keycloak.glootest.com`
- Test end-to-end with MCP Inspector against an `mcp-server-everything` test server

---

## Background

Why eager OAuth with Keycloak?

Keycloak supports Dynamic Client Registration (RFC 7591) natively, but it has practical drawbacks for MCP at scale:

- Every DCR call creates a new client entry in the Keycloak realm. With many MCP clients (Claude Code, Cursor, VS Code, ChatGPT, Inspector, …) per developer, the realm fills up quickly.
- DCR requires an initial access token or an open registration policy that some orgs don't want to expose to gateway components.
- Operationally, most teams want a single "MCP Gateway" client registered in Keycloak, not one per client.

**Eager OAuth** with pre-registered client_ids fixes this. agentgateway becomes the OAuth Authorization Server that MCP clients see, and Keycloak sits downstream of the gateway. MCP clients DCR against the gateway and get a single pre-registered Keycloak `client_id` / `client_secret` pair — no realm churn, no registration API needed at runtime.

Keycloak runs in-cluster over plain HTTP and is reached by the browser, the controller, and the proxy at one shared LoadBalancer address, so the issuer it derives (`http://<KC_IP>:8080/realms/<realm>`) stays consistent across every party in the flow.

```
┌──────────────┐   1. discovery + DCR   ┌─────────────────┐  3. authorize/token  ┌──────────┐
│  MCP client  │ ──────────────────────▶│  agentgateway   │ ───────────────────▶ │ Keycloak │
│ (Inspector,  │ ◀──────────────────────│ (OAuth issuer @ │ ◀─────────────────── │          │
│  Claude, …)  │   2. issuer metadata   │ /oauth-issuer)  │  4. authorization    │          │
└──────────────┘     pointing at GW     └─────────────────┘     code → token     └──────────┘
                                                │
                                                │  5. validate token, forward
                                                ▼
                                         ┌────────────────┐
                                         │   MCP server   │
                                         │ (test target)  │
                                         └────────────────┘
```

Three things make this work:

1. **Issuer metadata is served by the gateway** (`/.well-known/oauth-authorization-server/...`), so `registration_endpoint` points at the gateway, not Keycloak.
2. **The gateway implements `/oauth-issuer/register`** and returns the pre-registered Keycloak client_id from the issuer config's `client_config.clients`.
3. **The gateway brokers the authorization code flow** to Keycloak using the issuer config's `downstream_server`. The browser still opens to Keycloak's hosted login; the resulting JWT is what reaches the MCP backend.

---

## Custom Gateway Features Covered

- **OAuth 2.0 Authorization Server**: agentgateway acts as the AS at `/oauth-issuer/...`. MCP clients see the gateway as their OAuth provider, not Keycloak.
- **Pre-registered "fake DCR"**: `/oauth-issuer/register` returns the Keycloak `client_id`/`client_secret` pair you provide. MCP clients believe they did Dynamic Client Registration; in reality they got pre-registered credentials.
- **Authorization code flow brokering**: the gateway proxies the authorization code flow downstream to Keycloak (`authorize`, callback handling, `token` exchange).
- **JWT validation**: Keycloak-issued JWTs are validated at the MCP backend against Keycloak's JWKS (`/realms/<realm>/protocol/openid-connect/certs`).
- **Frontend TLS termination**: the existing `agentgateway-proxy` Gateway gains an HTTPS listener on port 443 alongside lab 001's HTTP listener on 8080.

---

## Step 0 — Deploy Keycloak

This lab runs its own Keycloak in-cluster (no external IdP account). The realm,
the confidential `mcp-gateway` client, its audience mapper, and a test user are
imported declaratively on first boot.

```bash
kubectl apply -k lib/keycloak/mcp-enterprise/
kubectl rollout status -n keycloak deployment/keycloak --timeout=180s
```

Capture the LoadBalancer address assigned to the Keycloak Service:

```bash
export KC_IP=$(kubectl get svc -n keycloak keycloak \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}')
echo "$KC_IP"
```

Confirm the realm is up and note the issuer Keycloak advertises:

```bash
curl -s "http://${KC_IP}:8080/realms/mcp-enterprise/.well-known/openid-configuration" | jq '.issuer, .authorization_endpoint, .token_endpoint, .jwks_uri'
```

Expected: `issuer` is `http://${KC_IP}:8080/realms/mcp-enterprise` (no trailing
slash), and the authorize/token/jwks endpoints sit under
`/realms/mcp-enterprise/protocol/openid-connect/`.

> **Reachability requirement.** `KC_IP` must be reachable both from your
> workstation browser AND from in-cluster pods (controller + proxy). This is the
> standard workshop setup (KinD+metallb, cloud LBs). If pods cannot reach the LB
> IP, see the Topology-B fallback in Troubleshooting.

---

## Step 1 — Set Environment Variables and DNS

Set these values in your shell so child processes (`kubectl`, `helm`) inherit them. Every value below is fixed by the realm import from Step 0, so you can run the block as-is.

```bash
export KC_REALM=mcp-enterprise
export KC_CLIENT_ID=mcp-gateway
export KC_CLIENT_SECRET=mcpGatewayWorkshopSecret
export KC_AUDIENCE=mcp-gateway
export KC_GATEWAY_HOST=mcp-keycloak.glootest.com

# KC_IP was captured in Step 0; derive the issuer (NO trailing slash):
export KC_ISSUER="http://${KC_IP}:8080/realms/${KC_REALM}"

export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY   # from Lab 001
```

Notes on these values:

- `KC_ISSUER` **must not** end with a trailing slash. The `iss` claim Keycloak puts in JWTs is `http://<KC_IP>:8080/realms/<realm>` (no trailing `/`), and the MCP authentication policy compares the two literally.
- `KC_IP` was captured in Step 0. If you opened a fresh shell, re-run the `kubectl get svc -n keycloak keycloak ...` command from Step 0 before deriving `KC_ISSUER`.
- `KC_AUDIENCE` is stamped on issued tokens by the `mcp-gateway` client's audience mapper in the realm import; it must match the `aud` claim the MCP authentication policy checks.

### Map the gateway hostname to the LoadBalancer IP

Find the LoadBalancer IP/hostname assigned to `agentgateway-proxy` from Lab 001:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "$GATEWAY_IP"
```

Add an `/etc/hosts` entry so both your terminal and your browser resolve `mcp-keycloak.glootest.com` to the gateway:

```bash
echo "$GATEWAY_IP $KC_GATEWAY_HOST" | sudo tee -a /etc/hosts
```

---

## Step 2 — Create a Self-Signed TLS Cert and Add an HTTPS Listener

OAuth requires HTTPS for everything that is not `localhost`, since the browser will redirect to Keycloak and back. This step creates a self-signed cert for `mcp-keycloak.glootest.com` and adds a port 443 HTTPS listener to the existing `agentgateway-proxy` Gateway alongside Lab 001's port 8080 HTTP listener.

Create a root certificate for the `glootest.com` domain and a leaf cert signed by that root:

```bash
mkdir -p example_certs
openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 \
  -subj '/O=Solo.io/CN=glootest.com' \
  -keyout example_certs/glootest.com.key \
  -out    example_certs/glootest.com.crt

openssl req -out example_certs/gateway.csr -newkey rsa:2048 -nodes \
  -keyout example_certs/gateway.key \
  -subj  "/CN=mcp-keycloak.glootest.com/O=Solo.io"

openssl x509 -req -sha256 -days 365 \
  -CA    example_certs/glootest.com.crt \
  -CAkey example_certs/glootest.com.key \
  -set_serial 0 \
  -in    example_certs/gateway.csr \
  -out   example_certs/gateway.crt \
  -extfile <(printf "subjectAltName=DNS:mcp-keycloak.glootest.com")
```

Store the leaf cert in a Kubernetes TLS secret:

```bash
kubectl create secret tls -n agentgateway-system mcp-keycloak-tls \
  --key  example_certs/gateway.key \
  --cert example_certs/gateway.crt \
  --dry-run=client -oyaml | kubectl apply -f -
```

Update the `agentgateway-proxy` Gateway to expose both listeners — the original HTTP on 8080 (preserved so other labs continue to work) and a new HTTPS listener on 443. Patch it rather than applying a full replacement manifest, so the `spec.infrastructure.parametersRef` from Lab 001 (which carries the proxy's replica count, resources, and, after Step 4, its `STS_URI`/`STS_AUTH_TOKEN` env vars) stays intact:

```bash
kubectl patch gateway -n agentgateway-system agentgateway-proxy --type=merge -p='
spec:
  infrastructure:
    parametersRef:
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayParameters
      name: agentgateway-config
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
      hostname: mcp-keycloak.glootest.com
      tls:
        mode: Terminate
        certificateRefs:
          - name: mcp-keycloak-tls
            kind: Secret
      allowedRoutes:
        namespaces:
          from: All
'
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

Re-run `helm upgrade` to enable the eager-OAuth feature in the controller, point it at Postgres + Keycloak JWKS, and inject the OAuth issuer config.

`--reuse-values` is required here. Without it, this upgrade merges the `-f` block below onto the chart's defaults instead of the release's current values, so any customization outside this lab reverts silently — including the `GatewayClass` `parametersRef` that enables the shared `ext-cache`/`ext-auth`/rate-limiter/WAF deployments for every Gateway of that class. This lab doesn't need to touch that setting, so it's left out of the values block; `--reuse-values` carries the existing value forward.

```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --reuse-values \
  --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
  -f -<<EOF
tokenExchange:
  enabled: true
  issuer: "enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777"
  tokenExpiration: 24h
  subjectValidator:
    validatorType: remote
    remoteConfig:
      url: "${KC_ISSUER}/protocol/openid-connect/certs"
  apiValidator:
    validatorType: remote
    remoteConfig:
      url: "${KC_ISSUER}/protocol/openid-connect/certs"
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
          "base_url": "https://${KC_GATEWAY_HOST}/oauth-issuer"
        },
        "client_config": {
          "clients": {
            "${KC_CLIENT_ID}": "${KC_CLIENT_SECRET}"
          }
        },
        "downstream_server": {
          "name": "keycloak",
          "client_id": "${KC_CLIENT_ID}",
          "client_secret": "${KC_CLIENT_SECRET}",
          "authorize_url": "${KC_ISSUER}/protocol/openid-connect/auth",
          "token_url": "${KC_ISSUER}/protocol/openid-connect/token",
          "redirect_uri": "https://${KC_GATEWAY_HOST}/oauth-issuer/callback/downstream",
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
| `downstream_server` | Credentials and URLs for the gateway to talk to Keycloak during the authorization code flow; `redirect_uri` must match an entry in the Keycloak client's "Valid redirect URIs" |

Wait for the controller and proxy pods to restart cleanly:

```bash
kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
kubectl rollout status -n agentgateway-system deployment/agentgateway-proxy --timeout=180s
```

> **⚠ Audience handling.** Keycloak does not add a useful `aud` by default (it
> emits `aud: account`). This lab's `mcp-gateway` client carries an
> `oidc-audience-mapper` (in `mcp-enterprise.json`) that injects
> `aud: mcp-gateway` into the **access token**, so audience validation in Step 8
> works with no `/authorize` audience-param hack. If a login
> completes but the JWT's `aud` is `account` instead of `mcp-gateway`, the mapper
> didn't import or the wrong client is in use — see Troubleshooting.

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
    - mcp-keycloak.glootest.com
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
| `keycloak-jwks` | AgentgatewayBackend | Static HTTP backend pointing at in-cluster Keycloak for JWKS lookups |
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
  app_id: "keycloak"
  authorize_url: "${KC_ISSUER}/protocol/openid-connect/auth"
  access_token_url: "${KC_ISSUER}/protocol/openid-connect/token"
  client_id: "${KC_CLIENT_ID}"
  client_secret: "${KC_CLIENT_SECRET}"
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
    - mcp-keycloak.glootest.com
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
  name: keycloak-jwks
  namespace: agentgateway-system
spec:
  static:
    host: ${KC_IP}
    port: 8080
EOF
```

> The in-cluster Keycloak serves plain HTTP on port 8080, so this backend has no TLS policy (a public IdP reached over 443 would need one).

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
| `issuer` | Keycloak is the JWT issuer (`${KC_ISSUER}` — **no** trailing slash) |
| `jwks` | Points at the `keycloak-jwks` backend created in Step 7. **`jwksPath` must be written without a leading slash** (`realms/${KC_REALM}/protocol/openid-connect/certs`) — the controller appends `/` between the backend URL and `jwksPath`, so a leading slash produces `http://$KC_IP//realms/...`, which returns 404. The controller log signature is `failed resolving jwks ... 404` and the policy goes `PartiallyValid`; `/mcp` then bypasses auth entirely (`GET /mcp` returns 406 instead of 401). |
| `audiences` | `mcp-gateway`, injected by the client's audience mapper |
| `resourceMetadata.agentgateway.dev/issuer-proxy` | Tells the gateway to serve its own AS metadata (from the in-cluster eager-OAuth issuer at `:7777/oauth-issuer`) when an MCP client fetches `.well-known/oauth-authorization-server/mcp`. Without this, the gateway would proxy Keycloak's metadata directly. |
| `resourceMetadata.authorizationServers` / `resource` | What shows up in the protected-resource discovery document for clients |

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-keycloak-eager
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
        issuer: ${KC_ISSUER}
        audiences:
          - ${KC_AUDIENCE}
        jwks:
          backendRef:
            name: keycloak-jwks
            kind: AgentgatewayBackend
            group: agentgateway.dev
          jwksPath: realms/${KC_REALM}/protocol/openid-connect/certs
        resourceMetadata:
          agentgateway.dev/issuer-proxy: http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/oauth-issuer
          authorizationServers:
            - https://${KC_GATEWAY_HOST}/mcp
          resource: https://${KC_GATEWAY_HOST}/mcp
EOF
```

---

## Step 9 — Test with MCP Inspector

### Trust the self-signed cert in your browser

Before launching Inspector, hit the gateway in your browser once to accept the self-signed cert warning:

```
https://mcp-keycloak.glootest.com/.well-known/oauth-protected-resource/mcp
```

Click "Advanced → proceed" (Chrome) / "Accept the risk" (Firefox). You should see the JSON discovery document. Without this step, the browser blocks the OAuth redirect chain silently.

![Chrome self-signed cert warning for mcp-keycloak.glootest.com](../../images/keycloak/01-cert-warning.png)

### Launch Inspector locally

The Inspector backend (Node) opens HTTP connections to the gateway and won't accept self-signed certs by default. Disable Node's TLS verification for the Inspector process:

```bash
NODE_TLS_REJECT_UNAUTHORIZED=0 npx @modelcontextprotocol/inspector
```

Inspector binds to `http://localhost:6274` and prints a session token in the terminal. Open the printed URL in a browser.

### Configure the connection

In the Inspector UI:

- **Transport type:** `Streamable HTTP`
- **Server URL:** `https://mcp-keycloak.glootest.com/mcp` (replace any URL left over from a previous session)
- Click **Connect**.

![Inspector configured with Streamable HTTP transport and the gateway's /mcp URL](../../images/keycloak/02-inspector-config.png)

### Walk through the OAuth flow

Inspector follows the protected-resource discovery automatically. You should see:

1. A redirect to the **Keycloak** login page at `http://${KC_IP}:8080` (plain HTTP — expected for this lab). **Verify the URL bar shows the Keycloak host `${KC_IP}:8080`, not the gateway hostname** — this confirms the eager-OAuth issuer correctly delegated downstream. Sign in as `mcp-user` / `mcp-user`.

   ![Keycloak "Sign in to mcp-enterprise" login page, reached via the LoadBalancer IP](../../images/keycloak/03-keycloak-login.png)

2. After completing the Keycloak login, a redirect back to Inspector's local callback.
3. Inspector status flips to **Connected**, with a "Successfully authenticated with OAuth" toast.

   ![Inspector showing Connected status and the mcp-servers/everything server](../../images/keycloak/04-inspector-connected.png)

### Confirm tools are reachable

In the Inspector left panel, click **Tools → List Tools**. The `mcp-server-everything` tools should render (`echo`, `add`, `printEnv`, `longRunningOperation`, `getTinyImage`, …). Run one (`echo` with `{"message":"hi"}`) — you should get a tool result, not a 401.

![Inspector Tools tab listing the mcp-server-everything tool set](../../images/keycloak/05-tools-list.png)

![Echo tool run showing "Tool Result: Success" with the echoed message](../../images/keycloak/06-echo-tool-success.png)

### What proves what

| Observation in Inspector | What it proves |
|---|---|
| Redirect lands on the Keycloak host `${KC_IP}:8080` | Eager-OAuth issuer is serving its own AS metadata; `registration_endpoint` was rewritten to point at the gateway |
| Login completes and Inspector shows "Connected" | The pre-registered `client_id`/`client_secret` from `client_config.clients` matched the Keycloak `mcp-gateway` client — fake-DCR worked end-to-end |
| Tool list renders without 401 | Keycloak-issued JWT validated against Keycloak JWKS at the MCP backend; `mcp.authentication` is configured correctly |
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

## Step 10 — Test with Claude Code

This step uses Claude Code as the MCP client in place of MCP Inspector. Claude Code follows the same MCP auth spec: it discovers the gateway's OAuth AS metadata, performs Dynamic Client Registration, opens a browser for login, and stores the resulting token. The result is a real agent harness authenticated through the eager-OAuth flow.

### Trust the self-signed cert

This lab uses a self-signed certificate. If your gateway is using a certificate from a trusted CA, skip this section — `NODE_TLS_REJECT_UNAUTHORIZED=0` is not needed.

Claude Code is a Node.js process and won't accept the self-signed gateway cert by default. Prefix the launch command with `NODE_TLS_REJECT_UNAUTHORIZED=0` as shown in the next section — do **not** add it to your shell rc file, as it disables TLS verification for all Node processes in that shell.

### Register the MCP server with Claude Code

Add the gateway's MCP endpoint to Claude Code's local config:

```bash
claude mcp add mcp-keycloak-gateway --transport http https://mcp-keycloak.glootest.com/mcp
```

Verify it was added:

```bash
claude mcp list
```

Expected output includes a line for the new server, with a health-check status that will read `Needs authentication` until the OAuth flow below completes:

```
mcp-keycloak-gateway: https://mcp-keycloak.glootest.com/mcp (HTTP) - ! Needs authentication
```

> **Quick auth check.** `claude mcp login <name>` runs the same discovery → eager-OAuth → Keycloak → token exchange path on its own, without opening a chat session:
>
> ```bash
> NODE_TLS_REJECT_UNAUTHORIZED=0 claude mcp login mcp-keycloak-gateway
> ```
>
> It opens the Keycloak login in your browser and reports `Authenticated with "mcp-keycloak-gateway"` on success. `claude mcp list` should then show `✔ Connected`.

### Launch Claude Code and walk through the OAuth flow

```bash
NODE_TLS_REJECT_UNAUTHORIZED=0 claude
```

On the first prompt that triggers MCP tool discovery, Claude Code initiates the OAuth flow automatically:

1. Claude Code fetches `/.well-known/oauth-protected-resource/mcp` to discover the authorization server.
2. It fetches `/.well-known/oauth-authorization-server/mcp` from the gateway. **Verify the `registration_endpoint` shows the gateway hostname** (`https://mcp-keycloak.glootest.com/oauth-issuer/register`), not the Keycloak host — this confirms the eager-OAuth issuer is serving its own AS metadata.
3. Claude Code POSTs to `/oauth-issuer/register` and receives the pre-registered Keycloak `client_id`.
4. A browser window opens to the **Keycloak login page** at `http://${KC_IP}:8080` (plain HTTP — expected for this lab). **Verify the URL bar shows the Keycloak host `${KC_IP}:8080`**, not the gateway hostname — this confirms the eager-OAuth issuer correctly delegated downstream.
5. Complete the Keycloak login as `mcp-user` / `mcp-user`.
6. The browser redirects back; Claude Code captures the authorization code via a local PKCE callback server and exchanges it for a token.
7. Claude Code resumes. The token is stored in Claude Code's local config and reused on subsequent runs.

### Confirm tools are reachable

At the Claude Code prompt, ask it to use a tool from the MCP server:

```
Use the echo tool from mcp-keycloak-gateway to echo "hello from agentgateway"
```

Expected: Claude Code calls the `echo` tool and returns the echoed message without a 401 error. You should see the tool invocation appear in the Claude Code output (tool name, input, result).

![claude mcp list showing mcp-keycloak-gateway as Connected after OAuth](../../images/keycloak/07-claude-code-mcp-connected.png)

### What proves what

| Observation in Claude Code | What it proves |
|---|---|
| Browser opens to the Keycloak host `${KC_IP}:8080`, not the gateway | Eager-OAuth AS is serving its own AS metadata; `registration_endpoint` was rewritten to point at the gateway |
| Login completes and Claude Code resumes tool calls | Pre-registered `client_id`/`client_secret` from `client_config.clients` matched the Keycloak `mcp-gateway` client — fake-DCR worked end-to-end |
| Tool call returns a result without a 401 | Keycloak-issued JWT validated against Keycloak JWKS at the MCP backend; `mcp.authentication` is configured correctly |
| Subsequent `claude` invocations skip the browser | Token stored in Claude Code's local config and reused automatically |

### Cleanup

Remove the MCP server from Claude Code's local config:

```bash
claude mcp remove mcp-keycloak-gateway
```

The Kubernetes resources for this lab are removed in the [Cleanup](#cleanup) section below.

---

## Troubleshooting

If MCP Inspector behaves unexpectedly, this table covers the common breakage modes for an eager-OAuth + Keycloak setup.

| Symptom in Inspector | Likely Cause | Where to Look |
|---|---|---|
| `/.well-known/oauth-authorization-server/mcp` returns Keycloak's own metadata (registration endpoint points at Keycloak) | The `agentgateway.dev/issuer-proxy` annotation under `resourceMetadata` is missing, or the `oauth-issuer` HTTPRoute (Step 6) is misrouted | Step 8 — confirm `agentgateway.dev/issuer-proxy` is set; Step 6 — `kubectl get httproute -n agentgateway-system oauth-issuer` |
| `/oauth-issuer/register` returns 404 or 501 | Step 5 helm upgrade did not apply `tokenExchange.enabled` + the issuer config, or the `/oauth-issuer` HTTPRoute (Step 6) is missing | `kubectl get httproute -n agentgateway-system oauth-issuer`; gateway pod logs |
| `GET /mcp` without a token returns **406** instead of 401, and `/.well-known/oauth-*-resource/mcp` returns 404 | The MCP authentication policy is `PartiallyValid` because the controller can't fetch JWKS. Most often caused by a leading slash on `jwksPath` (`/realms/...`), which produces `http://$KC_IP//realms/...` (404 from Keycloak) | `kubectl get enterpriseagentgatewaypolicy -n agentgateway-system mcp-keycloak-eager -o jsonpath='{.status.ancestors[*].conditions[*].message}'` should say `Policy accepted Attached to all targets`. Controller logs: `kubectl logs -n agentgateway-system deployment/enterprise-agentgateway \| grep jwks`. Fix per Step 8 — `jwksPath: realms/${KC_REALM}/protocol/openid-connect/certs` (no leading slash) |
| Controller pod CrashLoopBackOff with `error creating actor validator: unsupported validator type:` | Step 5 helm values are missing `tokenExchange.actorValidator` (and/or `apiValidator`) — all three validators are required at boot even though only the eager-OAuth issuer is being used | Re-run Step 5 with the validator block matching this lab |
| Login completes but tools still 401, or the `agentgateway-proxy` Deployment shows fewer replicas / missing resource requests than expected | The Gateway was updated with a full `kubectl apply -f -` manifest instead of the merge patch in Step 2, wiping `spec.infrastructure.parametersRef` and with it the proxy's `STS_URI`/`STS_AUTH_TOKEN` env vars from Step 4 | `kubectl get gateway -n agentgateway-system agentgateway-proxy -o jsonpath='{.spec.infrastructure}'` — empty means it's gone. `kubectl get deployment -n agentgateway-system agentgateway-proxy -o jsonpath='{.spec.template.spec.containers[0].env}' \| jq .` — check `STS_URI`/`STS_AUTH_TOKEN` are present. Restore it per Step 2 |
| `ext-cache-enterprise-agentgateway` (or ext-auth/rate-limiter/WAF) deployment disappears after Step 5 or the Cleanup helm upgrade | The upgrade ran without `--reuse-values`, so it merged onto chart defaults instead of the live release and reassigned the GatewayClass's `parametersRef` away from the shared-extensions config | `kubectl get gatewayclass enterprise-agentgateway -o jsonpath='{.spec.parametersRef.name}'`; re-run the helm upgrade with `--reuse-values` and without an explicit `gatewayClassParametersRefs` override |
| Inspector errors immediately (no Keycloak redirect) and controller logs show `failed to start auth flow ... secret not found: agentgateway-system/elicitation-secret` | The `elicitation-secret` Secret from Step 7 wasn't created or is in the wrong namespace | `kubectl get secret -n agentgateway-system elicitation-secret`; recreate per Step 7 |
| 401 after the browser flow with a valid-looking JWT / issuer mismatch | The `iss` in the JWT doesn't match the policy `issuer`. This happens when authorize, token, issuer, and JWKS are not all using the **same** Keycloak address (e.g. authorize on the LB IP but token on svc DNS) | Decode the access token (`jwt.io` or `... \| cut -d. -f2 \| base64 -d \| jq`) and compare `iss` to `${KC_ISSUER}`. Keycloak fixes `iss` from the address the token endpoint was called on — make authorize/token/issuer/JWKS all use one address (see the Topology-B row) |
| Pods can't reach the LB IP (token exchange or JWKS lookups fail from in-cluster) | The LB IP is browser-reachable but not pod-reachable, so the controller/proxy can't hit Keycloak | **Topology-B fallback:** set `token_url`, the subject/api validator URLs, the `keycloak-jwks` backend `host`, and the policy `issuer` to the in-cluster svc DNS `http://keycloak.keycloak.svc.cluster.local:8080/realms/${KC_REALM}...`; keep `authorize_url` on the LB IP so the browser can still reach it. Because Keycloak fixes `iss` at the **token** endpoint, the token's `iss` becomes the svc-DNS host — so the policy `issuer` must be the svc-DNS host to match |
| Login completes but the JWT's `aud` is `account`, not `mcp-gateway` (401) | The `oidc-audience-mapper` didn't import, or a different client than `mcp-gateway` was used | Verify the mapper exists on the `mcp-gateway` client / that the realm import (`mcp-enterprise.json`) applied: `kubectl exec` a curl of a client-credentials token and decode its `aud`, or re-run Step 0 |
| Keycloak login page loads over plain **HTTP** (`http://${KC_IP}:8080`) | Expected — the in-cluster Keycloak runs with `KC_HTTP_ENABLED=true` and the realm's `sslRequired: none`. Only the gateway front-door (`mcp-keycloak.glootest.com`) is HTTPS | No action needed; this is by design for the lab |
| Inspector shows "fetch failed" or `unable to verify the first certificate` | Inspector's Node process rejected the self-signed gateway cert | Restart Inspector with `NODE_TLS_REJECT_UNAUTHORIZED=0` (Step 9) |
| Inspector loops on connect with no Keycloak redirect; browser DevTools console (F12) shows `Access to fetch at '.../.well-known/oauth-*-resource/mcp' has been blocked by CORS policy` or `mcp-protocol-version is not allowed by Access-Control-Allow-Headers` | OAuth metadata discovery runs in the **browser** (Inspector UI), not through Inspector's `localhost:6277` proxy. Inspector sends `mcp-protocol-version` on the preflight, but agentgateway's internal handler hardcodes `Access-Control-Allow-Headers: content-type` and rejects it | The Step 8 `mcp-route` HTTPRoute must attach a Gateway API `CORS` filter to both `/.well-known/oauth-*/mcp` rules that allows `mcp-protocol-version` (and `Authorization`). Confirm with `kubectl get httproute -n agentgateway-system mcp-route -o yaml \| grep -A6 'type: CORS'` |
| Browser shows `ERR_CERT_AUTHORITY_INVALID` and the OAuth flow stops | Browser hasn't accepted the self-signed cert yet | Visit `https://mcp-keycloak.glootest.com/.well-known/oauth-protected-resource/mcp` and click through the warning |
| `mcp-keycloak.glootest.com` doesn't resolve | `/etc/hosts` entry missing or DNS cache stale | Re-run the `echo "$GATEWAY_IP $KC_GATEWAY_HOST" \| sudo tee -a /etc/hosts` step; on macOS flush DNS |
| Claude Code (Step 10) fails to connect with an SSL error or `unable to verify the first certificate` | `NODE_TLS_REJECT_UNAUTHORIZED` not set for the Claude Code process | Launch with `NODE_TLS_REJECT_UNAUTHORIZED=0 claude`; do **not** add this to your shell rc |

Useful commands:

```bash
# Confirm the discovery endpoints respond from the public URL
curl -sk "https://${KC_GATEWAY_HOST}/.well-known/oauth-protected-resource/mcp" | jq .
curl -sk "https://${KC_GATEWAY_HOST}/.well-known/oauth-authorization-server/mcp" | jq .

# Verify registration_endpoint points at the gateway, not Keycloak
curl -sk "https://${KC_GATEWAY_HOST}/.well-known/oauth-authorization-server/mcp" | jq .registration_endpoint

# Sanity-check Keycloak's own discovery doc for comparison
curl -s "http://${KC_IP}:8080/realms/${KC_REALM}/.well-known/openid-configuration" | jq .

# Tail gateway logs during an Inspector connection attempt
kubectl logs -n agentgateway-system deployment/agentgateway-proxy -f
```

---

## Cleanup

Fully revert to the Lab 001 baseline. Run these in order — the helm revert is **required**, not optional. Skipping it leaves the controller running with `tokenExchange.enabled` and a postgres URL pointing at a deleted DB; a future re-run of this lab will hit `relation "oauth_flow_states" does not exist` because the controller pod never restarts to migrate against a fresh postgres.

```bash
# 1. Delete lab-specific resources
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-keycloak-eager --ignore-not-found
kubectl delete httproute -n agentgateway-system mcp-route oauth-issuer --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-backend --ignore-not-found
kubectl delete agentgatewaybackend -n agentgateway-system keycloak-jwks --ignore-not-found
kubectl delete deployment -n agentgateway-system mcp-server --ignore-not-found
kubectl delete service -n agentgateway-system mcp-server --ignore-not-found
kubectl delete secret -n agentgateway-system elicitation-secret mcp-keycloak-tls --ignore-not-found

# 2. Roll back the EnterpriseAgentgatewayParameters env vars added in Step 4.
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type=json \
  -p='[{"op":"remove","path":"/spec/env"}]' || true

# 3. Restore the Lab 001 Gateway (HTTP-only on port 8080, no HTTPS listener).
#    Merge-patch, not a full apply — see the note in Step 2.
kubectl patch gateway -n agentgateway-system agentgateway-proxy --type=merge -p='
spec:
  infrastructure:
    parametersRef:
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayParameters
      name: agentgateway-config
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
'

# 4. Re-run Lab 001's helm upgrade to drop tokenExchange + KGW_OAUTH_ISSUER_CONFIG.
#    This restarts the controller and clears its stale postgres connection.
#    Re-detect ENTERPRISE_AGW_VERSION in case this cleanup runs in a fresh shell.
#    --reuse-values here too — see the note in Step 5.
export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --reuse-values \
  --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY

kubectl rollout status -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s

# 5. Drop the Postgres namespace
kubectl delete namespace postgres --ignore-not-found

# 6. Remove local cert files and the /etc/hosts entry
rm -rf example_certs
sudo sed -i '' "/${KC_GATEWAY_HOST}/d" /etc/hosts   # macOS; on Linux drop the empty '' arg

# 7. Remove the in-cluster Keycloak stack
kubectl delete -k lib/keycloak/mcp-enterprise/ --ignore-not-found
```

If helm reports the upgrade as a no-op (identical revision), force a controller restart manually so any stale DB state is cleared:

```bash
kubectl rollout restart -n agentgateway-system deployment/enterprise-agentgateway
kubectl rollout status   -n agentgateway-system deployment/enterprise-agentgateway --timeout=180s
```
