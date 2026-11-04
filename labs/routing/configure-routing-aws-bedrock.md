# Configure Routing to AWS Bedrock Provider

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Create a Kubernetes secret that contains our AWS Access Key credentials
- Create a route to AWS Bedrock as our LLM provider using an `AgentgatewayModel` and `HTTPRoute`
- Curl AWS Bedrock through the agentgateway proxy
- Validate the request went through the gateway in the Grafana UI

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
  namespace: agentgateway-system
type: Opaque
stringData:
  accessKey: ${AWS_ACCESS_KEY_ID}
  secretKey: ${AWS_SECRET_ACCESS_KEY}
  sessionToken: ${AWS_SESSION_TOKEN}
EOF
```

Create the AWS Bedrock route and `AgentgatewayModel` resources. For this setup we will configure multiple models using a single provider (AWS Bedrock) in a path-per-model routing configuration
```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: bedrock-mistral
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: bedrock
      sectionName: bedrock-mistral
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: bedrock
      sectionName: bedrock-mistral-2
  match:
    model: "*"
  provider: Bedrock
  bedrock:
    region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
    transformations:
      - field: model
        expression: '"mistral.voxtral-mini-3b-2507"'
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: bedrock-haiku4.5
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: bedrock
      sectionName: bedrock-haiku4.5
  match:
    model: "*"
  provider: Bedrock
  bedrock:
    region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
    transformations:
      - field: model
        expression: '"us.anthropic.claude-haiku-4-5-20251001-v1:0"'
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: bedrock-opus
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: bedrock
      sectionName: bedrock-opus
  match:
    model: "*"
  provider: Bedrock
  bedrock:
    region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
    transformations:
      - field: model
        expression: '"us.anthropic.claude-opus-4-7"'
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: bedrock-sonnet
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: bedrock
      sectionName: bedrock-sonnet
  match:
    model: "*"
  provider: Bedrock
  bedrock:
    region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
    transformations:
      - field: model
        expression: '"us.anthropic.claude-sonnet-4-6"'
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: bedrock-llama3-8b
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: bedrock
      sectionName: bedrock-llama3-8b
  match:
    model: "*"
  provider: Bedrock
  bedrock:
    region: us-west-2
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
    transformations:
      - field: model
        expression: '"meta.llama3-1-8b-instruct-v1:0"'
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bedrock
  namespace: agentgateway-system
  labels:
    example: bedrock-route
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - name: bedrock-haiku4.5
      matches:
        - path:
            type: PathPrefix
            value: /bedrock/haiku
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: bedrock-opus
      matches:
        - path:
            type: PathPrefix
            value: /bedrock/opus
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: bedrock-sonnet
      matches:
        - path:
            type: PathPrefix
            value: /bedrock/sonnet
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: bedrock-mistral
      matches:
        - path:
            type: PathPrefix
            value: /bedrock/mistral
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    - name: bedrock-llama3-8b
      matches:
        - path:
            type: PathPrefix
            value: /bedrock/llama3-8b
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    # catch-all will route to the bedrock mistral upstream
    - name: bedrock-mistral-2
      matches:
        - path:
            type: PathPrefix
            value: /bedrock
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
EOF
```

## curl AWS Bedrock Mistral endpoint
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/bedrock/mistral/v1/chat/completions" \
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
curl -i "$GATEWAY_IP:8080/bedrock/haiku/v1/chat/completions" \
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

## curl AWS Bedrock Opus endpoint
```bash
curl -i "$GATEWAY_IP:8080/bedrock/opus/v1/chat/completions" \
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

## curl AWS Bedrock Sonnet endpoint
```bash
curl -i "$GATEWAY_IP:8080/bedrock/sonnet/v1/chat/completions" \
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
curl -i "$GATEWAY_IP:8080/bedrock/llama3-8b/v1/chat/completions" \
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
# `001` runs two proxy replicas and a request is only counted on the replica
# that served it, so scrape both.
for pod in $(kubectl get pods -n agentgateway-system \
    -l app.kubernetes.io/name=agentgateway-proxy -o name); do
  kubectl port-forward -n agentgateway-system "$pod" 15020:15020 >/dev/null 2>&1 &
  PF=$!
  sleep 2
  echo "--- $pod"
  curl -s http://localhost:15020/metrics
  kill "$PF" 2>/dev/null; wait "$PF" 2>/dev/null || true
done
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
kubectl delete httproute -n agentgateway-system bedrock
kubectl delete agentgatewaymodel -n agentgateway-system bedrock-mistral
kubectl delete agentgatewaymodel -n agentgateway-system bedrock-haiku4.5
kubectl delete agentgatewaymodel -n agentgateway-system bedrock-opus
kubectl delete agentgatewaymodel -n agentgateway-system bedrock-sonnet
kubectl delete agentgatewaymodel -n agentgateway-system bedrock-llama3-8b
kubectl delete secret -n agentgateway-system bedrock-secret
```