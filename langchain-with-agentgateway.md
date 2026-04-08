# LangChain Multi-Agent Pipeline with AgentGateway

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Create a Kubernetes secret that contains your OpenAI API key credentials
- Create a route to OpenAI as the backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Run a LangChain two-agent pipeline (Researcher → Writer) whose LLM calls route through agentgateway
- Validate proxied requests in Grafana and access logs

## Overview

This lab shows how to run a [LangChain](https://www.langchain.com/) multi-agent pipeline through Enterprise AgentGateway. Two LangChain chains — a **Researcher** and a **Writer** — run sequentially to produce a short blog post on a chosen topic. All OpenAI API calls are intercepted by agentgateway, which:

- Injects the real OpenAI API key from a Kubernetes Secret
- Emits OpenTelemetry traces, metrics, and access logs for every LLM call
- Enables enforcement of rate limits, guardrails, and other policies — transparently, without any changes to the LangChain application

## Configure Required Variables

Export your OpenAI API key:
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create the OpenAI API key secret:
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
  --from-literal="Authorization=Bearer $OPENAI_API_KEY" \
  --dry-run=client -oyaml | kubectl apply -f -
```

## Create OpenAI Route and Backend

Apply the `AgentgatewayBackend` and `HTTPRoute`:
```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
---
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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

The `AgentgatewayBackend` matches requests on the `/openai` path prefix and forwards them to `api.openai.com` with the API key injected from the `openai-secret` Kubernetes Secret. LangChain's `ChatOpenAI` appends `/chat/completions` automatically, so the `base_url` is set to `http://$GATEWAY_IP:8080/openai/v1`.

## Get the Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo "Gateway IP: $GATEWAY_IP"
```

## Verify the Endpoint

Before running the pipeline, confirm the gateway is routing to OpenAI correctly:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Say hello"
      }
    ]
  }'
```

You should receive a `200 OK` response with a JSON body containing a `choices` array. If you see an error, check that the `openai-secret` was created correctly and the `HTTPRoute` is admitted.

## Run the LangChain Two-Agent Pipeline

The agent script and its dependencies live in `lib/langchain/multi-agent-researcher-writer/`. Install them in a local virtual environment:

```bash
python3 -m venv lib/langchain/multi-agent-researcher-writer/.venv
lib/langchain/multi-agent-researcher-writer/.venv/bin/pip install --upgrade pip -q
lib/langchain/multi-agent-researcher-writer/.venv/bin/pip install -r lib/langchain/multi-agent-researcher-writer/requirements.txt
```

Run the pipeline with your chosen topic:
```bash
GATEWAY_IP="$GATEWAY_IP" \
AGENT_TOPIC="AI Gateway key patterns and concepts" \
lib/langchain/multi-agent-researcher-writer/.venv/bin/python3 lib/langchain/multi-agent-researcher-writer/agent.py
```

You should see the Researcher chain produce bullet-point findings, followed by the Writer chain turning them into a polished blog post. All LLM calls flow through agentgateway.

Try a different topic:
```bash
GATEWAY_IP="$GATEWAY_IP" \
AGENT_TOPIC="Service Mesh key patterns and concepts" \
lib/langchain/multi-agent-researcher-writer/.venv/bin/python3 lib/langchain/multi-agent-researcher-writer/agent.py
```

## Observability

### View access logs

Tail agentgateway logs to see the proxied OpenAI calls:
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Each pipeline run generates two requests — one for the Researcher and one for the Writer — showing model name, token counts, and latency.

### View Metrics and Traces in Grafana

1. Port-forward to Grafana:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard**

The dashboard shows real-time data from the pipeline run, including:
- Token usage broken down by request type (input vs. output)
- Per-model request latency
- Total proxied request counts

### View Traces in Grafana

To see distributed traces for individual agent LLM calls:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces

Each chain invocation produces a trace with LLM-specific spans containing `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, and token counts.

### View the Prometheus metrics endpoint

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

Useful metrics:
- `agentgateway_gen_ai_client_token_usage` — token usage per agent call
- `agentgateway_gen_ai_server_request_duration` — latency per request
- `agentgateway_requests_total` — total proxied requests

## Cleanup

```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
rm -rf lib/langchain/multi-agent-researcher-writer/.venv
```
