# Configure Basic Routing to OpenAI

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Validate the request went through the gateway in the Grafana UI

### Configure Required Variables
Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and backend
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai-all-models
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      openai: {}
        #--- Uncomment to configure model override ---
        #model: ""
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

### View Metrics and Traces in Grafana

For a comprehensive view of metrics and traces, use the AgentGateway Grafana dashboard installed in lab 002.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Overview** to view metrics

The dashboard provides real-time visualization of:
- Core GenAI metrics (request rates, token usage by model)
- Streaming metrics (TTFT, TPOT)
- MCP metrics (tool calls, server requests)
- Connection and runtime metrics

### View Traces in Grafana

To view distributed traces with LLM-specific spans:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Filter traces by service, operation, or trace ID to find AgentGateway requests

Traces include LLM-specific spans with information like `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more.

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout:

```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 1
```

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

### (Optional) View Traces in Jaeger

If you installed Jaeger in lab `/install-on-openshift/002-set-up-monitoring-tools-ocp.md` instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans including `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret
```