# Configure Semantic Routing with vLLM Semantic Router

In this lab, you'll route LLM requests by **prompt content** instead of by the model name the client asks for. Clients send one stable virtual model name, `auto_model`. [vLLM Semantic Router](https://vllm-sr.ai/) (vSR), called by the gateway as an external processor, rewrites that name to an economy or a frontier model before the gateway routes the request, so simple prompts are served at economy pricing without any change to the client.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- An OpenAI API key with access to two models of different price. This lab uses `gpt-5.4-nano` as the economy model and `gpt-5.6-terra` as the frontier model.
- `helm` on your path. You install vSR from the upstream chart.

> **The router is a separate deployment.** Enterprise Agentgateway does not ship an embedding model or a classifier. It calls whichever router you deploy over ExtProc and honors the answer. vSR owns the model-selection policy. The gateway owns auth, routing, rate limits, and telemetry.

## Lab Objectives
- Install vLLM Semantic Router into the cluster and point it at a Kubernetes configuration namespace
- Define the models a client may reach with an `IntelligentPool`, and the rule that picks among them with an `IntelligentRoute`
- Call vSR from the gateway with an `EnterpriseAgentgatewayPolicy` using `traffic.extProc` in the `PreRouting` phase
- Send two requests that are identical except for the prompt, and observe two different models answering
- Read the routing decision in the router logs and the per-model cost split in gateway metrics

## Architecture

```
Client Request
    │  body: { "model": "auto_model", "messages": [...] }
    ▼
EnterpriseAgentgatewayPolicy (traffic.phase: PreRouting, traffic.extProc)
    │  gRPC → semantic-router:50051, failureMode: FailClosed
    ▼
vLLM Semantic Router
    │  IntelligentRoute: keyword signal "hard" matched?
    │  IntelligentPool:  which models is this client allowed to reach?
    │  rewrites body model: auto_model → gpt-5.4-nano | gpt-5.6-terra
    ▼
HTTPRoute /semantic  →  EnterpriseAgentgatewayBackend (openai-all-models)
    │  no model override, so the rewritten name passes straight through
    ▼
OpenAI
```

## Overview

### Why a virtual model name?

Without this pattern, each client hardcodes a model name. Developers pick one that handles their hardest case, so `gpt-5.6-terra` ends up answering throwaway prompts like "Write a 500-word essay about nothing." at frontier prices. Fixing that client-side means writing model-selection logic in every application, and keeping the selection rules in sync as models and prices change.

With a virtual model name, model choice becomes a platform decision. `auto_model` is the only name clients need, and the rule behind it lives in Kubernetes resources you can change at the platform layer. Clients keep calling the same OpenAI-compatible `/v1/chat/completions` endpoint with the same model name; only the answer's model changes.

You set the name with `auto_model_name` in the vSR values below, and it is what opts a request into semantic selection.
### What "semantic" means in this lab

vSR evaluates signals of several classes: semantic (embedding), complexity, keyword, context, and structure. **This lab uses keyword signals only.** Keyword signals classify without an embedding model download or a persistent volume, so the lab can work anywhere. The decision is also deterministic, so you can predict which model each prompt below reaches.

The trade-off is that keyword matching is not semantics. A prompt that asks for a proof without using the word "prove" will not escalate. To route on meaning rather than on vocabulary, swap the keyword signal for an `embeddings` signal in the `IntelligentRoute` and enable `persistence` in the Helm values so the embedding model survives a restart. The ExtProc policy, the route, and the backend all stay the same.

---

## Install vLLM Semantic Router

Create the namespace that will hold the routing configuration. The vSR process watches exactly one namespace for its `IntelligentPool` and `IntelligentRoute` resources.

```bash
kubectl create namespace semantic-router-config --dry-run=client -oyaml | kubectl apply -f -
```

Install the chart into `agentgateway-system`, watching that namespace.

```bash
helm upgrade -i semantic-router \
  oci://ghcr.io/vllm-project/charts/semantic-router \
  --version 0.0.0-latest \
  --namespace agentgateway-system \
  --set-string image.tag=latest \
  --set image.pullPolicy=Always \
  --set-json 'args=["--secure=false","--namespace=semantic-router-config"]' \
  -f - <<'EOF'
# No PVC. Keyword signals classify without a downloaded embedding model, so
# there is nothing to persist. Enable this when you switch to embedding signals.
persistence:
  enabled: false

resources:
  requests:
    cpu: 200m
    memory: 512Mi
  limits:
    cpu: "2"
    memory: 4Gi

config:
  providers:
    defaults:
      default_model: gpt-5.4-nano
      # Keep this map. See "Why declare reasoning_families?" below.
      reasoning_families:
        gpt:
          type: reasoning_effort
          parameter: reasoning_effort
      default_reasoning_effort: high
    models:
      - name: gpt-5.4-nano
        provider_model_id: gpt-5.4-nano
        api_format: openai
  routing:
    # The router validates its default model before the Kubernetes reconciler
    # applies the IntelligentPool, so every name any pool can select has to be
    # declared here for the process to start cleanly.
    modelCards:
      - name: gpt-5.4-nano
      - name: gpt-5.6-terra
    signals: {}
    decisions: []
  global:
    router:
      # Read pools and routes from Kubernetes CRDs rather than from this file.
      config_source: kubernetes
      # The client-facing name that opts a request into semantic selection.
      auto_model_name: auto_model
      strategy: priority
      clear_route_cache: false
      model_selection:
        enabled: false
      streamed_body:
        enabled: true
        max_bytes: 10485760
        timeout_sec: 30
    services:
      # The gateway owns rate limiting in this workshop. Disable the chart's
      # sample rules so the two do not both decide.
      ratelimit:
        providers: []
EOF
```

Wait for the Deployment. First start pulls a large image and initializes the embedding backend, which can take a few minutes.

```bash
kubectl wait --for=condition=Available deployment/semantic-router \
  -n agentgateway-system --timeout=600s
```

> **Why declare `reasoning_families`?** The chart ships a `reasoning_families` map telling vSR how to express "think harder" for each model family; for `gpt` that is OpenAI's `reasoning_effort` parameter. Helm replaces maps you override, so setting `config.providers.defaults` without re-declaring `reasoning_families` drops it, and vSR falls back to injecting `chat_template_kwargs`, a vLLM parameter that OpenAI rejects with `400 Unknown parameter`.

## Define the pool and the routing rule

vSR needs two resources: the **pool** is the set of models a client is allowed to reach, and the **route** is the rule that picks one of them.

The CRDs arrive with the Helm release. Confirm they exist before applying. Without them the next command fails with `no matches for kind "IntelligentPool"`.

```bash
kubectl get crd | grep vllm.ai
```

```bash
kubectl apply -f - <<'EOF'
apiVersion: vllm.ai/v1alpha1
kind: IntelligentPool
metadata:
  name: workshop-models
  namespace: semantic-router-config
spec:
  # Every request that matches no decision gets this model, so a prompt that
  # fails to escalate falls back to the cheap model instead of erroring.
  defaultModel: gpt-5.4-nano
  models:
    - name: gpt-5.4-nano
    - name: gpt-5.6-terra
---
apiVersion: vllm.ai/v1alpha1
kind: IntelligentRoute
metadata:
  name: workshop-routing
  namespace: semantic-router-config
spec:
  # Signals are the features vSR extracts from the prompt. Decisions are the
  # rules that turn features into a model choice.
  signals:
    keywords:
      - name: hard
        operator: OR
        caseSensitive: false
        keywords:
          - prove
          - derive
          - theorem
          - quantum physics
          - stack trace
          - race condition
          - refactor
  decisions:
    - name: escalate-hard-prompts
      priority: 100
      description: Prompts that need deeper reasoning reach the frontier model.
      signals:
        operator: AND
        conditions:
          - type: keyword
            name: hard
      modelRefs:
        - model: gpt-5.6-terra
          useReasoning: false
EOF
```

Confirm both reached Ready. vSR reports Ready only after it has reconciled the resource into its running configuration, so the rule is live.

```bash
kubectl wait --for=condition=Ready \
  intelligentpool/workshop-models intelligentroute/workshop-routing \
  -n semantic-router-config --timeout=180s
```

## Create the OpenAI backend and route

Replace with a valid OpenAI API key.

```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

One route and one backend serve both tiers. The `EnterpriseAgentgatewayBackend` sets **no** model override, so OpenAI serves whatever model name is in the request body when the gateway forwards it, including the name vSR just wrote there.

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: semantic
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /semantic
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
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

Confirm the plain path works before adding the router, so that a later failure has only one possible cause. This request names a real model, so it should answer normally.

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo $GATEWAY_IP

curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-5.4-nano","messages":[{"role":"user","content":"say hi"}]}' | jq '.model'
```

```
"gpt-5.4-nano-2026-03-17"
```

## Call the router from the gateway

One policy, attached to the Gateway, sends every request to vSR before routing.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: semantic-router
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    # PreRouting runs the processor before the gateway selects a route, which is
    # what makes the rewritten model name available to everything downstream.
    phase: PreRouting
    extProc:
      backendRef:
        name: semantic-router
        namespace: agentgateway-system
        port: 50051
      failureMode: FailClosed
      processingOptions:
        requestHeaderMode: Send
        # vSR needs the whole prompt to classify it, streamed rather than
        # buffered so a long body does not stall the request.
        requestBodyMode: FullDuplexStreamed
        responseHeaderMode: Send
        # The gateway performs provider response translation, so vSR needs the
        # response headers and not the body. Sending the body costs a stream per
        # request and buys nothing here.
        responseBodyMode: None
        requestTrailerMode: Send
        responseTrailerMode: Send
        allowModeOverride: false
EOF
```

```bash
kubectl get enterpriseagentgatewaypolicy semantic-router -n agentgateway-system
```

```
NAME              ACCEPTED   ATTACHED   AGE
semantic-router   True       True       3s
```

> **Why `FailClosed`?** With `FailOpen`, a router outage sends the request on with `auto_model` still in the body, and OpenAI answers `model_not_found`, which points you at the provider instead of at the router that failed. `FailClosed` rejects the request instead. Change it only if an unrouted request is better than no request. A router outage surfaces as HTTP 500 with `reason=ExtProc` in the access log, not the 503 you might expect from an unreachable dependency, so alerts keyed on 503 will miss it.

> **Watch the `backendRef`.** A `backendRef` that resolves to no Service fails the policy with `failed to build extProc: unable to find the Service` while status still reports `Accepted=True`, so verify the wiring with the test requests below.

## Test the routing decision

Both requests below are byte-identical except for the prompt text, and both name `auto_model` rather than a real model.

An easy prompt matches no keyword, so it falls to the pool's `defaultModel`:

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"Write a 500-word essay about nothing."}]}' | jq '.model'
```

```
"gpt-5.4-nano-2026-03-17"
```

A prompt containing `prove` and `derive` matches the `hard` signal and escalates:

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"Prove that the square root of two is irrational, and derive the general theorem."}]}' | jq '.model'
```

```
"gpt-5.6-terra"
```

Because `caseSensitive: false`, casing does not matter:

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"Help me REFACTOR this race condition"}]}' | jq '.model'
```

```
"gpt-5.6-terra"
```

### Read the decision in the router log

The gateway access log records the model that *served* the request. The reason it was chosen lives in the router, across two lines: `routing_decision` names the selected model and the rule that fired, and `router_replay_start` adds the signals that fired that rule.

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=semantic-router --tail=50 | grep router_replay_start | tail -1 | jq '{original_model, selected_model, decision, decision_priority, keyword_signals: .signals.keyword}'
```

```json
{
  "original_model": "auto_model",
  "selected_model": "gpt-5.6-terra",
  "decision": "escalate-hard-prompts",
  "decision_priority": 100,
  "keyword_signals": [
    "hard"
  ]
}
```

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. Both tiers appear as distinct label sets, so cost and token usage split by the model vSR chose:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics \
  | grep -o 'gen_ai_request_model="[^"]*",gen_ai_response_model="[^"]*"' | sort -u && kill $!
```

```
gen_ai_request_model="gpt-5.4-nano",gen_ai_response_model="gpt-5.4-nano-2026-03-17"
gen_ai_request_model="gpt-5.6-terra",gen_ai_response_model="gpt-5.6-terra"
```

> **`auto_model` does not appear in metrics.** The rewrite happens at `PreRouting`, so the gateway only ever sees the *selected* model and records that in `gen_ai_request_model`. Charting requested against selected therefore needs the router's `routing_decision` log line above. Gateway metrics give you spend and volume per real model, enough to tell whether the routing saved money.

Output tokens per tier:

```promql
sum by (gen_ai_request_model) (increase(agentgateway_gen_ai_client_token_usage_sum{gen_ai_token_type="output"}[5m]))
```

### View Access Logs

One line per request carries the served model, token counts, and realized cost:

```bash
kubectl logs -n agentgateway-system -l gateway.networking.k8s.io/gateway-name=agentgateway-proxy --tail=20 \
  | grep "http.path=/semantic"
```

```
http.status=200 ... gen_ai.request.model=gpt-5.6-terra gen_ai.response.model=gpt-5.6-terra
gen_ai.usage.input_tokens=14 gen_ai.usage.output_tokens=125 gen_ai.usage.reasoning_tokens=22
agw.ai.usage.cost.total=0.001528 ... model="gpt-5.6-terra" total_cost_usd="0.001528"
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

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system semantic-router --ignore-not-found
kubectl delete httproute -n agentgateway-system semantic --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret --ignore-not-found
kubectl delete intelligentroute -n semantic-router-config workshop-routing --ignore-not-found
kubectl delete intelligentpool -n semantic-router-config workshop-models --ignore-not-found
helm uninstall semantic-router -n agentgateway-system --ignore-not-found
kubectl delete namespace semantic-router-config --ignore-not-found
```

The Helm release installs the vSR CRDs, and `helm uninstall` leaves them behind. Remove them only if no other lab needs them:

```bash
kubectl delete crd intelligentpools.vllm.ai intelligentroutes.vllm.ai --ignore-not-found
```
