# Migrate from AgentgatewayBackend to AgentgatewayModel

Before v2026.9.2, Enterprise Agentgateway routed LLM traffic through `EnterpriseAgentgatewayBackend`. A backend describes a destination: one provider, often with one pinned model, and an `HTTPRoute` sends requests to it by path or header. The route makes the routing decision, and the model the client names in the request body plays no part in it unless you copy that field into a header first. A pinned model replaces whatever the client asked for. Each additional model means another backend, another route match, and often another policy.

Enterprise Agentgateway v2026.9.2 introduced the `AgentgatewayModel` API, which starts from the model instead. You declare the models your clients can call, one resource per model, and attach them to an `HTTPRoute` or a Gateway listener. The gateway reads `model` from the request body and selects the matching resource, which knows its provider, credentials and policies.

The model-centric pattern removes the scaffolding that each backend deployment repeated, and it matches how people use LLMs:

- Developers pick a model by name. OpenAI SDKs, coding agents and agent frameworks send `"model": "..."` in the request body. The gateway routes on that field, lists the models it serves at `/v1/models`, and answers unknown names with OpenAI-style errors that SDKs already handle.
- Platform teams publish stable names such as `fast` or `smart` and change the provider or model behind a name without a client release. Canary rollouts, failover and tiering happen behind one name.
- Each model carries its own credentials and policies, so teams review, own and grant access to models one at a time.

In this lab you run the backend pattern and the model pattern side by side on one gateway, compare what each one gives you, and move traffic from one to the other.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.
- An OpenAI API key in `$OPENAI_API_KEY`

## Lab Objectives
- Compare the backend-centric pattern (`EnterpriseAgentgatewayBackend` with `spec.ai`) with the model-centric pattern (`AgentgatewayModel`)
- Publish client-facing model names with `match.model`, aliases, wildcards and `visibility`
- Use the gateway's built-in `/v1/models` listing and OpenAI-compatible errors
- Split traffic with a `weighted` virtual model and tier traffic with a `conditional` virtual model
- Trace each request back to the model name the client sent
- Weigh the pros and cons, map backend fields to model fields, and plan a migration
- Compare `AgentgatewayModel` with how other LLM gateways model a model

## Two Ways to Describe an LLM

The models attached to one route rule form a model table, called a model router. The gateway serves that router's API paths under the rule's prefix and selects from the table by the body `model`.

![Backend-centric vs model-centric routing: on the left, a client posts to /legacy, an HTTPRoute path match sends the request to an EnterpriseAgentgatewayBackend pinned to gpt-5.4-nano, and OpenAI receives gpt-5.4-nano whatever the client asked for. On the right, a client posts to /openai/v1/chat/completions, the HTTPRoute rule acts as a model router that looks up the body model among fast, smart, a gpt-* wildcard and the virtual model assistant, which sends 80% of requests to the Internal mock model and 20% to fast](../../images/routing/backend-vs-model-routing.png)

## Configure Required Variables

Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create the OpenAI API key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Export the gateway address
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo $GATEWAY_IP
```

### Deploy a Mock LLM

The virtual model steps route some traffic to a local OpenAI-compatible mock, so you can see which target served each request without paying for every call. This is the same vLLM simulator as the [Mock OpenAI Server lab](configure-mock-openai-server.md).

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
        - name: vllm-sim
          image: ghcr.io/llm-d/llm-d-inference-sim:latest
          imagePullPolicy: IfNotPresent
          args:
            - --model
            - mock-gpt-4o
            - --port
            - "8000"
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

```bash
kubectl rollout status deploy/mock-gpt-4o -n agentgateway-system --timeout=240s
```

## Run the Backend-Centric Pattern

Create the backend-centric pattern: a route on `/legacy` that forwards to a backend pinned to `gpt-5.4-nano`. This route stays up for the rest of the lab, the way an existing deployment keeps serving while you migrate.

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: legacy-openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /legacy
      backendRefs:
        - name: legacy-openai
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: legacy-openai
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: gpt-5.4-nano
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

Send a request that asks for `gpt-5.4-mini`:

```bash
curl -s "$GATEWAY_IP:8080/legacy" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }' | jq -r '.model'
```

Expected output:

```
gpt-5.4-nano-2026-03-17
```

The client asked for `gpt-5.4-mini` and got `gpt-5.4-nano`, with no error and no header to say so. The pin is a property of the destination, so the backend replaces whatever the client sends. To offer a second model you add a second backend, a second route match, and a way to tell the two apart.

Ask the legacy route which models it serves:

```bash
curl -s -w "\nHTTP %{http_code}\n" "$GATEWAY_IP:8080/legacy/v1/models"
```

Expected output:

```
failed to process LLM request: failed to parse Completions request: EOF while parsing a value at line 1 column 0
HTTP 400
```

The backend treats every request on its route as a chat completion, so a `GET` with no body fails to parse. SDKs, coding agents and IDE plugins call `GET /v1/models` to discover what they can use, so on backends you write and maintain that list yourself.

## Publish the Same Provider as Models

Create a second route on `/openai` and attach models to it. Both routes run on the same Gateway, so existing clients keep working while you move them.

| Model | Client sends | Provider receives | Visibility |
|---|---|---|---|
| `fast` | `fast` | `gpt-5.4-nano` | `Public` |
| `smart` | `smart` | `gpt-5.4-mini` | `Public` |
| `openai-gpt-models` | any `gpt-*` name | the same name | `Public` |
| `mock` | `mock` | `mock-gpt-4o` | `Internal` |

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
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: fast
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
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
  name: smart
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
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
  name: openai-gpt-models
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  match:
    model: gpt-*
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: mock
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  visibility: Internal
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

A model with no `match` serves requests for its `metadata.name`, so `fast` and `smart` are aliases by construction. The `transformations` entry rewrites the body `model` to the name the provider expects. `openai-gpt-models` uses a suffix wildcard to pass any `gpt-*` name through unchanged, and the router prefers the most specific match, so an exact name beats `gpt-*`, which beats `*`.

Check that every model attached:

```bash
kubectl get agentgatewaymodel -n agentgateway-system \
  -o custom-columns='NAME:.metadata.name,MATCH:.spec.match.model,VISIBILITY:.spec.visibility,ACCEPTED:.status.parents[0].conditions[?(@.type=="Accepted")].status'
```

Expected output:

```
NAME                MATCH    VISIBILITY   ACCEPTED
fast                <none>   <none>       True
mock                <none>   Internal     True
openai-gpt-models   gpt-*    <none>       True
smart               <none>   <none>       True
```

### List the Models

The gateway answers `GET <prefix>/v1/models` itself, from the model table:

```bash
curl -s "$GATEWAY_IP:8080/openai/v1/models" | jq -r '.data[].id' | head -6
```

Expected output (truncated):

```
smart
fast
gpt-4.1
gpt-4.1-mini
gpt-4o
gpt-4o-2024-08-06
```

`fast` and `smart` come first. The `gpt-*` wildcard model adds every model in the gateway's built-in model catalog whose name matches `gpt-*`, so clients can discover pass-through names too. `mock` is `Internal`, so it stays out of the list.

### Request Models by Name

The route forwards the path under `/openai` to the provider, so clients call the provider API path `/openai/v1/chat/completions`. Ask for each alias and for a wildcard name:

```bash
for m in fast smart gpt-5.4-nano; do
  printf '%-14s -> ' "$m"
  curl -s "$GATEWAY_IP:8080/openai/v1/chat/completions" \
    -H "content-type: application/json" \
    -d "{\"model\": \"$m\", \"messages\": [{\"role\": \"user\", \"content\": \"Whats your favorite poem?\"}]}" \
    | jq -r '.model'
done
```

Expected output:

```
fast           -> gpt-5.4-nano-2026-03-17
smart          -> gpt-5.4-mini-2026-03-17
gpt-5.4-nano   -> gpt-5.4-nano-2026-03-17
```

OpenAI returns a dated model ID, so the response shows which provider model served each alias.

### Get Errors Clients Understand

Request a model that does not exist, an `Internal` model, and a body with no `model` field:

```bash
for body in '{"model": "claude-opus", "messages": [{"role": "user", "content": "hi"}]}' \
            '{"model": "mock", "messages": [{"role": "user", "content": "hi"}]}' \
            '{"messages": [{"role": "user", "content": "hi"}]}'; do
  curl -s -w "  HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai/v1/chat/completions" \
    -H "content-type: application/json" -d "$body"
done
```

Expected output:

```
{"error":{"message":"Model not found","type":"invalid_request_error","code":"model_not_found"}}  HTTP 404
{"error":{"message":"Model not found","type":"invalid_request_error","code":"model_not_found"}}  HTTP 404
{"error":{"message":"LLM request body is missing string field 'model'","type":"invalid_request_error","code":"missing_model"}}  HTTP 400
```

The gateway answers in the OpenAI error shape before any provider call, so SDKs raise their normal `NotFoundError` or `BadRequestError`. An `Internal` model looks the same as a missing one from the outside. Every request needs a `model` field, because the router selects with it.

## Route Across Models with Virtual Models

A virtual model publishes one client-facing name and picks a concrete model per request. It sets `spec.virtualModel` in place of `spec.provider`, so it has no credentials or policies of its own. It supports four strategies:

| Strategy | Picks a target by | Typical use |
|---|---|---|
| `weighted` | Relative weight | Canary a new model or provider, A/B tests |
| `failover` | Priority group, then health and latency | Survive a provider outage or rate limit. See [Configure LLM Failover](llm-failover.md) |
| `conditional` | The first CEL `when` that is `true` | Tier by header or body content |
| `callout` | The model name an HTTP service returns | Central routing service, such as a classifier. Available in v2026.10.0 and later. See [Semantic Routing with Jev](configure-semantic-routing-jev.md) |

### Canary a Model with Weights

Publish `assistant` as a stable name, and send about 80% of its traffic to the mock and 20% to `fast`. To shift traffic later, change the weights. Clients keep sending `assistant`.

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: assistant
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  virtualModel:
    weighted:
      targets:
        - modelRef:
            name: mock
          weight: 80
        - modelRef:
            name: fast
          weight: 20
EOF
```

Send 20 requests and count which model served each one:

```bash
for i in $(seq 20); do
  curl -s "$GATEWAY_IP:8080/openai/v1/chat/completions" \
    -H "content-type: application/json" \
    -d '{"model": "assistant", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}' \
    | jq -r '.model'
done | sort | uniq -c | sort -rn
```

Expected output (the split varies from run to run):

```
     14 mock-gpt-4o
      6 gpt-5.4-nano-2026-03-17
```

### Tier Traffic with Conditions

Send clients that set `x-tier: premium` to `smart`, and everyone else to the mock. Targets are evaluated in order, and the last target without `when` is the default.

```bash
kubectl apply -f - <<'EOF'
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: tiered
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  virtualModel:
    conditional:
      targets:
        - when: 'default(request.headers["x-tier"], "") == "premium"'
          modelRef:
            name: smart
        - modelRef:
            name: mock
EOF
```

```bash
for tier in premium standard; do
  printf '%-9s -> ' "$tier"
  curl -s "$GATEWAY_IP:8080/openai/v1/chat/completions" \
    -H "content-type: application/json" \
    -H "x-tier: $tier" \
    -d '{"model": "tiered", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}' \
    | jq -r '.model'
done
```

Expected output:

```
premium   -> gpt-5.4-mini-2026-03-17
standard  -> mock-gpt-4o
```

The model list now shows both virtual models next to the aliases. Their targets stay hidden when they are `Internal`:

```bash
curl -s "$GATEWAY_IP:8080/openai/v1/models" | jq -r '.data[].id' | grep -v '^gpt-'
```

Expected output:

```
smart
fast
assistant
tiered
```

## Trace a Request to the Name the Client Sent

With aliases and virtual models, the name the client sends and the name the provider receives differ. The access log records both: `agw.ai.original_model` is the client's name, and `gen_ai.request.model` is what the gateway sent upstream.

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --tail 500 \
  | grep 'route=agentgateway-system/openai' | grep 'endpoint=' \
  | awk '{for (i = 1; i <= NF; i++) if ($i ~ /^(agw\.ai\.original_model|gen_ai\.request\.model)=/) printf "%s ", $i; print ""}' \
  | sort | uniq -c
```

Expected output (your counts differ):

```
   1 gen_ai.request.model=gpt-5.4-mini agw.ai.original_model=smart
   1 gen_ai.request.model=gpt-5.4-mini agw.ai.original_model=tiered
   6 gen_ai.request.model=gpt-5.4-nano agw.ai.original_model=assistant
   1 gen_ai.request.model=gpt-5.4-nano agw.ai.original_model=fast
   1 gen_ai.request.model=gpt-5.4-nano agw.ai.original_model=gpt-5.4-nano
  14 gen_ai.request.model=mock-gpt-4o agw.ai.original_model=assistant
   1 gen_ai.request.model=mock-gpt-4o agw.ai.original_model=tiered
```

Token counts and cost (`agw.ai.usage.cost.total`) are recorded the same way as with backends, and trace spans in the Solo UI carry the same two attributes.

## Cut Traffic Over from the Backend

Both routes now serve the same provider. Moving a client is a base URL change, from `http://$GATEWAY_IP:8080/legacy` to `http://$GATEWAY_IP:8080/openai/v1`, plus a model name it chooses from the list. Once the access log shows no traffic on `legacy-openai`, delete the backend route:

```bash
kubectl delete httproute -n agentgateway-system legacy-openai --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system legacy-openai --ignore-not-found
```

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$GATEWAY_IP:8080/legacy" \
  -H "content-type: application/json" \
  -d '{"model": "gpt-5.4-nano", "messages": [{"role": "user", "content": "hi"}]}'
```

Expected output:

```
HTTP 404
```

## Pros and Cons

### What You Gain

- Clients choose the model with the body `model` field, which OpenAI-compatible SDKs send on every request.
- `GET /v1/models` lists the `Public` models on each router, so Codex, IDE plugins and SDK model pickers discover models from the gateway.
- Each model is one resource with its own credentials, guardrails, health policy, TLS and tunnel. Teams can own model files in Git, and Kubernetes RBAC on `agentgatewaymodels` controls who can publish a model.
- Virtual models give canary, failover, conditional tiering and callout routing one stable client name, so you change providers without a client release.
- Priority failover takes three models in the [failover lab](llm-failover.md), where backends took one backend plus three `sectionName` policies. Routing by model name in the [route-by-model lab](configure-body-based-routing.md) takes the models alone, with no header-extraction policy.
- The gateway answers unknown and hidden models with an OpenAI-shaped `404 model_not_found`, and a missing `model` with `400 missing_model`, before any provider call.
- One model serves chat, Responses, Messages, embeddings and audio. The gateway detects the API from the path and records tokens and cost for each.

### What You Give Up

- Every request needs a body `model`. Requests that carry none, such as `GET /v1/files` or `/v1/batches`, fail with `400`. Keep those on an `EnterpriseAgentgatewayBackend`, as the [Batches lab](configure-openai-batches.md) and [Titan embeddings lab](configure-routing-aws-bedrock-titan-embeddings.md) do.
- Clients change their path: model routes forward the provider API path, so clients call `/openai/v1/chat/completions` in place of `POST /openai`. SDK base URLs end in `/v1`.
- `EnterpriseAgentgatewayPolicy` cannot target an `AgentgatewayModel`. Per-model settings go in `spec.policies`, and policies for a group of models target the `HTTPRoute` or one of its named rules.
- A model rule takes `PathPrefix` matches only, rejects `URLRewrite` and `RequestRedirect` filters, and holds one `backendRef`. A multi-rule route needs rule names and `sectionName` on every model.
- A `HEAD` preflight or health probe on a model prefix returns `400`, because it has no body. Answer it with an `Exact` route and a `directResponse`, as the [Claude Code lab](../agent-harnesses/claude-code.md) does for `HEAD /claude/api/hello`.
- A model attaches only to a route in its own namespace, and virtual model targets must be in the same namespace and on the same router.
- Routing on method, query parameter or header before the model is known needs an `EnterpriseAgentgatewayBackend` route. MCP servers, static hosts, JWKS and tunnel proxies also stay on `EnterpriseAgentgatewayBackend`.
- `AgentgatewayModel` is a `v1alpha1` API. It is enabled by default and can change in a later release. Per-model settings are limited to the fields in `spec.policies`.

## Migration Guide

### Field Mapping

| `EnterpriseAgentgatewayBackend` | `AgentgatewayModel` |
|---|---|
| `spec.ai.provider.openai: {}` | `provider: OpenAI` |
| `spec.ai.provider.openai.model: gpt-5.4-nano` | `policies.transformations` with `field: model`, `expression: '"gpt-5.4-nano"'` on a `match.model: "*"` model, or a model named for the alias |
| `spec.ai.provider.host`, `port`, `path` | `baseURL: https://host:port/v1` (include the API base path) |
| `spec.ai.provider.anthropic`, `bedrock`, `vertexai`, `azureopenai` | `provider: Anthropic`, `Bedrock` + `bedrock.region`, `VertexAI` + `vertexai.projectId`, `Azure` + `azure.resourceName` and `azure.resourceType` |
| OpenAI-compatible provider with a host override (Groq, Ollama) | The built-in `provider` (`Groq`, `Ollama`, `Mistral`, ...) |
| `spec.policies.auth`, `tls`, `tunnel` | `spec.policies.auth`, `tls`, `tunnel` |
| `PreRouting` transformation that copies `json(request.body).model` into a header, plus `HTTPRoute` header matches | Remove. The body `model` selects the model |
| `spec.policies.ai.routes` | Remove. The path selects the API |
| `modelAliases` in a policy's `ai` settings | One model per alias |
| `spec.ai.groups` priority groups | Internal models plus a virtual model with `failover.targets[].priority` |
| `EnterpriseAgentgatewayPolicy` with `backend.health` | `spec.policies.health` on each concrete model |
| `EnterpriseAgentgatewayPolicy` targeting the backend (`sectionName` per provider) | `spec.policies` on each model |
| `EnterpriseAgentgatewayPolicy` targeting the route or Gateway | Unchanged |
| `HTTPRoute` `backendRefs` to the backend | `backendRefs` with `group: agentgateway.dev`, `kind: AgentgatewayModel`, `name: "*"`, and `parentRefs` on each model |
| `/v1/models` passthrough or `directResponse` route | Remove. The gateway serves it |

### Migration Steps

1. List every `EnterpriseAgentgatewayBackend` with `spec.ai`, the policies that target each one, and which clients call it. Flag backends that serve requests without a body `model`; they stay as backends.
2. Decide the names clients see. Pick stable aliases (`fast`, `smart`, `assistant`) over provider IDs where you expect to swap providers. Mark models that exist only as targets `Internal`.
3. Run both side by side. Attach the new models to a new route prefix, as `/openai` sat next to `/legacy` in this lab. The Gateway serves both.
4. Move per-provider settings onto models. Credentials, health, guardrails and body transformations move into `spec.policies`. Route and Gateway policies, such as rate limits, budgets, JWT and WAF, keep their `targetRefs`.
5. Check status. `kubectl get agentgatewaymodel -o yaml` reports `Accepted` and `ResolvedRefs` per parent, with the broken requirement in the message.
6. Move clients. Change each client's base URL to `<prefix>/v1` and confirm it can list models. To move traffic from one provider to another later, publish the client's model name as a `weighted` virtual model and shift the weights.
7. Watch, then delete. When the access log and metrics show no traffic on the backend route, delete the route, the backend and the policies that targeted it.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `404` with `model_not_found` | No `Public` model matches the name, or the model is `Internal` | Check `GET <prefix>/v1/models` and `match.model` |
| `404` with `virtual_model_target_not_found` | A virtual model target is missing or attached to a different router | Put targets on the same route rule as the virtual model |
| Provider receives the alias name | No `model` transformation on the alias | Add `policies.transformations` with `field: model` |

## How AgentgatewayModel Compares to Other LLM Gateways

The table maps each concept from this lab to other LLM gateways. Product behavior comes from each vendor's public documentation as of November 2026.

| Product | Name clients send | Name the provider gets | Split and failover | Routing by request context | Configured as |
|---|---|---|---|---|---|
| Enterprise Agentgateway `AgentgatewayModel` | `metadata.name` or `match.model` (exact, `*`, `prefix-*`, `*-suffix`) | `policies.transformations` on `field: model` | Virtual model `weighted`, or `failover` with priority groups and health eviction | Virtual model `conditional` (CEL `when`) or `callout` (your HTTP service) | Kubernetes CRD, one resource per model |
| LiteLLM Proxy | `model_list[].model_name` | `litellm_params.model` | Entries that share a `model_name` load-balance (`routing_strategy`); `fallbacks` between model groups | Tag routing (`tags` in the body or `x-litellm-tags`) | `config.yaml`, database and UI |
| Agent Router (formerly Envoy AI Gateway) | `x-ai-eg-model` header match, filled from the body `model` | `backendRefs[].modelNameOverride` | `backendRefs[].weight` and `priority` | Gateway API header and path matches | Kubernetes CRDs: `AIGatewayRoute`, `AIServiceBackend`, `BackendSecurityPolicy` |
| Kong AI Gateway (`ai-proxy-advanced`) | `targets[].model.model_alias` | `targets[].model.name` | `balancer.algorithm` (including `priority`), `targets[].weight`, `failover_criteria` | `semantic` balancing on target descriptions | Plugin configuration |
| Portkey | A config attached to the request | `targets[].override_params.model` | `strategy.mode: loadbalance` or `fallback` | `strategy.mode: conditional` with `conditions[].query` | JSON config, UI |
| TrueFoundry AI Gateway | Virtual model name | Target catalog model | `weight-based-routing`, `priority-based-routing`, `latency-based-routing` | Per-target `metadata_match` | UI or YAML |
| OpenRouter | Catalog slug, such as `openai/gpt-5.4-mini` | The same slug | `models: [...]` fallback list in the request; `provider.order` and `sort` | `openrouter/auto` hosted router | Request body parameters |
| Gateway API Inference Extension | Body-based routing copies `model` into `X-Gateway-Model-Name` | The same name | `HTTPRoute` weights across `InferencePool`s | Gateway API header matches | Kubernetes CRDs; `InferencePool` only from v1.6.0 |

### Where the Industry Agrees

- The body `model` is the routing key. Each product in the table selects on the OpenAI-compatible `model` field. Agent Router and the Inference Extension copy it into a header so that standard route matching can use it; the rest read the body. `AgentgatewayModel` reads the body, so you skip the header-extraction policy.
- A client-facing name maps to one or more provider targets, each with its own model rewrite. LiteLLM `model_name`, Agent Router `modelNameOverride`, Kong `model_alias`, Portkey `override_params.model` and TrueFoundry virtual models all express this. In `AgentgatewayModel` the name is the resource and the rewrite is a transformation.
- Weighted split and priority fallback come as a pair. LiteLLM, Agent Router, Kong, Portkey and TrueFoundry all ship both, matching `weighted` and `failover`.
- `GET /v1/models` lists what clients may call. LiteLLM and Agent Router serve it from configuration, as `AgentgatewayModel` does.

### Where AgentgatewayModel Differs

- `conditional` targets evaluate CEL over request headers, the request body and other request context, in the same expression language as the gateway's other policies.
- `callout` asks an HTTP service you run for the model name, with a fallback model and a response cache. OpenRouter's auto router is a hosted service that OpenRouter runs.
- `match.model` takes prefix and suffix wildcards (`gpt-*`, `*-latest`), and the most specific match wins. LiteLLM supports `*` and `provider/*`.

For self-hosted models, an `AgentgatewayModel` with `provider: Custom` and `custom.backendRef` can target an `InferencePool`, so the Inference Extension's endpoint picker still chooses the pod while the model resource owns the name, policies and failover.

## Cleanup
```bash
kubectl delete agentgatewaymodel -n agentgateway-system tiered assistant mock openai-gpt-models smart fast --ignore-not-found
kubectl delete httproute -n agentgateway-system openai legacy-openai --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system legacy-openai --ignore-not-found
kubectl delete service -n agentgateway-system mock-gpt-4o-svc --ignore-not-found
kubectl delete deployment -n agentgateway-system mock-gpt-4o --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret --ignore-not-found
```
