# Configure Routing to Groq

In this lab, you'll route to [Groq](https://console.groq.com/), an OpenAI-compatible inference provider, using the `openai` provider with a host override.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- A Groq API key from [console.groq.com/keys](https://console.groq.com/keys) (the free tier is enough for this lab)

## Lab Objectives
- Create a Kubernetes secret that contains our Groq api-key credentials
- Create a route to Groq as our backend LLM provider using an `EnterpriseAgentgatewayBackend` and `HTTPRoute`
- Point the `openai` provider at a non-OpenAI host with `host`, `port`, and `pathPrefix`, and originate TLS to it
- Curl Groq through the agentgateway proxy
- Validate the request went through the gateway in the Grafana UI

## Why the `openai` Provider?

Groq serves the OpenAI Chat Completions API at `https://api.groq.com/openai/v1/chat/completions`. There is no dedicated `groq` provider. Because the wire format is identical to OpenAI's, you use `provider.openai` and redirect it at Groq's endpoint. The gateway still parses the request and response as OpenAI traffic, so token counts, model names, streaming metrics, and prompt/completion logging work as they do in the [OpenAI lab](configure-routing-openai.md).

These provider fields redirect the traffic:

| Setting | Value | Description |
|---|---|---|
| `host` / `port` | `api.groq.com` / `443` | The upstream endpoint. Both must be set together; the CRD rejects one without the other. |
| `pathPrefix` | `/openai/v1` | Replaces the `openai` provider's built-in `/v1` prefix. The gateway appends the operation suffix (`/chat/completions`), producing `/openai/v1/chat/completions`. |
| `policies.tls.sni` | `api.groq.com` | Originates TLS to the upstream and sets the SNI server name. Validates against the system trust store, which is correct for a public CA like Groq's. |

> **Both the prefix and the TLS policy are required.** Omit `policies.tls.sni` and the gateway sends cleartext to port `443`, so Cloudflare answers `400 The plain HTTP request was sent to HTTPS port`. Set `pathPrefix: /openai` instead of `/openai/v1` and the `/v1` segment is lost, so Groq answers `404 Unknown request URL: POST /openai/chat/completions`.

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

Create groq route and backend
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
        - name: groq-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: groq-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      host: api.groq.com
      port: 443
      pathPrefix: /openai/v1
      openai: {}
        #--- Uncomment to configure model override ---
        #model: ""
  policies:
    auth:
      secretRef:
        name: groq-secret
    tls:
      sni: api.groq.com
EOF
```

Verify that the backend and route were accepted:
```bash
kubectl get enterpriseagentgatewaybackend groq-all-models -n agentgateway-system \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].message}{"\n"}'
kubectl get httproute groq -n agentgateway-system \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}{"\n"}'
```

Expected output:
```
Backend successfully accepted
True
```

## curl groq
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/groq" \
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

The backend routes every model Groq serves. Swap the `model` field to reach another one, for example `llama-3.3-70b-versatile`, or pin the backend to a single model by uncommenting `provider.openai.model`, which overrides whatever the client sends.

Streaming works over the same route: add `"stream": true` to the body and the gateway relays Groq's SSE chunks while recording TTFT and TPOT metrics.

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

> **Note: Groq traffic is labeled as OpenAI.** Because the backend uses the `openai` provider, metrics carry `gen_ai_system="openai"` and access logs and spans carry `gen_ai.provider.name=openai`, both alongside `gen_ai.request.model=openai/gpt-oss-20b`. To isolate Groq traffic, filter dashboards and queries by model or by `route="agentgateway-system/groq"` rather than by provider. Cost is also reported as `total_cost_usd="0"`: the base model catalog prices OpenAI, Anthropic, and Gemini models, and Groq's model IDs aren't in it. Add them yourself with a custom catalog, as covered in the [LLM Cost Management lab](../observability/llm-cost-management.md).

### View Traces in the Solo UI

To view distributed traces with LLM-specific spans:

1. Port-forward to the Solo UI:
```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
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
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system groq-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system groq-secret --ignore-not-found
unset GROQ_API_KEY
```
