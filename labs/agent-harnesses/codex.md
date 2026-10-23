# Configure Enterprise AgentGateway for Codex

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

You also need the [Codex CLI](https://github.com/openai/codex) installed locally (`npm install -g @openai/codex` or `brew install --cask codex`).

## Lab Objectives
- Create a Kubernetes secret that contains your OpenAI API key
- Create a route to OpenAI's Responses API using an `EnterpriseAgentgatewayBackend` and `HTTPRoute`
- Add retry and token rate limit policies to the route
- Serve the Codex model catalog request from the gateway with a `directResponse`
- Test the route with curl to verify connectivity
- Use the Codex CLI through the agentgateway proxy with a Codex profile
- Validate Codex requests in Grafana, the Solo UI, and access logs

## Overview

Codex talks to OpenAI through the Responses API (`/v1/responses`). This lab configures Enterprise AgentGateway as a custom Codex model provider:

- **Responses API** (`/v1/responses`): the format Codex uses for every agent turn, including tool calls
- **Additional endpoints** (`*`): all other API endpoints pass through

The gateway holds the OpenAI API key and injects it on every request. Developers run Codex with no OpenAI credential on their machine, and the platform team gets observability, rate limiting, and guardrails for all Codex traffic.

## Configure Required Variables

Export your OpenAI API key:
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY     # an OpenAI API key with Responses API access
```

Create the OpenAI API key secret:
```bash
kubectl create secret generic codex-openai-apikey -n agentgateway-system \
  --from-literal="Authorization=Bearer $OPENAI_API_KEY" \
  --dry-run=client -oyaml | kubectl apply -f -
```

## Create the Codex Route and Backend

Create the `HTTPRoute`, `EnterpriseAgentgatewayBackend`, and `EnterpriseAgentgatewayPolicy`:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: codex-route
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /codex
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            remove:
            - authorization
      backendRefs:
        - name: codex-openai-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "540s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: codex-openai-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: codex-openai-apikey
    ai:
      routes:
        "/v1/responses": "Responses"
        "*": "Passthrough"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: codex-policies
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: codex-route
  traffic:
    retry:
      attempts: 3
      backoff: 500ms
      codes:
      - 429
      - 502
      - 503
      - 504
    rateLimit:
      local:
      - tokens: 5000000
        unit: Hours
EOF
```

The `RequestHeaderModifier` filter removes any `Authorization` header the client sends, so the only credential that reaches OpenAI is the one in `codex-openai-apikey`. The `"/v1/responses": "Responses"` entry tells the gateway to parse Responses API traffic, which gives you token counts, cost, and model names in metrics, logs, and traces. The policy retries transient upstream failures and caps the route at 5 million LLM tokens per hour.

## Serve the Codex Model Catalog

At startup Codex requests its model catalog from `<base_url>/models` in a Codex-specific format that OpenAI does not serve. Without this route, OpenAI returns `404` and Codex logs `ERROR codex_models_manager::manager: failed to refresh available models`.

This route answers `/codex/v1/models` at the gateway with an empty catalog, so Codex uses its built-in model metadata. The `Exact` match takes precedence over the `/codex` `PathPrefix` match in `codex-route`:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: codex-models-route
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: Exact
            value: /codex/v1/models
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: codex-models-response
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: codex-models-route
  traffic:
    directResponse:
      status: 200
      body: '{"models":[]}'
EOF
```

## Export the Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo "Gateway IP: $GATEWAY_IP"
```

## Test the Route with curl

The request carries no API key. The gateway adds it:
```bash
curl -i "$GATEWAY_IP:8080/codex/v1/responses" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.6-terra",
    "input": "Whats your favorite poem?"
  }'
```

Expected output (truncated):
```
HTTP/1.1 200 OK
content-type: application/json
...
{"id":"resp_...","status":"completed","output":[{"type":"message","content":[{"type":"output_text",...
```

## Configure and Launch Codex

Codex reads extra configuration layers from `$CODEX_HOME/<profile>.config.toml` (`~/.codex` by default). Create an `agentgateway` profile that defines the gateway as a model provider:
```bash
mkdir -p ~/.codex
cat > ~/.codex/agentgateway.config.toml <<EOF
model_provider = "agentgateway"
model = "gpt-5.6-terra"

[model_providers.agentgateway]
name = "Enterprise Agentgateway"
base_url = "http://$GATEWAY_IP:8080/codex/v1"
wire_api = "responses"
EOF
```

The provider sets no `env_key`, so Codex sends no OpenAI credential. Your existing Codex settings and any ChatGPT login stay untouched and apply again when you run Codex without the profile.

Run a one-shot task through the gateway:
```bash
codex exec -p agentgateway --skip-git-repo-check "Reply with exactly: hello from agentgateway" < /dev/null
```

Expected output (truncated):
```
OpenAI Codex v0.161.0
--------
model: gpt-5.6-terra
provider: agentgateway
...
codex
hello from agentgateway
```

The output has no `failed to refresh available models` error, because the gateway answered the catalog request.

Start an interactive Codex session through the gateway:
```bash
codex -p agentgateway
```

Every model call Codex makes in this session, including the turns that follow a tool call, goes through `/codex/v1/responses` on the gateway.

## Observability

### View Metrics in Grafana

Use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../../002-set-up-ui-and-monitoring-tools.md) to visualize metrics.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

The dashboard shows:
- Core GenAI metrics (request rates, token usage by model) with `gpt-5.6-terra` as the model
- Streaming metrics (TTFT, TPOT)
- Connection and runtime metrics

### View Traces in the Solo UI

Distributed traces for Codex requests are in the Solo UI deployed in the [monitoring tools lab](../../002-set-up-ui-and-monitoring-tools.md).

1. Port-forward to the Solo UI service:
```bash
kubectl port-forward -n agentgateway-system svc/enterprise-agentgateway-ui 4000:80
```

2. Open http://localhost:4000 in your browser

3. Click **Tracing** in the left nav

Each Codex model call is one span. Filter on the **Route** column for `agentgateway-system/codex-route`. The **Input Tokens** and **Output Tokens** columns show per-call token counts, and **MCP/LLM Payload** opens the full prompt and completion. A single Codex task that runs tools produces one span per model turn.

4. Click any row to open the trace detail view. **Span Details** lists the gen-AI attributes the gateway emits, including `operation: "chat"`, `provider: "openai"`, `request.model`, `response.model`, and the input, output, cached, and reasoning token counts.

### View Access Logs

The gateway logs every Codex request to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20 | grep codex
```

Each log line carries:
- `route=agentgateway-system/codex-route` and `http.path=/codex/v1/responses`
- `gen_ai.request.model` and `gen_ai.response.model` (`gpt-5.6-terra`)
- Input, output, cached, and reasoning token counts
- `agw.ai.usage.cost.total`, the estimated cost of the call in USD
- Time to first token and request duration
- A `trace.id` you can search for in the Solo UI's **Tracing** view

Codex resends its full conversation on every turn, so after the first turn most input tokens show up as `gen_ai.usage.cache_read.input_tokens`.

Codex's startup catalog request logs as `route=agentgateway-system/codex-models-route http.path=/codex/v1/models?client_version=... http.status=200`. The gateway answers it, so it never reaches OpenAI.

### View Metrics Endpoint

AgentGateway exposes Prometheus metrics at the `/metrics` endpoint:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep codex-route | head -20 && kill $!
```

Look for metrics like:
- `agentgateway_gen_ai_client_token_usage`: token usage by model and type
- `agentgateway_gen_ai_server_request_duration`: request latency for Codex calls
- `agentgateway_requests_total`: HTTP request counts

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system codex-policies codex-models-response
kubectl delete httproute -n agentgateway-system codex-route codex-models-route
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system codex-openai-backend
kubectl delete secret -n agentgateway-system codex-openai-apikey
rm -f ~/.codex/agentgateway.config.toml
unset GATEWAY_IP
```
