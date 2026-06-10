# OpenAPI to MCP — In-Cluster Deployment

Expose a service you already run in your cluster as MCP tools by handing agentgateway its OpenAPI schema — no custom MCP server required.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

This lab deploys the **[Stripe mock server](https://github.com/stripe/stripe-mock)** (`stripe/stripe-mock`) into your cluster. It is a stateless mock that returns realistic, hardcoded Stripe API responses — **no Stripe account, real keys, or internet egress required**.

> **Related lab:** The [OpenAPI to MCP — External API](openapi-to-mcp-external-api.md) lab fronts an *external, public* HTTPS API (Open-Meteo) and originates TLS to it. This lab covers the more common enterprise case: a service **you deploy and own**, reached over plain in-cluster HTTP, that requires an upstream credential.

## Lab Objectives
- Expose an in-cluster deployment's OpenAPI spec as MCP tools
- Store a curated OpenAPI 3.0 schema subset in a ConfigMap and reference it from a backend
- Configure an `entMcp` backend with `protocol: OpenAPI` over plain HTTP (no TLS origination)
- Inject a static upstream credential with `policies.auth` so the generated tools can authenticate to the API
- Validate the generated tools with MCP Inspector and `curl`

## Overview

### What "OpenAPI to MCP" does

Most enterprises already run dozens of internal REST APIs described by OpenAPI specs. Rather than hand-writing an MCP server in front of each one, agentgateway can read an OpenAPI 3.0 schema and **synthesize one MCP tool per API operation** automatically. Each operation's `operationId` becomes the tool name, and its parameters/request body become the tool's input schema. AI agents can then discover and call your existing services through the standard MCP protocol, while the gateway handles the REST translation, upstream auth, and observability.

### `entMcp` vs `mcp`

The other MCP labs in this workshop use `spec.mcp`, which only speaks to upstreams that are already MCP servers (`StreamableHTTP` or `SSE`). OpenAPI-to-MCP is an **enterprise** feature, so it lives under `spec.entMcp`, which is a superset that adds the `OpenAPI` protocol:

| Backend field | Supported protocols |
|---|---|
| `spec.mcp` (OSS) | `StreamableHTTP`, `SSE` |
| `spec.entMcp` (enterprise) | `StreamableHTTP`, `SSE`, **`OpenAPI`** |

When `protocol: OpenAPI` is set, you must also provide `openAPI.schemaRef`, which points at a ConfigMap containing the OpenAPI 3.0 **JSON** schema under a `data.schema` key.

---

## Step 1: Deploy the Stripe mock server

Deploy `stripe-mock` into a dedicated `stripe-mock` namespace. It serves HTTP on port `12111`.

```bash
kubectl create namespace stripe-mock --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: stripe-mock
  namespace: stripe-mock
spec:
  replicas: 1
  selector:
    matchLabels:
      app: stripe-mock
  template:
    metadata:
      labels:
        app: stripe-mock
    spec:
      containers:
        - name: stripe-mock
          image: stripe/stripe-mock:latest
          ports:
            - containerPort: 12111
              name: http
---
apiVersion: v1
kind: Service
metadata:
  name: stripe-mock
  namespace: stripe-mock
spec:
  selector:
    app: stripe-mock
  ports:
    - name: http
      port: 12111
      targetPort: 12111
EOF
```

Verify that the pod is ready:
```bash
kubectl wait --for=condition=available deployment/stripe-mock -n stripe-mock --timeout=60s
```

> `stripe-mock` is **stateless**: it validates requests against the embedded Stripe OpenAPI spec and returns hardcoded sample objects. It does not persist anything you send, and it does not validate the *value* of your API key — but it does require an `Authorization` header to be present (see Step 3).

---

## Step 2: Store the OpenAPI schema in a ConfigMap

The full Stripe spec describes hundreds of operations, which would generate hundreds of MCP tools. Here we curate a small subset — four read-only operations (`listProducts`, `listPrices`, `listCustomers`, `listCharges`) — so the generated tools stay focused and the ConfigMap stays small. These were chosen because stripe-mock returns recognizable, non-empty sample data for them (a "T-shirt" product priced at $20.00/month, a sample customer, and a $1.00 charge), which makes the generated tools satisfying to call.

> The `servers.url` is left as `/` on purpose: the actual host and port come from the `EnterpriseAgentgatewayBackend` in the next step, not from the schema.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: stripe-mock-schema
  namespace: agentgateway-system
data:
  schema: |
    {
      "openapi": "3.0.0",
      "info": {
        "title": "Stripe Mock API (subset)",
        "version": "1.0.0",
        "description": "Curated subset of the Stripe API served by stripe-mock."
      },
      "servers": [
        { "url": "/" }
      ],
      "paths": {
        "/v1/products": {
          "get": {
            "operationId": "listProducts",
            "summary": "List products",
            "description": "Returns the product catalog. stripe-mock returns hardcoded sample products (e.g. a 'T-shirt'), each with a name, description, and default price.",
            "parameters": [
              {
                "name": "limit",
                "in": "query",
                "required": false,
                "description": "Maximum number of products to return (1-100).",
                "schema": { "type": "integer" }
              }
            ],
            "responses": {
              "200": { "description": "A list of products" }
            }
          }
        },
        "/v1/prices": {
          "get": {
            "operationId": "listPrices",
            "summary": "List prices",
            "description": "Returns prices for products in the catalog. Monetary amounts are in the smallest currency unit, so unit_amount 2000 means $20.00 USD. stripe-mock returns hardcoded sample data.",
            "parameters": [
              {
                "name": "limit",
                "in": "query",
                "required": false,
                "description": "Maximum number of prices to return (1-100).",
                "schema": { "type": "integer" }
              }
            ],
            "responses": {
              "200": { "description": "A list of prices" }
            }
          }
        },
        "/v1/customers": {
          "get": {
            "operationId": "listCustomers",
            "summary": "List customers",
            "description": "Returns a list of customers. stripe-mock returns hardcoded sample data.",
            "parameters": [
              {
                "name": "limit",
                "in": "query",
                "required": false,
                "description": "Maximum number of customers to return (1-100).",
                "schema": { "type": "integer" }
              },
              {
                "name": "email",
                "in": "query",
                "required": false,
                "description": "Filter customers by exact email match.",
                "schema": { "type": "string" }
              }
            ],
            "responses": {
              "200": { "description": "A list of customers" }
            }
          }
        },
        "/v1/charges": {
          "get": {
            "operationId": "listCharges",
            "summary": "List charges",
            "description": "Returns a list of charges. Monetary amounts (e.g. amount) are in the smallest currency unit, so 100 means $1.00 USD. stripe-mock returns hardcoded sample data.",
            "parameters": [
              {
                "name": "limit",
                "in": "query",
                "required": false,
                "description": "Maximum number of charges to return (1-100).",
                "schema": { "type": "integer" }
              }
            ],
            "responses": {
              "200": { "description": "A list of charges" }
            }
          }
        }
      }
    }
EOF
```

---

## Step 3: Create the backend, upstream credential, and HTTPRoute

`stripe-mock` rejects any request without an `Authorization` header (it returns `401`). Store a bearer credential in a `Secret` and inject it on every upstream call with `policies.auth`. Because the upstream is in-cluster plain HTTP, there is **no** `policies.tls` block here (contrast with the public-API lab).

> The value below is a generic placeholder. `stripe-mock` does not validate the key value, so any `Bearer ...` string works. You may substitute a real Stripe **test** key (`sk_test_...`) if you prefer.

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: stripe-mock-token
  namespace: agentgateway-system
type: Opaque
stringData:
  Authorization: "Bearer sk_test_123"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: stripe-mock-openapi
  namespace: agentgateway-system
spec:
  entMcp:
    targets:
      - name: stripe-mock
        static:
          host: stripe-mock.stripe-mock.svc.cluster.local
          port: 12111
          protocol: OpenAPI
          openAPI:
            schemaRef:
              name: stripe-mock-schema
  policies:
    auth:
      secretRef:
        name: stripe-mock-token
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openapi-mcp-stripe
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp
      backendRefs:
        - name: stripe-mock-openapi
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
EOF
```

Review the following table to understand this configuration.

| Setting | Description |
|---|---|
| `entMcp` | The enterprise MCP backend type, which supports the `OpenAPI`, `StreamableHTTP`, and `SSE` protocols. |
| `targets[].static.protocol` | Set to `OpenAPI` to expose REST API operations as MCP tools. |
| `targets[].static.host` / `port` | The in-cluster service, addressed as `<service>.<namespace>.svc.cluster.local` on its plain-HTTP port `12111`. No TLS origination is needed. |
| `targets[].static.openAPI.schemaRef.name` | The ConfigMap that holds the OpenAPI 3.0 JSON schema. The ConfigMap must have a `data.schema` key. |
| `policies.auth.secretRef` | A `Secret` whose `Authorization` key value is injected as the `Authorization` header on every upstream request — this is what satisfies stripe-mock's auth requirement. |

> **Contrast with the public-API lab:** that lab adds `policies.tls` to originate TLS to an HTTPS upstream and needs no upstream credential. This lab drops `tls` (in-cluster HTTP) and adds `policies.auth` (the API requires a token).

Verify that the HTTPRoute is accepted:
```bash
kubectl get httproute openapi-mcp-stripe -n agentgateway-system \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}{"\n"}'
```

Also verify that the backend itself is accepted. This is where the most likely misconfigurations surface — a wrong `schemaRef.name` or a ConfigMap missing the `data.schema` key — and they would otherwise show up only as an empty tool list later:
```bash
kubectl get enterpriseagentgatewaybackend stripe-mock-openapi -n agentgateway-system \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```
Both commands should print `True`.

---

## Step 4: Verify the generated tools with MCP Inspector

### Get the gateway address
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo $GATEWAY_IP
```

If you are running locally without a LoadBalancer address, port-forward the proxy instead. Forward to the Gateway's HTTP listener port (`8080` in this workshop — confirm with `kubectl get gateway agentgateway-proxy -n agentgateway-system -o jsonpath='{.spec.listeners[*].port}'`):
```bash
kubectl port-forward -n agentgateway-system svc/agentgateway-proxy 8080:8080
```

### Run MCP Inspector
```bash
npx @modelcontextprotocol/inspector@0.21.1
```

Connect to your AgentGateway:
- **Transport Type**: Select `Streamable HTTP`
- **URL**: `http://$GATEWAY_IP:8080/mcp` (LoadBalancer) or `http://localhost:8080/mcp` (port-forward)
- Click **Connect**

### List and run a tool
1. Click the **Tools** tab, then **List Tools**. You should see four tools — `listProducts`, `listPrices`, `listCustomers`, and `listCharges` — each with an input schema derived from the OpenAPI spec.
2. Select **listProducts** and click **Run Tool** (leave the optional `limit` blank). It returns a Stripe list object (`"object": "list"`) whose `data` contains a sample product — a `"T-shirt"` described as `"Comfortable gray cotton t-shirt"`.
3. Run **listPrices** the same way and confirm you get a price with `"unit_amount": 2000` and a monthly `recurring` interval — i.e. $20.00/month. (Stripe amounts are in the smallest currency unit, so `2000` = $20.00 USD.)
4. (Optional) Run **listCharges** with `limit` = `3` (the parameter is nested under a `query` object — see the **Tool input shape** note in Step 5) and confirm you get a charge with `"amount": 100` ($1.00) and `"status": "succeeded"`.

---

## Step 5 (optional): Verify with curl

These steps assume the proxy is reachable at `http://localhost:8080` (via port-forward).

1. Initialize an MCP session and capture the session ID from the response headers.
   ```bash
   SESSION=$(curl -sD - -o /dev/null "http://localhost:8080/mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
     | grep -i "^mcp-session-id:" | awk '{print $2}' | tr -d '\r')
   echo "session: $SESSION"
   ```

2. Send the `notifications/initialized` notification, then list the tools.
   ```bash
   curl -s "http://localhost:8080/mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -H "Mcp-Session-Id: $SESSION" \
     -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

   curl -s "http://localhost:8080/mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -H "Mcp-Session-Id: $SESSION" \
     -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
   ```

   Confirm that `listProducts`, `listPrices`, `listCustomers`, and `listCharges` appear in the tool list.

3. Call the `listProducts` tool (its `limit` parameter is optional, so empty arguments are valid).
   ```bash
   curl -s "http://localhost:8080/mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -H "Mcp-Session-Id: $SESSION" \
     -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"listProducts","arguments":{}}}'
   ```

   You should get back a Stripe list object (`"object": "list"`) containing a sample `"T-shirt"` product, wrapped in an MCP `tools/call` result.

> **Tool input shape:** every operation here takes an optional `limit` query parameter, and the generated `inputSchema` groups query parameters under a top-level `query` object. Inspect the `inputSchema` in the `tools/list` output and nest the arguments accordingly — for example, `"arguments":{"query":{"limit":3}}` for `listCharges`.

---

## Observability

### View access logs
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

A `tools/call` against this backend logs `protocol=mcp`, `mcp.method.name=tools/call`, `mcp.target=stripe-mock`, `mcp.resource.type=tool`, `mcp.session.id=<id>`, and `http.status=200`.

> Tip: if your gateway also fronts other traffic, filter to this route to cut the noise:
> ```bash
> kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --tail 200 | grep -F "route=agentgateway-system/openapi-mcp-stripe"
> ```

### View MCP metrics
```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 2 && curl -s http://localhost:15020/metrics | grep -iE "mcp" && kill $!
```

You should see MCP request counters, including:
- `agentgateway_mcp_requests_total` — total MCP requests handled
- `agentgateway_requests_total{...protocol="mcp"...}` — overall request counter, labeled with `protocol="mcp"` for this backend
- `agentgateway_request_duration_seconds_*` — request latency histogram (also carries the `protocol="mcp"` label)

> **Multiple replicas:** `/metrics` is scraped **per pod**. If your gateway runs more than one replica, the `port-forward` above lands on a single pod, and because OpenAPI-to-MCP is stateless the tool calls load-balance across replicas — so the counts you see reflect only that one pod's share and will likely be lower than the number of calls you made. For an aggregate across all replicas, use the Grafana/Prometheus view below.

### View in Grafana
1. Port-forward Grafana:
   ```bash
   kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
   ```
2. Open http://localhost:3000 (username: `admin`, password: `prom-operator`)
3. Navigate to **Dashboards > AgentGateway Dashboard** and view the **MCP metrics** section for tool call rates and durations against the Stripe mock backend.

---

## Next steps

- **Secure the route**: Apply an `EnterpriseAgentgatewayPolicy` with JWT authentication and CEL-based RBAC, exactly as shown in the [Configure Route to MCP Server lab](in-cluster-mcp.md). Because the route path (`/mcp`) and gateway are the same, that policy applies unchanged. Note this secures the *downstream* (client→gateway) hop, independent of the `policies.auth` upstream credential configured here.
- **Front your own API**: Swap the Deployment for any in-cluster service that ships an OpenAPI 3.0 spec, point `schemaRef` at its (curated) schema, and adjust `policies.auth` to whatever upstream credential it needs.
- **Reduce tool-context bloat**: For large OpenAPI specs that generate many tools, explore `toolMode: Search` or `Code` in the [MCP tool mode labs](mcp-tool-mode-search.md).
- **See also**: [OpenAPI to MCP — External API](openapi-to-mcp-external-api.md) for the external-HTTPS-API variant with TLS origination.

---

## Cleanup

```bash
kubectl delete httproute -n agentgateway-system openapi-mcp-stripe
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system stripe-mock-openapi
kubectl delete secret -n agentgateway-system stripe-mock-token
kubectl delete configmap -n agentgateway-system stripe-mock-schema
kubectl delete namespace stripe-mock
```
