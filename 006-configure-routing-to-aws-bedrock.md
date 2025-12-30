# Configure Routing to AWS Bedrock Provider

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our AWS Access Key credentials
- Create a route to AWS Bedrock as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Curl AWS Bedrock through the agentgateway proxy
- Validate the request went through the gateway in Jaeger UI

## Export AWS Credentials
Log in to AWS console and export the following variables
```bash
export AWS_ACCESS_KEY_ID="<aws access key id>"
export AWS_SECRET_ACCESS_KEY="<aws secret access key>"
export AWS_SESSION_TOKEN="<aws session token>"
```

echo the vars to make sure that they were exported
```bash
echo $AWS_ACCESS_KEY_ID
echo $AWS_SECRET_ACCESS_KEY
echo $AWS_SESSION_TOKEN
```

Create a secret containing an AWS access key
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: bedrock-secret
  namespace: enterprise-agentgateway
type: Opaque
stringData:
  accessKey: ${AWS_ACCESS_KEY_ID}
  secretKey: ${AWS_SECRET_ACCESS_KEY}
  sessionToken: ${AWS_SESSION_TOKEN}
EOF
```

Create AWS Bedrock route and `AgentgatewayBackend`. For this setup we will configure multiple `AgentgatewayBackends` using a single provider (AWS Bedrock) in a path-per-model routing configuration
```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: bedrock-titan
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      bedrock:
        model: amazon.titan-tg1-large
        region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: bedrock-haiku3.5
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      bedrock:
        model: anthropic.claude-3-5-haiku-20241022-v1:0
        region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: bedrock-llama3-8b
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      bedrock:
        model: meta.llama3-1-8b-instruct-v1:0
        region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bedrock
  namespace: enterprise-agentgateway
  labels:
    example: bedrock-route
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /bedrock/haiku
      backendRefs:
        - name: bedrock-haiku3.5
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /bedrock/titan
      backendRefs:
        - name: bedrock-titan
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /bedrock/llama3-8b
      backendRefs:
        - name: bedrock-llama3-8b
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
    # catch-all will route to the bedrock titan upstream
    - matches:
        - path:
            type: Exact
            value: /bedrock
      backendRefs:
        - name: bedrock-titan
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

## curl AWS Bedrock Titan endpoint
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/bedrock/titan" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## curl AWS Bedrock Haiku endpoint
```bash
curl -i "$GATEWAY_IP:8080/bedrock/haiku" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## curl AWS Bedrock llama3-8b endpoint
```bash
curl -i "$GATEWAY_IP:8080/bedrock/llama3-8b" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
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

If you installed Jaeger in lab 002 instead of Tempo, you can view traces directly:

```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans including `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway bedrock
kubectl delete agentgatewaybackend -n enterprise-agentgateway bedrock-titan
kubectl delete agentgatewaybackend -n enterprise-agentgateway bedrock-haiku3.5
kubectl delete agentgatewaybackend -n enterprise-agentgateway bedrock-llama3-8b
kubectl delete secret -n enterprise-agentgateway bedrock-secret
```