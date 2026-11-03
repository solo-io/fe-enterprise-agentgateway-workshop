# Configure Routing to Groq

In this lab, you'll route to [Groq](https://console.groq.com/), an OpenAI-compatible inference provider, using the built-in `Groq` provider.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.
- A Groq API key from [console.groq.com/keys](https://console.groq.com/keys) (the free tier is enough for this lab)

## Lab Objectives
- Create a Kubernetes secret that contains our Groq api-key credentials
- Create a route to Groq as our LLM provider using an `AgentgatewayModel` and `HTTPRoute`
- Curl Groq through the agentgateway proxy
- Validate the request went through the gateway in the Grafana UI

## The `Groq` Provider

Groq serves the OpenAI Chat Completions API at `https://api.groq.com/openai/v1/chat/completions`. Setting `provider: Groq` on the model fills in that host, the `/openai/v1` base path, and TLS to port `443`, so the model needs only the provider name and the API key. For an OpenAI-compatible service without a built-in provider, set `provider: OpenAI` and point `baseURL` at the service, for example `baseURL: https://api.example.com/v1`.

### Configure Required Variables
Replace with a valid Groq API key
```bash
export GROQ_API_KEY=$GROQ_API_KEY
```

Create groq api-key secret
```bash
kubectl create secret generic groq-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $GROQ_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create groq route and model
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: groq
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /groq
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
  name: groq-all-models
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: groq
  match:
    model: "*"
  provider: Groq
  policies:
    auth:
      secretRef:
        name: groq-secret
    #--- Uncomment to always send one model, whatever the client requests ---
    #transformations:
    #  - field: model
    #    expression: '"openai/gpt-oss-20b"'
EOF
```

Verify that the model and route were accepted:
```bash
kubectl get agentgatewaymodel groq-all-models -n agentgateway-system \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].message}{"\n"}'
kubectl get httproute groq -n agentgateway-system \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}{"\n"}'
```

Expected output:
```
Successfully accepted AgentgatewayModel
True
```

## curl groq
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/groq/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "openai/gpt-oss-20b",
    "messages": [
      {
        "role": "user",
        "content": "Explain quantum computing in one sentence."
      }
    ],
    "temperature": 0.7
  }'
```

Expected output (truncated):
```
HTTP/1.1 200 OK
content-type: application/json
x-ratelimit-remaining-tokens: 7921
...
{"model":"openai/gpt-oss-20b","service_tier":"on_demand","usage":{"prompt_tokens":78,"completion_tokens":143,...},"choices":[{"message":{"content":"Quantum computing uses qubits that can exist in multiple states simultaneously...","role":"assistant"}...
```

The client sends no `Authorization` header. The gateway attaches the key from `groq-secret`, so callers reach Groq while the credential stays in the cluster.

The model routes every model Groq serves. Swap the `model` field to reach another one, for example `llama-3.3-70b-versatile`, or pin the route to a single model by uncommenting the `transformations` block, which overrides whatever the client sends.

Streaming works over the same route: add `"stream": true` to the body and the gateway relays Groq's SSE chunks while recording TTFT and TPOT metrics.

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

> **Note: Groq traffic is labeled `groq`.** Metrics carry `gen_ai_system="groq"` and access logs and spans carry `gen_ai.provider.name=groq`, alongside `gen_ai.request.model=openai/gpt-oss-20b`, so you can filter dashboards and queries by provider. The access log also reports per-request cost under `agw.ai.usage.cost.total` for Groq models in the base model catalog. To price a model the catalog lacks, add it with a custom catalog, as covered in the [LLM Cost Management lab](../observability/llm-cost-management.md).

### View Traces in the Solo UI

To view distributed traces with LLM-specific spans:

1. Port-forward to the Solo UI:
```bash
kubectl port-forward -n agentgateway-system svc/enterprise-agentgateway-ui 4000:80
```

2. Open http://localhost:4000 in your browser

3. Click **Tracing** in the left navigation

4. Use the **Search spans** box or the time-range buttons to find your requests, then click a row to open its span details

Each span carries LLM attributes including `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and per-request cost under `agw.ai.usage.cost`. Prompt and completion text stays out of spans; the access logs carry it as `llm.prompt` and `llm.completion`.

### View Access Logs

The gateway logs every LLM request to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

The log line carries the upstream endpoint (`endpoint=api.groq.com:443`), the model and token counts, and a `trace.id` you can search for in the Solo UI's **Tracing** view. Reasoning models like `openai/gpt-oss-20b` also report `llm_reasoning_tokens`, which is included in `completion_tokens`.

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system groq --ignore-not-found
kubectl delete agentgatewaymodel -n agentgateway-system groq-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system groq-secret --ignore-not-found
unset GROQ_API_KEY
```
