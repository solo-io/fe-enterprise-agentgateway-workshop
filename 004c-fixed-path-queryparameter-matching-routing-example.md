# Configure Fixed Path + Query Parameter Matching Routing Example

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Configure LLM routing example with fixed-path + queryparameter matcher to access endpoint
- Curl OpenAI endpoints through the agentgateway proxy
- Validate path to model mapping
- Cleanup routes to start fresh for the next lab

Create openai api-key secret if it has not been created already
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Lets create an OpenAI `AgentgatewayBackend` per specific-model if you haven't already
```bash
kubectl apply -f - <<EOF
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-gpt-3.5-turbo
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      openai:
        #--- Uncomment to configure model override ---
        model: "gpt-3.5-turbo"
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-gpt-4o-mini
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      openai:
        #--- Uncomment to configure model override ---
        model: "gpt-4o-mini"
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-gpt-4o
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      openai:
        #--- Uncomment to configure model override ---
        model: "gpt-4o"
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

### Configure LLM routing example with fixed path + header matching to access endpoints

Now we can configure a `HTTPRoute` that has a specific path-per-model endpoint using the same backends we used previously
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
          queryParams:
          - type: Exact
            name: model
            value: gpt-3.5-turbo
      backendRefs:
        - name: openai-gpt-3.5-turbo
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-4o-mini
      backendRefs:
        - name: openai-gpt-4o-mini
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-4o
      backendRefs:
        - name: openai-gpt-4o
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

## curl /openai with the "model=gpt-3.5-turbo" query parameter
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai?model=gpt-3.5-turbo" \
  -H "content-type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
We should see that the response shows that the model used was `gpt-3.5-turbo-0125`

## curl /openai with the "model=gpt-4o-mini" query parameter
```bash
curl -i "$GATEWAY_IP:8080/openai?model=gpt-4o-mini" \
  -H "content-type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
We should see that the response shows that the model used was `gpt-4o-mini-2024-07-18`

## curl /openai with the "model=gpt-4o" query parameter
```bash
curl -i "$GATEWAY_IP:8080/openai?model=gpt-4o" \
  -H "content-type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
We should see that the response shows that the model used was `gpt-4o-2024-08-06`

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
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete secret -n enterprise-agentgateway openai-secret
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-gpt-3.5-turbo
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-gpt-4o
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-gpt-4o-mini
```