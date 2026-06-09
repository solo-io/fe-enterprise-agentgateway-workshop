# OpenAPI to MCP — External API

Expose an existing REST API as MCP tools by handing agentgateway an OpenAPI schema — no custom MCP server required.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

This lab points the gateway at the public **[Open-Meteo](https://open-meteo.com/)** weather API (`api.open-meteo.com`). It is free, requires **no API key or account**, and serves live data over HTTPS. The only requirement is that your cluster has outbound internet egress (the same assumption as the `in-cluster-mcp` fetch tool and the HTTPS MCP labs).

## Lab Objectives
- Understand how agentgateway converts an OpenAPI specification into MCP tools
- Store an OpenAPI 3.0 schema in a ConfigMap and reference it from a backend
- Configure an `entMcp` backend with `protocol: OpenAPI` that originates TLS to an HTTPS upstream
- Expose the generated tools on an MCP route and validate them with MCP Inspector and `curl`

## Overview

### What "OpenAPI to MCP" does

Most enterprises already have dozens of REST APIs described by OpenAPI specs. Rather than hand-writing an MCP server in front of each one, agentgateway can read an OpenAPI 3.0 schema and **synthesize one MCP tool per API operation** automatically. Each operation's `operationId` becomes the tool name, and its parameters/request body become the tool's input schema. AI agents can then discover and call your existing APIs through the standard MCP protocol, while the gateway handles the REST translation, TLS origination, auth, and observability.

### `entMcp` vs `mcp`

The other MCP labs in this workshop use `spec.mcp`, which only speaks to upstreams that are already MCP servers (`StreamableHTTP` or `SSE`). OpenAPI-to-MCP is an **enterprise** feature, so it lives under `spec.entMcp`, which is a superset that adds the `OpenAPI` protocol:

| Backend field | Supported protocols |
|---|---|
| `spec.mcp` (OSS) | `StreamableHTTP`, `SSE` |
| `spec.entMcp` (enterprise) | `StreamableHTTP`, `SSE`, **`OpenAPI`** |

When `protocol: OpenAPI` is set, you must also provide `openAPI.schemaRef`, which points at a ConfigMap containing the OpenAPI 3.0 **JSON** schema under a `data.schema` key.

---

## Step 1: Store the OpenAPI schema in a ConfigMap

The gateway reads the OpenAPI 3.0 schema from the `data.schema` key of a ConfigMap. Below is a curated subset of the Open-Meteo Forecast API — a single `getWeatherForecast` operation with well-described parameters so the generated MCP tool has a meaningful input schema.

> The `servers.url` is left as `/` on purpose: the actual host, port, and TLS settings come from the `EnterpriseAgentgatewayBackend` in the next step, not from the schema. The schema only describes the *paths* and *operations*.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: open-meteo-schema
  namespace: agentgateway-system
data:
  schema: |
    {
      "openapi": "3.0.0",
      "info": {
        "title": "Open-Meteo Forecast API",
        "version": "1.0.0",
        "description": "Free weather forecast API. No API key required."
      },
      "servers": [
        { "url": "/" }
      ],
      "paths": {
        "/v1/forecast": {
          "get": {
            "operationId": "getWeatherForecast",
            "summary": "Get the weather forecast for a geographic location",
            "description": "Returns current, hourly, and/or daily weather variables for a latitude/longitude. Use the 'current' parameter for an instantaneous snapshot.",
            "parameters": [
              {
                "name": "latitude",
                "in": "query",
                "required": true,
                "description": "Latitude of the location in decimal degrees (e.g. 51.5072 for London).",
                "schema": { "type": "number" }
              },
              {
                "name": "longitude",
                "in": "query",
                "required": true,
                "description": "Longitude of the location in decimal degrees (e.g. -0.1276 for London).",
                "schema": { "type": "number" }
              },
              {
                "name": "current",
                "in": "query",
                "required": false,
                "description": "Comma-separated list of current weather variables, e.g. 'temperature_2m,wind_speed_10m,relative_humidity_2m'.",
                "schema": { "type": "string" }
              },
              {
                "name": "hourly",
                "in": "query",
                "required": false,
                "description": "Comma-separated list of hourly weather variables, e.g. 'temperature_2m,precipitation'.",
                "schema": { "type": "string" }
              },
              {
                "name": "daily",
                "in": "query",
                "required": false,
                "description": "Comma-separated list of daily weather variables, e.g. 'temperature_2m_max,temperature_2m_min'.",
                "schema": { "type": "string" }
              },
              {
                "name": "temperature_unit",
                "in": "query",
                "required": false,
                "description": "Unit for temperature values.",
                "schema": { "type": "string", "enum": ["celsius", "fahrenheit"] }
              },
              {
                "name": "timezone",
                "in": "query",
                "required": false,
                "description": "Timezone for daily/hourly timestamps. Use 'auto' to resolve from the coordinates.",
                "schema": { "type": "string" }
              },
              {
                "name": "forecast_days",
                "in": "query",
                "required": false,
                "description": "Number of forecast days to return (0-16).",
                "schema": { "type": "integer" }
              }
            ],
            "responses": {
              "200": {
                "description": "Weather forecast data"
              }
            }
          }
        }
      }
    }
EOF
```

---

## Step 2: Create the OpenAPI backend and HTTPRoute

Create an `EnterpriseAgentgatewayBackend` with the `entMcp` backend type. Set `protocol: OpenAPI`, reference the ConfigMap, and — because Open-Meteo is HTTPS-only on port `443` — originate TLS to the upstream with `static.policies.tls`.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: open-meteo-openapi
  namespace: agentgateway-system
spec:
  entMcp:
    targets:
      - name: open-meteo
        static:
          host: api.open-meteo.com
          port: 443
          protocol: OpenAPI
          openAPI:
            schemaRef:
              name: open-meteo-schema
          policies:
            tls:
              sni: api.open-meteo.com
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openapi-mcp
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
        - name: open-meteo-openapi
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
EOF
```

Review the following table to understand this configuration.

| Setting | Description |
|---|---|
| `entMcp` | The enterprise MCP backend type, which supports the `OpenAPI`, `StreamableHTTP`, and `SSE` protocols. |
| `targets[].static.protocol` | Set to `OpenAPI` to expose REST API operations as MCP tools. |
| `targets[].static.host` / `port` | The upstream REST API endpoint. Open-Meteo serves HTTPS on port `443`. For in-cluster services, use `<service>.<namespace>.svc.cluster.local`. |
| `targets[].static.openAPI.schemaRef.name` | The ConfigMap that holds the OpenAPI 3.0 JSON schema. The ConfigMap must have a `data.schema` key. |
| `targets[].static.policies.tls.sni` | Originates TLS to the upstream and sets the SNI server name. Required for HTTPS upstreams. Validates against the system trust store, which is correct for a public CA like Open-Meteo's. |

> **Heads-up — the `/mcp` path is shared across the MCP labs.** Several labs in this workshop (in-cluster MCP, tool federation, the Stripe OpenAPI lab, etc.) all bind `PathPrefix: /mcp` on the same `agentgateway-proxy` gateway. They coexist because each is a separate `HTTPRoute` → backend, but if you have more than one applied at once the gateway matches `/mcp` to only one of them (most-specific / last-accepted wins), so MCP Inspector may connect to a different backend than you expect. If you plan to run multiple MCP labs simultaneously, give each route a distinct path (e.g. `/mcp/open-meteo`) or delete the others first. The Cleanup section at the bottom removes this lab's route.

Verify that the HTTPRoute is accepted:
```bash
kubectl get httproute openapi-mcp -n agentgateway-system \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}{"\n"}'
```

---

## Step 3: Verify the generated tools with MCP Inspector

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

### List and run the tool
1. Click the **Tools** tab, then **List Tools**. You should see the `getWeatherForecast` tool, with the parameter schema derived directly from the OpenAPI spec.
2. Select **getWeatherForecast**. The generated tool groups all OpenAPI query parameters under a single **`query`** object, so the Inspector form shows a `query` section — expand it and fill in the fields, for example:
   - `latitude`: `51.5072`
   - `longitude`: `-0.1276`
   - `current`: `temperature_2m,wind_speed_10m`
3. Click **Run Tool**.
4. Verify that the tool returns live weather JSON from Open-Meteo, including a `current` block with `temperature_2m` and `wind_speed_10m`.

---

## Step 4 (optional): Verify with curl

You can exercise the same flow from the command line. These steps assume the proxy is reachable at `http://localhost:8080` (via port-forward).

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

   Confirm that `getWeatherForecast` appears in the tool list. Note that its `inputSchema` groups the OpenAPI query parameters under a top-level **`query`** object — your tool-call arguments must match that shape.

3. Call the tool. The arguments are nested under `query` to match the generated input schema.
   ```bash
   curl -s "http://localhost:8080/mcp" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -H "Mcp-Session-Id: $SESSION" \
     -d '{
       "jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{
         "name":"getWeatherForecast",
         "arguments":{
           "query":{
             "latitude":51.5072,
             "longitude":-0.1276,
             "current":"temperature_2m,wind_speed_10m"
           }
         }
       }
     }'
   ```

   You should get back live Open-Meteo weather JSON wrapped in an MCP `tools/call` result, for example:
   ```json
   {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{...\"current\":{\"time\":\"2026-06-09T22:15\",\"temperature_2m\":14.0,\"wind_speed_10m\":15.5}}"}],"isError":false}}
   ```

---

## Observability

### View access logs
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Look for MCP-specific fields in the structured log output. A `tools/call` against this backend logs `protocol=mcp`, `mcp.method.name=tools/call`, `mcp.target=open-meteo`, `mcp.resource.type=tool`, `gen_ai.tool.name=getWeatherForecast` (the specific tool invoked), `mcp.session.id=<id>`, and `http.status=200`. The `initialize`, `notifications/initialized`, and `tools/list` lines carry `protocol=mcp` and `mcp.method.name` but **not** `mcp.target` / `mcp.resource.type` — those only appear on `tools/call`.

> **Multiple proxy replicas:** the gateway often runs more than one replica (`kubectl get deploy agentgateway-proxy -n agentgateway-system`), and an OpenAPI/StreamableHTTP MCP session is stateless (the `Mcp-Session-Id` token encodes the target, so successive requests can land on any replica). The `--prefix` flag above tags each line with its source pod, and the `-l` selector streams from all matching pods — keep both so you don't miss a request served by a different replica. If you ever filter to a single pod, grep by `mcp.session.id=<id>` instead to follow one session across replicas.

> Tip: if your gateway also fronts other traffic (for example LLM routes or other MCP labs), filter to this route to cut the noise:
> ```bash
> kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 200 | grep -F "route=agentgateway-system/openapi-mcp "
> ```

### View MCP metrics
```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 2 && curl -s http://localhost:15020/metrics | grep -iE "open-meteo|protocol=\"mcp\"" && kill $!
```

> The grep is scoped to `open-meteo` and `protocol="mcp"` on purpose. A plain `grep -iE "mcp"` returns every MCP route on the gateway (federation, other labs, etc.), which is a lot of noise on a shared cluster.

> **Single-pod scrape, multiple replicas:** `port-forward` to `deployment/agentgateway-proxy` connects to **one** pod, and these counters are per-pod (Prometheus normally scrapes every pod and sums them). So with multiple replicas the numbers below reflect only the replica you hit, not the cluster total — re-run the port-forward to land on a different pod, or use Grafana (next section), which aggregates across all replicas.

You should see MCP request counters, including:
- `agentgateway_mcp_requests_total{...server="open-meteo",resource="getWeatherForecast"...}` — per-tool MCP request counter (labeled by `method`, `resource_type`, `server`, and `resource`)
- `agentgateway_requests_total{...protocol="mcp"...}` — overall request counter, labeled with `protocol="mcp"` and `backend="agentgateway-system/open-meteo-openapi"` for this backend
- `agentgateway_request_duration_seconds_*` — request latency histogram (also carries the `protocol="mcp"` label)

### View in Grafana
1. Port-forward Grafana:
   ```bash
   kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
   ```
2. Open http://localhost:3000 (username: `admin`, password: `prom-operator`)
3. Navigate to **Dashboards > AgentGateway Dashboard** and view the **MCP metrics** section for tool call rates and durations against the Open-Meteo backend.

---

## Next steps

- **Secure the route**: Apply an `EnterpriseAgentgatewayPolicy` with JWT authentication and CEL-based RBAC, exactly as shown in the [Configure Route to MCP Server lab](in-cluster-mcp.md). Because the route path (`/mcp`) and gateway are the same, that policy applies unchanged.
- **Federate multiple APIs**: Add more targets (each with its own OpenAPI schema) to one `entMcp` backend, or combine OpenAPI tools with native MCP servers — see [MCP Tool Federation](mcp-tool-federation.md).
- **Reduce tool-context bloat**: For large OpenAPI specs that generate many tools, explore `toolMode: Search` or `Code` in the [MCP tool mode labs](mcp-tool-mode-search.md).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `tools/call` returns a TLS/connection error, or the upstream resets the connection | `policies.tls` was omitted, so the gateway tried plaintext against an HTTPS-only upstream on `443`. | Add `policies.tls.sni: api.open-meteo.com` to the target (Step 2). HTTPS upstreams **must** originate TLS; the SNI is required for servers that virtual-host by name. |
| Upstream returns `404` / `400` even though `tools/list` works | The schema `paths` don't match the real API path, or the `servers.url` was set to a full host instead of `/`. | Keep `servers.url` as `/` — host/port/TLS come from the backend, not the schema. Make the `paths` key (`/v1/forecast`) exactly match the upstream route. |
| `tools/call` fails with a schema/validation error | Arguments weren't nested under the generated **`query`** object. agentgateway groups all OpenAPI query parameters under a single top-level `query` property. | Wrap the parameters: `"arguments":{"query":{"latitude":...,"longitude":...}}`. Inspect the exact shape with `tools/list` first. |
| `tools/list` is empty or the tool name is wrong | The OpenAPI operation has no `operationId`, or the ConfigMap key isn't `data.schema`. | Every operation needs an `operationId` (it becomes the tool name). The ConfigMap must expose the JSON under `data.schema`. |
| Backend status is not `Accepted`, or HTTPRoute `ResolvedRefs=False` | The `schemaRef` ConfigMap name is wrong/missing, or the backend `group`/`kind` on the `backendRef` is misspelled. | `kubectl get enterpriseagentgatewaybackend open-meteo-openapi -n agentgateway-system -o yaml` and check `.status.conditions`. Confirm the ConfigMap exists and the `backendRefs.group` is `enterpriseagentgateway.solo.io`. |
| MCP Inspector connects to the wrong tools | Another MCP lab is also bound to `/mcp` on this gateway. | See the `/mcp` shared-path note in Step 2 — use a distinct path or remove the other routes. |

---

## Cleanup

```bash
kubectl delete httproute -n agentgateway-system openapi-mcp
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system open-meteo-openapi
kubectl delete configmap -n agentgateway-system open-meteo-schema
```
