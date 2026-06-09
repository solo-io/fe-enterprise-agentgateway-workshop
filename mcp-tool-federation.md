# MCP Tool Federation

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

Additional requirements:

- **FRED API key** (required — the `fred` pod won't start without it). Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html
- **BLS API key** (optional — raises rate limits). Get a free key at https://data.bls.gov/registrationEngine/
- `openssl`, `base64`, and `bash` on your local machine (used by the `lib/jwt/generate-jwt.sh` helper)
- `npx` available locally (for MCP Inspector)

## Lab Objectives

- Federate four independent MCP servers behind a single `EnterpriseAgentgatewayBackend` using multiple `spec.mcp.targets`
- Understand tool-name prefixing for selector-based targets (`<service-name>-<port>_<tool-name>`)
- Demonstrate `failureMode: FailOpen` — one backend can fail without taking down the federated endpoint
- Apply a single JWT authentication policy that gates the entire union of tools
- Use **persona-based tool filtering** to expose a different slice of the federated tool surface per JWT claim — without changing the federation topology

## Overview

### Why federation?

The MCP labs you've worked through so far ([In-Cluster MCP](in-cluster-mcp.md), [Dynamic MCP](dynamic-mcp.md)) each route to a single backend. In a real platform, you'll have many MCP servers — domain-specific, language-specific, or owned by different teams — and consumers want **one endpoint** that aggregates them all, **one auth policy** that gates them all, and **one observability surface** that attributes traffic across them all.

A **multiplexed** (a.k.a. "Virtual MCP") backend does exactly that: one `EnterpriseAgentgatewayBackend` with multiple `spec.mcp.targets`. Clients open one MCP connection and see the union of every backend's tools. The gateway prefixes tool names so they don't collide and uses the prefix to route `tools/call` back to the right backend.

This lab federates four real-world financial-research MCP servers — written in different languages, owned by different sources — under one endpoint:

| Target | Image | Tools | What it does |
|---|---|---|---|
| `arxiv` | `ably7/airxiv-mcp:0.1.0` | 5 | arXiv academic paper search & PDF text extraction (Python) |
| `fred` | `ably7/fred-mcp-server:1.1.0` | 3 | Federal Reserve Economic Data — 800k+ time series (Node) |
| `secedgar` | `ably7/sec-edgar-mcp:1.0.8` | 21 | SEC EDGAR filings, XBRL financials, insider trading (Python) |
| `bls` | `ably7/mcp-bls:1.0.0` | 5 | U.S. Bureau of Labor Statistics — employment, CPI, wages (Node) |

Result: one MCP endpoint exposing ~34 tools across academic papers, monetary policy data, public-company filings, and U.S. labor statistics. Clients can't tell which tool comes from a Python pod and which comes from Node.

### Architecture

```
                      ┌──────────────────────────────────┐
                      │  EnterpriseAgentgatewayBackend   │
                      │  spec.mcp.targets:               │
                      │   - name: arxiv     → label sel  │
                      │   - name: fred      → label sel  │
                      │   - name: secedgar  → label sel  │
                      │   - name: bls       → label sel  │
                      │  failureMode: FailOpen           │
                      └──────────────┬───────────────────┘
                                     │
                              ┌──────┼──────┬──────────┐
                              │      │      │          │
                          ┌───▼──┐ ┌─▼───┐ ┌▼────────┐ ┌▼────┐
                          │arxiv │ │fred │ │sec-edgar│ │ bls │
                          │5 tls │ │3 tls│ │21 tools │ │5 tls│
                          └──────┘ └─────┘ └─────────┘ └─────┘
                                      (namespace: mcp)
```

### Tool-name prefixing

When a target uses a **label selector** (this lab), AgentGateway prefixes each discovered tool with `<service-name>-<port>_`. So:

| Backend service / port | Native tool | Federated tool name |
|---|---|---|
| `mcp-airxiv` / 80 | `search_arxiv` | `mcp-airxiv-80_search_arxiv` |
| `mcp-fred` / 80 | `fred_get_series` | `mcp-fred-80_fred_get_series` |
| `mcp-sec-edgar` / 80 | `get_cik_by_ticker` | `mcp-sec-edgar-80_get_cik_by_ticker` |
| `mcp-bls` / 80 | `get_unemployment` | `mcp-bls-80_get_unemployment` |

This naming becomes important in [Step 7](#step-7-persona-based-tool-filtering) when we write CEL expressions that match on tool prefix.

---

## Step 1: Namespace and API key secrets

```bash
kubectl create namespace mcp
```

Create the FRED API key secret (required):

```bash
kubectl create secret generic fred-api-key --namespace mcp \
  --from-literal=FRED_API_KEY=<your-fred-key>
```

Optionally create the BLS API key secret (raises rate limits; the pod runs without it too):

```bash
kubectl create secret generic bls-api-key --namespace mcp \
  --from-literal=BLS_API_KEY=<your-bls-key>
```

---

## Step 2: Deploy the four MCP servers

Each Service is labeled `app=mcp-<name>` — that's the label the federated backend will select on. The `appProtocol: kgateway.dev/mcp` tells AgentGateway to speak MCP to the upstream.

```bash
kubectl apply -f - <<EOF
# ─── airxiv (arXiv academic papers, Python) ──────────────────────────
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-airxiv
  namespace: mcp
spec:
  selector:
    matchLabels:
      app: mcp-airxiv
  template:
    metadata:
      labels:
        app: mcp-airxiv
    spec:
      containers:
      - name: mcp-airxiv
        image: ably7/airxiv-mcp:0.1.0
        imagePullPolicy: Always
        ports:
        - containerPort: 8000
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-airxiv
  namespace: mcp
  labels:
    app: mcp-airxiv
spec:
  selector:
    app: mcp-airxiv
  ports:
  - port: 80
    targetPort: 8000
    appProtocol: kgateway.dev/mcp
---
# ─── fred (Federal Reserve Economic Data, Node) ──────────────────────
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-fred
  namespace: mcp
spec:
  selector:
    matchLabels:
      app: mcp-fred
  template:
    metadata:
      labels:
        app: mcp-fred
    spec:
      containers:
      - name: mcp-fred
        image: ably7/fred-mcp-server:1.1.0
        imagePullPolicy: Always
        ports:
        - containerPort: 3000
        env:
        - name: FRED_API_KEY
          valueFrom:
            secretKeyRef:
              name: fred-api-key
              key: FRED_API_KEY
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-fred
  namespace: mcp
  labels:
    app: mcp-fred
spec:
  selector:
    app: mcp-fred
  ports:
  - port: 80
    targetPort: 3000
    appProtocol: kgateway.dev/mcp
---
# ─── sec-edgar (SEC filings + XBRL, Python) ──────────────────────────
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-sec-edgar
  namespace: mcp
spec:
  selector:
    matchLabels:
      app: mcp-sec-edgar
  template:
    metadata:
      labels:
        app: mcp-sec-edgar
    spec:
      containers:
      - name: mcp-sec-edgar
        image: ably7/sec-edgar-mcp:1.0.8
        imagePullPolicy: Always
        env:
        - name: SEC_EDGAR_USER_AGENT
          value: "Solo Demo (demo@solo.io)"
        ports:
        - containerPort: 9870
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-sec-edgar
  namespace: mcp
  labels:
    app: mcp-sec-edgar
spec:
  selector:
    app: mcp-sec-edgar
  ports:
  - port: 80
    targetPort: 9870
    appProtocol: kgateway.dev/mcp
---
# ─── bls (U.S. Bureau of Labor Statistics, Node) ─────────────────────
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-bls
  namespace: mcp
spec:
  selector:
    matchLabels:
      app: mcp-bls
  template:
    metadata:
      labels:
        app: mcp-bls
    spec:
      containers:
      - name: mcp-bls
        image: ably7/mcp-bls:1.0.0
        imagePullPolicy: Always
        ports:
        - containerPort: 3000
        env:
        - name: BLS_API_KEY
          valueFrom:
            secretKeyRef:
              name: bls-api-key
              key: BLS_API_KEY
              optional: true
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-bls
  namespace: mcp
  labels:
    app: mcp-bls
spec:
  selector:
    app: mcp-bls
  ports:
  - port: 80
    targetPort: 3000
    appProtocol: kgateway.dev/mcp
EOF
```

Wait for all four deployments to become ready:

```bash
kubectl rollout status deployment/mcp-airxiv    -n mcp
kubectl rollout status deployment/mcp-fred      -n mcp
kubectl rollout status deployment/mcp-sec-edgar -n mcp
kubectl rollout status deployment/mcp-bls       -n mcp
```

---

## Step 3: Create the multiplexed backend and HTTPRoute

One `EnterpriseAgentgatewayBackend` with four `targets` — each a selector matching one of the Service labels we just created. `failureMode: FailOpen` keeps the endpoint serving the healthy backends even if one of them dies; the default `FailClosed` would fail the whole session.

`timeouts.request: "0s"` on the HTTPRoute disables the per-request timeout so long-running tool calls (e.g., large XBRL queries from SEC EDGAR) can complete.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-federation-backend
  namespace: agentgateway-system
spec:
  mcp:
    failureMode: FailOpen
    targets:
      - name: arxiv
        selector:
          namespaces:
            matchLabels:
              kubernetes.io/metadata.name: mcp
          services:
            matchLabels:
              app: mcp-airxiv
      - name: fred
        selector:
          namespaces:
            matchLabels:
              kubernetes.io/metadata.name: mcp
          services:
            matchLabels:
              app: mcp-fred
      - name: secedgar
        selector:
          namespaces:
            matchLabels:
              kubernetes.io/metadata.name: mcp
          services:
            matchLabels:
              app: mcp-sec-edgar
      - name: bls
        selector:
          namespaces:
            matchLabels:
              kubernetes.io/metadata.name: mcp
          services:
            matchLabels:
              app: mcp-bls
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-federation
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp
      backendRefs:
        - name: mcp-federation-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "0s"
EOF
```

---

## Step 4: Verify federation with MCP Inspector

### Get the gateway address

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo $GATEWAY_IP
```

### Run MCP Inspector

```bash
npx @modelcontextprotocol/inspector@0.21.1
```

Connect to your AgentGateway:

- **Transport Type**: `Streamable HTTP`
- **URL**: `http://$GATEWAY_IP:8080/mcp` (substitute your actual IP/hostname)
- Click **Connect**

### List the federated tool surface

1. Click the **Tools** tab
2. Click **List Tools**
3. You should see **~34 tools** across four prefixes — `mcp-airxiv-80_*`, `mcp-fred-80_*`, `mcp-sec-edgar-80_*`, `mcp-bls-80_*`

### Run one tool from each backend

| Tool | Args |
|---|---|
| `mcp-sec-edgar-80_get_cik_by_ticker` | `ticker: AAPL` |
| `mcp-bls-80_get_unemployment` | `startYear: 2024`, `endYear: 2024` |
| `mcp-fred-80_fred_get_series` | `series_id: GDP`, `observation_start: 2024-01-01`, `observation_end: 2024-12-31` |
| `mcp-airxiv-80_search_arxiv` | `keyword: quantitative finance`, `max_results: 3` |

All four calls succeed through one MCP session — the federation is transparent to the client.

---

## Step 5: Demonstrate FailOpen

Scale one backend to zero and watch the federation behavior:

```bash
kubectl scale deployment mcp-bls -n mcp --replicas=0
```

In MCP Inspector, click **Disconnect** then **Connect** again, then **List Tools**. The `mcp-bls-80_*` tools have **disappeared** from the list — but every other backend still works. Try a `mcp-sec-edgar-80_*` or `mcp-airxiv-80_*` tool to confirm.

With the default `FailClosed`, the entire MCP session would fail at init because one target is unhealthy. `FailOpen` is the right call for a federated tool catalog where users probably don't care if one source is briefly down.

Restore the BLS backend:

```bash
kubectl scale deployment mcp-bls -n mcp --replicas=1
kubectl rollout status deployment/mcp-bls -n mcp
```

Reconnect in Inspector and the `mcp-bls-80_*` tools come back.

---

## Step 6: Apply JWT authentication across the federation

Federation's first big payoff: **one auth policy gates every tool in the union**. Clients can't authenticate into one backend and be denied at another, because there is only one gateway-side check.

This lab uses a dedicated demo keypair under `lib/jwt/` (distinct from the `solo.io`-issuer JWTs used in other workshop labs) so that this lab's tokens don't accidentally validate against other labs' policies.

### Inspect the bundled JWKS

The public key is already published as a JWKS document. Take a look:

```bash
cat lib/jwt/jwks.json
```

You'll see one RSA key with `"kid": "workshop-jwt-key-001"`. This is the document we'll inline into the gateway policy.

### Apply the JWT policy

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-federation-jwt
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mcp-federation
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: workshop.solo.io
          jwks:
            inline: |
                $(sed 's/^/                /' lib/jwt/jwks.json)
EOF
```

> The `$(sed ...)` substitution inlines `lib/jwt/jwks.json` with the indentation YAML needs. If your shell doesn't expand it, paste the contents of `lib/jwt/jwks.json` manually under `inline: |` with each line indented by 16 spaces.

### Test without a token

In MCP Inspector, click **Disconnect** then **Connect**. You should see:

```
MCP error -32001: Error POSTing to endpoint (HTTP 401): authentication failure: no bearer token found
```

### Mint an admin token and reconnect

```bash
TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/admin.json)
echo "$TOKEN"
```

In MCP Inspector → **Authentication** → **API Token**:

- **Header Name**: `Authorization`
- **Bearer Token**: paste `Bearer <the token from $TOKEN>` (the literal word `Bearer ` followed by the token)

Click **Reconnect**. Tool calls now succeed — for **all four backends** through the same auth check. No per-server JWKS config.

### Inspect the JWT claims

You can decode the JWT at https://jwt.io or in your shell:

```bash
echo "$TOKEN" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null; echo
```

You'll see the `admin.json` claims back out:

```json
{
  "iss": "workshop.solo.io",
  "sub": "admin-user",
  "exp": 4070908800,
  "persona": "admin",
  "org": "platform",
  "team": "sre"
}
```

The `persona` claim is the one we'll use to carve different tool surfaces in the next step.

---

## Step 7: Persona-based tool filtering

Federation's second payoff: with all tools behind one endpoint, you can express **per-identity entitlements as a single policy**, then let the gateway filter the tool catalog automatically per caller. The same federated endpoint presents a different tool surface to a researcher than to an equity analyst.

We'll define four personas keyed off the `persona` JWT claim:

| Persona | `persona` claim | Visible tools |
|---|---|---|
| Academic Researcher | `academic` | `mcp-airxiv-80_*` only |
| Macro Economist | `economist` | `mcp-fred-80_*` + `mcp-bls-80_*` |
| Equity Analyst | `analyst` | `mcp-sec-edgar-80_*` only |
| Platform Admin | `admin` | All ~34 tools |

### Apply the persona policy

The policy attaches to the **backend** (not the HTTPRoute) and uses the `spec.backend.mcp.authorization` shape. The CEL context for MCP authorization exposes two key per-tool attributes that the gateway extracts from parsed MCP traffic, applied to both `tools/list` (catalog visibility) and `tools/call` (invocation):

- `mcp.tool.name` — the **upstream-native** tool name (e.g., `search_arxiv`), not the federation-prefixed name the client sees
- `mcp.tool.target` — the **federation target identifier** in the form `<service-name>-<port>` (e.g., `mcp-airxiv-80`), which is the same string used as the client-visible prefix

Since we want to filter by source backend, we'll match on `mcp.tool.target`:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-federation-persona-authz
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: mcp-federation-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            - >-
              (jwt.persona == "admin") ||
              (jwt.persona == "academic"  && mcp.tool.target == "mcp-airxiv-80") ||
              (jwt.persona == "economist" && (mcp.tool.target == "mcp-fred-80" || mcp.tool.target == "mcp-bls-80")) ||
              (jwt.persona == "analyst"   && mcp.tool.target == "mcp-sec-edgar-80")
EOF
```

> The exact enterprise field path (`spec.backend.mcp.authorization`) is verified against `v2026.5.2`. If your cluster rejects the resource, run `kubectl explain enterpriseagentgatewaypolicies.spec.backend.mcp` to confirm the field shape on your installed version.
>
> **Why `mcp.tool.target` and not `mcp.tool.name.startsWith(...)`?** In a multiplexed backend, `mcp.tool.name` evaluates to the upstream's *unprefixed* tool name (e.g., `search_arxiv`) — see [agentgateway-enterprise#398](https://github.com/solo-io/agentgateway-enterprise/issues/398). The `mcp.tool.target` attribute is the right hook for per-backend filtering in a federation.

### Cycle through the personas

For each persona, mint the token, paste it into MCP Inspector's Authentication panel as `Bearer $TOKEN`, click **Reconnect**, then **List Tools** and observe what's visible.

```bash
# Academic — expect only mcp-airxiv-80_* tools
TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/academic.json) && echo "$TOKEN"
```

```bash
# Economist — expect mcp-fred-80_* + mcp-bls-80_* tools
TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/economist.json) && echo "$TOKEN"
```

```bash
# Analyst — expect only mcp-sec-edgar-80_* tools
TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/analyst.json) && echo "$TOKEN"
```

```bash
# Admin — expect all ~34 tools
TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/admin.json) && echo "$TOKEN"
```

Same federated endpoint, same backend, same policy — four distinct tool surfaces, decided per-request from the JWT.

The policy gates both `tools/list` **and** `tools/call`, so clients can't bypass the catalog filter by sending a raw `tools/call` for a tool the filter would hide.

### Want to add a persona?

Mint a new claims file under `lib/jwt/claims/`, set `persona` to a new value, and add a clause to the CEL expression. No backend reconfiguration, no per-backend policies — the federation surface is the unit of governance.

---

## Observability

Every request through the federated backend is annotated with `mcp.target` identifying which of the four upstream MCP servers served it. This is the per-backend dimension you want when looking at traffic distribution across the federation.

### View access logs

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Look for MCP-specific fields: `mcp.method`, `mcp.resource`, `mcp.resource.name`, `mcp.target`, and `http.status`.

### View MCP metrics

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep mcp && kill $!
```

You should see counters such as `agentgateway_mcp_tool_calls_total`, `agentgateway_mcp_server_requests_total`, and `agentgateway_mcp_request_duration_seconds` — broken down by `mcp.target` so you can see which backend is hot.

### View in Grafana

```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

Open http://localhost:3000 (username: `admin`, password: `prom-operator`) and navigate to **Dashboards > AgentGateway Dashboard**. The **MCP metrics** section shows per-target tool-call rates across the federation.

---

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-federation-persona-authz --ignore-not-found
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-federation-jwt --ignore-not-found
kubectl delete httproute -n agentgateway-system mcp-federation --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-federation-backend --ignore-not-found
kubectl delete deployment -n mcp mcp-airxiv mcp-fred mcp-sec-edgar mcp-bls --ignore-not-found
kubectl delete service    -n mcp mcp-airxiv mcp-fred mcp-sec-edgar mcp-bls --ignore-not-found
kubectl delete secret     -n mcp fred-api-key bls-api-key --ignore-not-found
```

The `mcp` namespace is left in place — other workshop labs may share it.
