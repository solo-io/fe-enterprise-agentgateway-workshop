# Configure Enterprise AgentGateway for Claude Code

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our Anthropic API key credentials
- Create a passthrough route to Anthropic as our backend LLM provider using a `Backend` and `HTTPRoute`
- Test the route with curl to verify connectivity
- Use Claude Code CLI through the agentgateway proxy
- Validate Claude Code requests in Grafana UI and access logs

## Overview

This lab configures Enterprise AgentGateway with passthrough routing to Anthropic's Claude API. The configuration supports multiple endpoints including:

- **Native Anthropic API** (`/v1/messages`) - Claude's native message format used by Claude Code CLI
- **Additional endpoints** (`/v1/models`, `*`) - All other API endpoints pass through

This makes the gateway compatible with Claude Code CLI and other Anthropic API clients, enabling observability, rate limiting, and guardrails for all Claude interactions.

## Configure Required Variables
Replace with a valid Anthropic API key
```bash
export CLAUDE_API_KEY=<your-anthropic-api-key>
```

Create anthropic api-key secret
```bash
kubectl create secret generic anthropic-secret -n enterprise-agentgateway \
--from-literal="Authorization=$CLAUDE_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

## Create Anthropic Route and Backend

Create the HTTPRoute and AgentgatewayBackend with passthrough configuration:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: claude-passthrough
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
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
        - name: anthropic-passthrough
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: anthropic-passthrough
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      anthropic: {}
  policies:
    auth:
      secretRef:
        name: anthropic-secret
    ai:
      routes:
        "/v1/messages": "Messages"
        "/v1/models": "Passthrough"
        "*": "Passthrough"
EOF
```

The `policies.ai.routes` configuration allows you to route different Anthropic API endpoints through the gateway:
- `/v1/messages`: `"Messages"` - The native Anthropic messages API (used by Claude Code CLI)
- `/v1/models`: `"Passthrough"` - Proxies model listing requests
- `*`: `"Passthrough"` - Default passthrough for any other paths

## Test Anthropic Route with curl

Export the gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

Test the Anthropic native `/v1/messages` endpoint:
```bash
curl -i "$GATEWAY_IP:8080/claude/v1/messages" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-3-5-haiku-latest",
    "max_tokens": 1024,
    "messages": [
      {
        "role": "user",
        "content": "Explain what an API Gateway does in one sentence."
      }
    ]
  }'
```

You should see a successful response from Claude with the completion.

## Use Claude Code CLI Through the Gateway

Now that the route is configured, you can use Claude Code CLI through the Enterprise AgentGateway.

### Configure Claude Code

Set the base URL to point to the gateway:
```bash
export ANTHROPIC_BASE_URL="http://$GATEWAY_IP:8080/claude"
```

**Note for Vertex AI users**: If you were previously using Claude Code with Vertex AI, unset the following variable first:
```bash
unset CLAUDE_CODE_USE_VERTEX
```

### Launch Claude Code

Launch Claude Code CLI (it will automatically use `ANTHROPIC_BASE_URL`):
```bash
claude
```

All your Claude Code sessions will now route through the Enterprise AgentGateway, enabling observability, rate limiting, and guardrails.

Try asking Claude Code a question to generate some traffic:
```
> what is kubernetes?
```

## Observability

### View Metrics and Traces in Grafana

For a comprehensive view of Claude Code traffic metrics and traces, use the AgentGateway Grafana dashboard installed in lab 002.

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
kubectl logs deploy/agentgateway -n enterprise-agentgateway -f | jq .
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
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
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

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway claude-passthrough
kubectl delete agentgatewaybackend -n enterprise-agentgateway anthropic-passthrough
kubectl delete secret -n enterprise-agentgateway anthropic-secret
```
