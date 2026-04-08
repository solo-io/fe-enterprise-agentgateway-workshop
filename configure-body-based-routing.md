# Configure Body-Based Routing

In this lab, you'll configure **body-based routing** to automatically dispatch incoming LLM requests to different backends based on the `model` field in the JSON request body. An `EnterpriseAgentgatewayPolicy` extracts the model name at request time and promotes it to a request header; a standard `HTTPRoute` then uses header matching to route to either OpenAI or a local mock LLM server — no client-side changes required.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy a mock LLM server to act as a second backend
- Create an OpenAI secret and per-model `AgentgatewayBackend` resources
- Configure an `EnterpriseAgentgatewayPolicy` to extract the `model` field from the request body and promote it to a request header
- Create an `HTTPRoute` that uses header matching to route requests to either OpenAI or the mock LLM based on the extracted model name
- Curl the gateway with different model values to observe routing behavior

## Architecture

```
Client Request
    │  body: { "model": "gpt-4o-mini" | "mock-gpt-4o" | <missing> }
    ▼
AgentgatewayPolicy (phase: PreRouting, Request Transformation)
    │  X-Gateway-Model-Name   = json(request.body).model
    │  X-Gateway-Model-Status = "specified" | "unspecified"
    ▼
HTTPRoute (header match)
    ├─ X-Gateway-Model-Name: gpt-4o-mini    →  OpenAI (gpt-4o-mini)
    ├─ X-Gateway-Model-Name: mock-gpt-4o    →  Mock LLM Server
    └─ X-Gateway-Model-Status: unspecified  →  Mock LLM Server (fallback)
```

## Deploy the Mock LLM Server

Deploy the [vLLM Simulator](https://github.com/llm-d/llm-d-inference-sim) as a lightweight OpenAI-compatible backend. This will serve as our second routing target alongside OpenAI.

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

## Create AgentgatewayBackend Resources

Create one backend for each routing target: OpenAI (`gpt-4o-mini`) and the mock LLM server.

```bash
kubectl apply -f - <<EOF
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-gpt-4o-mini
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: "gpt-4o-mini"
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-4o"
      host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth:
      passthrough: {}
EOF
```

## Configure Body-Based Routing

### Step 1: Extract Model from Request Body

Apply an `AgentgatewayPolicy` with `phase: PreRouting` that reads the `model` field from the JSON request body using CEL expressions and injects two headers. The `PreRouting` phase is critical — it ensures headers are set before the `HTTPRoute` makes its matching decision.

- `x-gateway-model-name` — the value of `model` from the request body
- `x-gateway-model-status` — `specified` if the `model` field is present, `unspecified` if it is missing

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: extract-model-from-body
  namespace: agentgateway-system
spec:
  targetRefs:
    - name: agentgateway-proxy
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    phase: PreRouting
    transformation:
      request:
        set:
          - name: x-gateway-model-name
            value: "json(request.body).model"
          - name: x-gateway-model-status
            value: "default(json(request.body).model, '') == '' ? 'unspecified' : 'specified'"
EOF
```

### Step 2: Route on the Extracted Headers

Create the `HTTPRoute` with three rules:
1. Known model `gpt-4o-mini` → OpenAI
2. Known model `mock-gpt-4o` → Mock LLM
3. `x-gateway-model-status: unspecified` → Mock LLM (fallback for requests with no `model` field)

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
          headers:
            - type: Exact
              name: x-gateway-model-name
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
          headers:
            - type: Exact
              name: x-gateway-model-name
              value: mock-gpt-4o
      backendRefs:
        - name: mock-gpt-4o
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          headers:
            - type: Exact
              name: x-gateway-model-status
              value: unspecified
      backendRefs:
        - name: mock-gpt-4o
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

## Test Body-Based Routing

Set the gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "Gateway IP: $GATEWAY_IP"
```

### Route to OpenAI

Send a request with `"model": "gpt-4o-mini"` — the gateway extracts this value from the body and routes to the OpenAI backend.

```bash
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

The response `model` field should show `gpt-4o-mini-2024-07-18` (the OpenAI resolved version).

### Route to Mock LLM

Send a request with `"model": "mock-gpt-4o"` — the gateway routes this to the local mock server instead.

```bash
curl -i "$GATEWAY_IP:8080/openai" \
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

The response `model` field should show `mock-gpt-4o`, confirming the request was served by the mock LLM backend.

### Fallback — No Model in Body

Send a request with **no `model` field**. The policy sets `x-gateway-model-status: unspecified`, and the fallback rule routes it to the mock LLM.

```bash
curl -i "$GATEWAY_IP:8080/openai" \
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

The response `model` field should show `mock-gpt-4o`, confirming the fallback route was matched via `x-gateway-model-status: unspecified`.

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

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

If you installed Jaeger in lab `/install-on-openshift/002-set-up-monitoring-tools-ocp.md` instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans including `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete agentgatewaypolicy -n agentgateway-system extract-model-from-body
kubectl delete httproute -n agentgateway-system body-based-routing
kubectl delete agentgatewaybackend -n agentgateway-system openai-gpt-4o-mini
kubectl delete agentgatewaybackend -n agentgateway-system mock-gpt-4o
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete svc -n agentgateway-system mock-gpt-4o-svc
kubectl delete deploy -n agentgateway-system mock-gpt-4o
```
