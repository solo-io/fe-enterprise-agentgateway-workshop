# Configure Routing by Match Type

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Configure an `EnterpriseAgentgatewayBackend` per model using the model override parameter
- Configure LLM routing using three `HTTPRoute` match types: path, header, and query parameter
- Curl OpenAI endpoints through the agentgateway proxy for each match type
- Validate path to model mapping
- Cleanup routes to start fresh for the next lab

## Setup

Create the OpenAI api-key secret if it has not been created already
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Our default `EnterpriseAgentgatewayBackend` allows the user to specify any `model` parameter in the request body. To restrict access to specific models, configure a model override in the `EnterpriseAgentgatewayBackend`:
```
provider:
  openai:
    model: "gpt-4o-mini"
```
When a model override is configured, the gateway overrides any user-input `model` parameter in the request body (e.g. if the user supplies `model: gpt-5-2025-08-07`, the gateway overrides it to `gpt-4o-mini`).

Create an `EnterpriseAgentgatewayBackend` per model for finer-grained control over which models clients can access.

**When model overrides are specified, the client does not need to supply a `model` parameter in the request body, since the gateway injects it. A client-supplied model is accepted but overwritten.**

Lets create an OpenAI `EnterpriseAgentgatewayBackend` per specific-model if you haven't already
```bash
kubectl apply -f - <<EOF
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-gpt-3.5-turbo
  namespace: agentgateway-system
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
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-gpt-4o-mini
  namespace: agentgateway-system
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
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-gpt-4o
  namespace: agentgateway-system
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

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

Each option below configures the same `openai` `HTTPRoute` with a different match type. Applying a later option's `HTTPRoute` replaces the previous one, so you can try them one at a time against the backends created above.

## Option A: Path-per-Model Matching

Configure an `HTTPRoute` that maps a specific path to each model:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-3.5-turbo
      backendRefs:
        - name: openai-gpt-3.5-turbo
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-4o-mini
      backendRefs:
        - name: openai-gpt-4o-mini
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-4o
      backendRefs:
        - name: openai-gpt-4o
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

curl each model's path:
```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-3.5-turbo" \
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
The response shows the model used: `gpt-3.5-turbo-0125`

```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-4o-mini" \
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
The response shows the model used: `gpt-4o-mini-2024-07-18`

```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-4o" \
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
The response shows the model used: `gpt-4o-2024-08-06`

## Option B: Header Matching

Configure an `HTTPRoute` that matches on a fixed path plus a `model` header:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          headers:
          - type: Exact
            name: model
            value: gpt-3.5-turbo
      backendRefs:
        - name: openai-gpt-3.5-turbo
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          headers:
          - type: Exact
            name: model
            value: gpt-4o-mini
      backendRefs:
        - name: openai-gpt-4o-mini
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          headers:
          - type: Exact
            name: model
            value: gpt-4o
      backendRefs:
        - name: openai-gpt-4o
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

curl `/openai` with the `model` header set:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "model: gpt-3.5-turbo" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-3.5-turbo-0125`

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "model: gpt-4o-mini" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-4o-mini-2024-07-18`

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "model: gpt-4o" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-4o-2024-08-06`

## Option C: Query Parameter Matching

Configure an `HTTPRoute` that matches on a fixed path plus a `model` query parameter:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
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
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
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
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
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
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

curl `/openai` with the `model` query parameter set:
```bash
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
The response shows the model used: `gpt-3.5-turbo-0125`

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
The response shows the model used: `gpt-4o-mini-2024-07-18`

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
The response shows the model used: `gpt-4o-2024-08-06`

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../../002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

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
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

### (Optional) View Traces in Jaeger

If you installed Jaeger in the [002 — Set Up Monitoring Tools (OCP)](../installation/openshift/002-set-up-monitoring-tools-ocp.md) lab instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans including `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-gpt-4o
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-gpt-4o-mini
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-gpt-3.5-turbo
```
