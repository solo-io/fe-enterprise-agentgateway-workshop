# Configure Basic Routing to Azure OpenAI

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our Azure OpenAI api-key credentials
- Create a route to Azure OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Curl Azure OpenAI through the agentgateway proxy
- Validate the request went through the gateway in the Grafana UI

### Configure Required Variables

Set the following environment variables to match your Azure OpenAI deployment.
For reference, an endpoint typically follows this format:
`https://${ENDPOINT}/openai/deployments/${DEPLOYMENT_NAME}/chat/completions?api-version=2024-02-01`

**Note:** The ENDPOINT should be just the hostname without the `https://` scheme (e.g., `my-endpoint.openai.azure.com`)

```bash
export AZURE_OPENAI_API_KEY="<API-KEY>"
export ENDPOINT="<AZURE-OPENAI-ENDPOINT>"  # Just the hostname, no https://
export DEPLOYMENT_NAME="<DEPLOYMENT-NAME>"
```

Create azure openai api-key secret
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: azureopenai-secret
  namespace: enterprise-agentgateway # Putting in same ns where the redis, ext auth is getting deployed
type: Opaque
stringData:
  Authorization: "Bearer ${AZURE_OPENAI_API_KEY}"
EOF
```

Create azure openai route and backend
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: azure-openai
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /azure
      backendRefs:
        - name: azure-openai
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: azure-openai
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      azureopenai:
        endpoint: "${ENDPOINT}"
        deploymentName: "${DEPLOYMENT_NAME}"
  policies:
    auth:
      secretRef:
        name: azureopenai-secret
EOF
```

## curl azure openai
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/azure" \
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
kubectl delete httproute -n enterprise-agentgateway azure-openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway azure-openai
kubectl delete secret -n enterprise-agentgateway azureopenai-secret
```