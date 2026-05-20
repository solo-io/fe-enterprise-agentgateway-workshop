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
          group: agentgateway.dev
          kind: AgentgatewayBackend
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
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
kubectl delete agentgatewaybackend -n agentgateway-system claude-subscription-backend
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

Create the HTTPRoute and AgentgatewayBackend with passthrough configuration:
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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "540s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
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
kubectl delete agentgatewaybackend -n agentgateway-system claude-direct-apikey-backend
kubectl delete secret -n agentgateway-system claude-direct-apikey
unset ANTHROPIC_BASE_URL CLAUDE_API_KEY GATEWAY_IP
```

---

## Observability

These steps apply to both Option A and Option B. For Option B, substitute `$GATEWAY_IP:8080/claude` wherever `$CLAUDE_GATEWAY_IP:4040` appears in the examples.

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

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

### View Traces in Grafana

To view distributed traces with LLM-specific spans from Claude Code requests:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Filter traces by service, operation, or trace ID to find AgentGateway requests from Claude Code

Traces include LLM-specific spans with information like `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more.

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout. You can tail the logs to see Claude Code traffic flowing through:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Example output shows comprehensive request details including:
- Model information (e.g., `claude-3-5-sonnet-20241022`)
- Token usage (input and output tokens)
- Request duration
- Trace IDs for correlation with Grafana traces
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

### (Optional) View Traces in Jaeger

If you installed Jaeger in lab `/install-on-openshift/002-set-up-monitoring-tools-ocp.md` instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans.
