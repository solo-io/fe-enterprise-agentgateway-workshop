# Configure Enterprise AgentGateway for Claude Code

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Choose between Direct API Key or Claude Max / Team Subscription access
- Create a Kubernetes secret that contains your Anthropic credentials
- Create a passthrough route to Anthropic as our backend LLM provider using a `Backend` and `HTTPRoute`
- Test the route with curl to verify connectivity
- Use Claude Code CLI through the agentgateway proxy
- Validate Claude Code requests in Grafana UI and access logs

## Overview

This lab configures Enterprise AgentGateway with passthrough routing to Anthropic's Claude API. The configuration supports multiple endpoints including:

- **Native Anthropic API** (`/v1/messages`) - Claude's native message format used by Claude Code CLI
- **Additional endpoints** (`/v1/models`, `*`) - All other API endpoints pass through

This makes the gateway compatible with Claude Code CLI and other Anthropic API clients, enabling observability, rate limiting, and guardrails for all Claude interactions.

## Choose Your Access Method

There are two ways to authenticate Claude Code through the gateway:

| | Claude Max / Team Subscription | Direct API Key |
|---|---|---|
| **Credential type** | `sk-ant-oat01...` OAuth token from your Claude subscription | `sk-ant-api...` key from console.anthropic.com |
| **Gateway setup** | Reuses the existing `agentgateway-proxy` listener | Reuses the existing `agentgateway-proxy` listener |
| **Path** | `$GATEWAY_IP:8080/claude` | `$GATEWAY_IP:8080/claude` |
| **Best for** | Individual or team Claude Max subscriptions | Service accounts, CI/CD, API-billed access |

Follow **Option A** or **Option B** below, then continue to the shared testing and observability steps.

---

## Option A: Claude Max / Team Subscription

### Configure Required Variables

Use the Claude Code CLI to generate a long-lived OAuth token from your Claude Max subscription:
```bash
claude setup-token
```

This will open a browser-based authentication flow. Once complete, the CLI will print your token. Export it:
```bash
export CLAUDE_OAUTH_TOKEN=<token-printed-by-setup-token>
```

### Create the Secret and Resources

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: claude-subscription-token
  namespace: agentgateway-system
type: Opaque
stringData:
  Authorization: $CLAUDE_OAUTH_TOKEN
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: claude-subscription-route
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /claude
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            remove:
            - x-api-key
            - authorization
      backendRefs:
        - name: claude-subscription-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: claude-subscription-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      anthropic: {}
  policies:
    auth:
      secretRef:
        name: claude-subscription-token
    ai:
      routes:
        "/v1/messages": "Messages"
        "/v1/models": "Passthrough"
        "*": "Passthrough"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: claude-subscription-policies
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: claude-subscription-route
  traffic:
    retry:
      attempts: 3
      backoff: 500ms
      codes: [429, 502, 503, 504, 529]
    timeouts:
      request: 540s
    rateLimit:
      local:
      - tokens: 5000000
        unit: Hours
EOF
```

### Export the Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### Test the Route with curl

```bash
curl -i "$GATEWAY_IP:8080/claude/v1/messages" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1024,
    "messages": [
      {
        "role": "user",
        "content": "Explain what an API Gateway does in one sentence."
      }
    ]
  }'
```

### Configure and Launch Claude Code

Because the gateway injects the team credential, Claude Code only needs a placeholder API key:

```bash
export ANTHROPIC_BASE_URL="http://$GATEWAY_IP:8080/claude"
export ANTHROPIC_API_KEY=dummy
claude
```

**Note for Vertex AI users**: If you were previously using Claude Code with Vertex AI, unset the following variable first:
```bash
unset CLAUDE_CODE_USE_VERTEX
```

### Cleanup

```bash
kubectl delete httproute -n agentgateway-system claude-subscription-route
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system claude-subscription-backend
kubectl delete agentgatewaypolicy -n agentgateway-system claude-subscription-policies
kubectl delete secret -n agentgateway-system claude-subscription-token
unset ANTHROPIC_BASE_URL ANTHROPIC_API_KEY CLAUDE_OAUTH_TOKEN GATEWAY_IP
```

---

## Option B: Direct API Key

### Configure Required Variables

Replace with a valid Anthropic API key:
```bash
export CLAUDE_API_KEY=<your-anthropic-api-key>
```

Create the Anthropic API key secret:
```bash
kubectl create secret generic claude-direct-apikey -n agentgateway-system \
--from-literal="Authorization=$CLAUDE_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

### Create Anthropic Route and Backend

Create the HTTPRoute and EnterpriseAgentgatewayBackend with passthrough configuration:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: claude-directapikey-route
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /claude
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            remove:
            - x-api-key
            - authorization
      backendRefs:
        - name: claude-direct-apikey-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "540s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: claude-direct-apikey-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      anthropic: {}
  policies:
    auth:
      secretRef:
        name: claude-direct-apikey
    ai:
      routes:
        "/v1/messages": "Messages"
        "/v1/models": "Passthrough"
        "*": "Passthrough"
EOF
```

### Export the Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### Test the Route with curl

```bash
curl -i "$GATEWAY_IP:8080/claude/v1/messages" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1024,
    "messages": [
      {
        "role": "user",
        "content": "Explain what an API Gateway does in one sentence."
      }
    ]
  }'
```

### Configure and Launch Claude Code

```bash
export ANTHROPIC_BASE_URL="http://$GATEWAY_IP:8080/claude"
claude
```

### Cleanup

```bash
kubectl delete httproute -n agentgateway-system claude-directapikey-route
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system claude-direct-apikey-backend
kubectl delete secret -n agentgateway-system claude-direct-apikey
unset ANTHROPIC_BASE_URL CLAUDE_API_KEY GATEWAY_IP
```

---

## Observability

These steps apply to both Option A and Option B. For Option B, substitute `$GATEWAY_IP:8080/claude` wherever `$CLAUDE_GATEWAY_IP:4040` appears in the examples.

### View Metrics in Grafana

Use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../installation/002-set-up-ui-and-monitoring-tools.md) to visualize metrics.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

The dashboard provides real-time visualization of:
- Core GenAI metrics (request rates, token usage by model)
- Claude Code specific metrics showing `claude-3-5-sonnet` or `claude-3-5-haiku` model usage
- Streaming metrics (TTFT, TPOT)
- Connection and runtime metrics

### View Traces in the Solo UI

Distributed traces for Claude Code requests are surfaced in the Solo UI deployed in the [monitoring tools lab](../installation/002-set-up-ui-and-monitoring-tools.md). The UI ingests OTLP spans from AgentGateway via its built-in OpenTelemetry collector and stores them in ClickHouse.

1. Port-forward to the Solo UI service:
```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
```

2. Open http://localhost:4000 in your browser

3. Click **Tracing** in the left nav

![solo-ui-tracing-1.png](images/claude-desktop/solo-ui-tracing-1.png)

You will see a table of recent spans with the following columns:

| Column | Example |
|---|---|
| **Trace ID** | `18a3b8ec4acf4293f36cf85eb18d0dc6` |
| **Name** | `POST /claude/*` |
| **User ID** | populated when JWT-based authn is configured (otherwise `N/A`) |
| **Start Time** / **Duration** | per-request latency |
| **MCP/LLM Payload** | click to open the **AI Payload** drawer with the full `prompt` and `completion` JSON |
| **Route** | `agentgateway-system/claude-subscription-route` (Option A) or `agentgateway-system/claude-directapikey-route` (Option B) |
| **Input Tokens** / **Output Tokens** | per-request token counts |

Use the **search spans** box at the top to filter, the time-range selector to scope the window, and the route column to confirm Claude Code traffic is hitting the route you created in this lab.

4. Click any row to open the trace detail view

![solo-ui-tracing-2.png](images/claude-desktop/solo-ui-tracing-2.png)

The detail view gives you three coordinated panels:

- **Execution Flow** — a visual `Start → POST /claude/* → End` graph of the request through the gateway
- **Trace Tree** — the underlying span hierarchy
- **Span Details** — the full OpenTelemetry attributes for the selected span, including the gen-AI semantic conventions emitted by AgentGateway: `operation: "chat"`, `provider: "anthropic"`, `request.model`, `request.max_tokens`, `response.model`, and `usage.input_tokens` / `usage.output_tokens` / `usage.cache_creation` / `usage.cache_read`

Cross-reference the **Trace ID** with the access logs (next section) to jump from a single log line directly to its full prompt/completion payload and span attributes.

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout. You can tail the logs to see Claude Code traffic flowing through:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Example output shows comprehensive request details including:
- Model information (e.g., `claude-3-5-sonnet-20241022`)
- Token usage (input and output tokens)
- Request duration
- Trace IDs for correlation with Solo UI traces
- Full request and response bodies

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

Look for metrics like:
- `agentgateway_gen_ai_client_token_usage` - Token usage by model and type
- `agentgateway_gen_ai_server_request_duration` - Request latency for Claude API calls
- `agentgateway_requests_total` - HTTP request counts