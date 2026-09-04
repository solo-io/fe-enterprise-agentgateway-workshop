# Configure Basic Routing to OpenAI

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Validate the request went through the gateway in the Grafana UI

### Configure Required Variables
Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and backend
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
      backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
        #--- Uncomment to configure model override ---
        #model: ""
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
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

### View Traces in the Solo UI

To view distributed traces with LLM-specific spans:

1. Port-forward to the Solo UI:
```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
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
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```