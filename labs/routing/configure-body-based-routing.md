# Configure Body-Based Routing

In this lab, you'll configure **body-based routing** to dispatch incoming LLM requests to different providers based on the `model` field in the JSON request body. Each `AgentgatewayModel` declares the model name it serves in `match.model`, and the gateway reads the request body and sends the request to the model whose name matches. A catch-all model handles every other name. Clients change only the `model` value they already send.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Deploy a mock LLM server to act as a second backend
- Create an OpenAI secret and one `AgentgatewayModel` per routing target
- Create an `HTTPRoute` whose rule selects the models attached to it
- Curl the gateway with different model values to observe routing behavior

## Architecture

```
Client Request
    │  body: { "model": "gpt-5.4-nano" | "mock-gpt-4o" | <any other name> }
    ▼
HTTPRoute body-based-routing (/openai) → AgentgatewayModel match.model
    ├─ gpt-5.4-nano   →  OpenAI (gpt-5.4-nano)
    ├─ mock-gpt-4o    →  Mock LLM Server
    └─ *              →  Mock LLM Server (fallback for any other name)
```

## Deploy the Mock LLM Server

Deploy the [vLLM Simulator](https://github.com/llm-d/llm-d-inference-sim) as a lightweight OpenAI-compatible backend. This is our second routing target alongside OpenAI.

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-4o
  template:
    metadata:
      labels:
        app: mock-gpt-4o
    spec:
      containers:
      - args:
        - --model
        - mock-gpt-4o
        - --port
        - "8000"
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        name: vllm-sim
        env:
          - name: POD_NAME
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.name
          - name: POD_NAMESPACE
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.namespace
        ports:
        - containerPort: 8000
          name: http
          protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpt-4o-svc
  namespace: agentgateway-system
  labels:
    app: mock-gpt-4o
spec:
  selector:
    app: mock-gpt-4o
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
      name: http
  type: ClusterIP
EOF
```

## Configure Required Variables

Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create OpenAI API key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

## Create the Route and Models

Create one `HTTPRoute` rule on `/openai` and three models attached to it:

1. `openai-gpt-5.4-nano` serves requests for `gpt-5.4-nano` from OpenAI
2. `mock-gpt-4o` serves requests for `mock-gpt-4o` from the mock server
3. `fallback-mock` matches every other model name with `match.model: "*"` and sends it to the mock server, pinned to `mock-gpt-4o`

An exact `match.model` takes precedence over the `*` wildcard, so the fallback only receives names that no other model claims.

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: body-based-routing
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
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
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
      name: body-based-routing
  match:
    model: gpt-5.4-nano
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: body-based-routing
  match:
    model: mock-gpt-4o
  provider: OpenAI
  baseURL: http://mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: fallback-mock
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: body-based-routing
  match:
    model: "*"
  provider: OpenAI
  baseURL: http://mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
    transformations:
      - field: model
        expression: '"mock-gpt-4o"'
EOF
```

## Test Body-Based Routing

Set the gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "Gateway IP: $GATEWAY_IP"
```

### Route to OpenAI

Send a request with `"model": "gpt-5.4-nano"`. The `openai-gpt-5.4-nano` model matches the name and sends the request to OpenAI.

```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
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

The response `model` field should show `gpt-5.4-nano-2026-03-17` (the OpenAI resolved version).

### Route to Mock LLM

Send a request with `"model": "mock-gpt-4o"`. The `mock-gpt-4o` model sends it to the local mock server instead.

```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

The response `model` field should show `mock-gpt-4o`: the mock LLM server served the request.

### Fallback: Unknown Model

Send a request for a model that no exact `match.model` claims. The `fallback-mock` model matches it through `*` and sends it to the mock server.

```bash
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "my-unlisted-model",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

The response `model` field should show `mock-gpt-4o`: the fallback model rewrote the unknown name. A request with no `model` field at all gets `HTTP 400` with `missing_model`, because the gateway needs the field to pick a model.

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
kubectl delete agentgatewaymodel -n agentgateway-system fallback-mock
kubectl delete agentgatewaymodel -n agentgateway-system mock-gpt-4o
kubectl delete agentgatewaymodel -n agentgateway-system openai-gpt-5.4-nano
kubectl delete httproute -n agentgateway-system body-based-routing
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete svc -n agentgateway-system mock-gpt-4o-svc
kubectl delete deploy -n agentgateway-system mock-gpt-4o
```
