# Configure Routing by Match Type

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Configure an `AgentgatewayModel` per model that pins requests to that model
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

A catch-all `AgentgatewayModel` (`match.model: "*"`) forwards whatever `model` the client puts in the request body. To restrict a route to one model, pin it with a body transformation that overwrites the `model` field:
```
match:
  model: "*"
provider: OpenAI
policies:
  transformations:
    - field: model
      expression: '"gpt-5.4-nano"'
```
With a pin in place, the gateway replaces any client-supplied `model` (e.g. if the user supplies `model: gpt-5-2025-08-07`, the gateway sends `gpt-5.4-nano`). The request body still needs a `model` field, because the gateway reads it before applying the pin.

Create one `AgentgatewayModel` per model for finer-grained control over which models clients can access. Each model attaches to one rule of the `openai` `HTTPRoute` through `sectionName`, so the rule decides which requests reach the model. Every option below names its rules `gpt-5.4-mini`, `gpt-5.4-nano` and `gpt-5.6-terra`, so these models work with all three options. A model reports `Accepted` once the route with its rule exists.

Create an OpenAI `AgentgatewayModel` per specific model:
```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-gpt-5.4-mini
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
      sectionName: gpt-5.4-mini
  match:
    model: "*"
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
    transformations:
      - field: model
        expression: '"gpt-5.4-mini"'
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-gpt-5.4-nano
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
      sectionName: gpt-5.4-nano
  match:
    model: "*"
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
    transformations:
      - field: model
        expression: '"gpt-5.4-nano"'
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-gpt-5.6-terra
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
      sectionName: gpt-5.6-terra
  match:
    model: "*"
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
    transformations:
      - field: model
        expression: '"gpt-5.6-terra"'
EOF
```

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

Each option below configures the same `openai` `HTTPRoute` with a different match type. Applying a later option's `HTTPRoute` replaces the previous one, so you can try them one at a time against the models created above.

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
    - name: gpt-5.4-mini
      matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-5.4-mini
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: gpt-5.4-nano
      matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-5.4-nano
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: gpt-5.6-terra
      matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-5.6-terra
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
EOF
```

curl each model's path:
```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-5.4-mini/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.4-mini-2026-03-17`

```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-5.4-nano/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.4-nano-2026-03-17`

```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-5.6-terra/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.6-terra",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.6-terra`

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
    - name: gpt-5.4-mini
      matches:
        - path:
            type: PathPrefix
            value: /openai
          headers:
          - type: Exact
            name: model
            value: gpt-5.4-mini
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: gpt-5.4-nano
      matches:
        - path:
            type: PathPrefix
            value: /openai
          headers:
          - type: Exact
            name: model
            value: gpt-5.4-nano
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: gpt-5.6-terra
      matches:
        - path:
            type: PathPrefix
            value: /openai
          headers:
          - type: Exact
            name: model
            value: gpt-5.6-terra
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
EOF
```

curl `/openai` with the `model` header set:
```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -H "model: gpt-5.4-mini" \
  -d '{
    "model": "gpt-5.4-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.4-mini-2026-03-17`

```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -H "model: gpt-5.4-nano" \
  -d '{
    "model": "gpt-5.4-nano",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.4-nano-2026-03-17`

```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -H "model: gpt-5.6-terra" \
  -d '{
    "model": "gpt-5.6-terra",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.6-terra`

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
    - name: gpt-5.4-mini
      matches:
        - path:
            type: PathPrefix
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-5.4-mini
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: gpt-5.4-nano
      matches:
        - path:
            type: PathPrefix
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-5.4-nano
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: gpt-5.6-terra
      matches:
        - path:
            type: PathPrefix
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-5.6-terra
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
EOF
```

curl `/openai` with the `model` query parameter set:
```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions?model=gpt-5.4-mini" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.4-mini-2026-03-17`

```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions?model=gpt-5.4-nano" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.4-nano-2026-03-17`

```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions?model=gpt-5.6-terra" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.6-terra",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The response shows the model used: `gpt-5.6-terra`

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../../002-set-up-monitoring-tools.md). For traces, use the AgentGateway UI.

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

### View Traces in the Solo UI

To view distributed traces with LLM-specific spans:

1. Port-forward to the Solo UI:
```bash
kubectl port-forward -n agentgateway-system svc/enterprise-agentgateway-ui 4000:80
```

2. Open http://localhost:4000 in your browser

3. Click **Tracing** in the left navigation

4. Use the **Search spans** box or the time-range buttons to find your requests, then click a row to open its span details

Each span carries LLM attributes including `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and per-request cost under `agw.ai.usage.cost`. Spans also carry message content: the first user message as `llm.prompt.user` and the response text as `llm.completion.output`. The access logs record tokens and cost but no message text, because `001` ships the `llm_prompt` and `llm_completion` log attributes commented out.

### View Access Logs

The gateway logs every LLM request to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

The log line carries the model and token counts, plus a `trace.id` you can search for in the Solo UI's **Tracing** view.

### (Optional) View Traces in Jaeger

If you installed Jaeger in the [002 — Set Up Monitoring Tools (OCP)](../installation/openshift/002-set-up-monitoring-tools-ocp.md) lab instead of the Solo UI, you can view traces in the Jaeger UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see the traces. Each span carries LLM attributes including `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, and `gen_ai.usage.output_tokens`, plus per-request cost under `agw.ai.usage.cost`. Spans also carry message content: the first user message as `llm.prompt.user` and the response text as `llm.completion.output`. The access logs record tokens and cost but no message text, because `001` ships the `llm_prompt` and `llm_completion` log attributes commented out

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete agentgatewaymodel -n agentgateway-system openai-gpt-5.6-terra
kubectl delete agentgatewaymodel -n agentgateway-system openai-gpt-5.4-nano
kubectl delete agentgatewaymodel -n agentgateway-system openai-gpt-5.4-mini
```
