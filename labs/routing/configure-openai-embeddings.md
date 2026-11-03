# Configure OpenAI Embeddings
Configure access to multiple OpenAI API endpoints, such as chat completions, embeddings, and the model list, through one `AgentgatewayModel`.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our LLM provider using an `AgentgatewayModel` and `HTTPRoute`
- Serve the chat completions, embeddings, and models endpoints from the same model
- Test both chat completions and embeddings through the agentgateway proxy
- Validate the requests went through the gateway in Grafana UI

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

Create openai route and model
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
    - name: default
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
  name: openai-all-models
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
      sectionName: default
  match:
    model: "*"
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

The rule has no path match, so it serves every path on the Gateway. The gateway recognizes each OpenAI API from the request path, so one model serves all three endpoints:
- `/v1/chat/completions`: processed as LLM traffic, with token counts, cost, and guardrail support
- `/v1/embeddings`: processed as LLM traffic, with input token counts and cost
- `/v1/models`: answered by the gateway itself with the models this route can reach

## Test OpenAI Chat Completions and Embeddings

Export the gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### Test Chat Completions
```bash
curl -i "$GATEWAY_IP:8080/v1/chat/completions" \
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

### Test /embeddings endpoint
```bash
curl -i "$GATEWAY_IP:8080/v1/embeddings" \
  -H "content-type: application/json" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "The quick brown fox jumped over the lazy dog."
  }'
```

Example embeddings response:
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [
        -0.012488048,
        -0.013707811,
        -0.009338607,
        ...
      ]
    }
  ],
  "model": "text-embedding-3-small",
  "usage": {
    "prompt_tokens": 10,
    "total_tokens": 10
  }
}
```

## Test /models endpoint

Test models listing using `/v1/models`:
```bash
curl -i "$GATEWAY_IP:8080/v1/models" \
  -H "content-type: application/json"
```

## Compare access logs across endpoints
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Example output for chat completions:
```
2026-10-29T17:30:37.258513Z	info	request gateway=agentgateway-system/agentgateway-proxy listener=http route_rule=default route=agentgateway-system/openai endpoint=api.openai.com:443 src.addr=10.244.0.0:19691 http.method=POST http.host=172.22.255.254 http.path=/v1/chat/completions http.version=HTTP/1.1 http.status=200 trace.id=b24cbccac4883ad33f3e6d76755117a7 span.id=32d7563fd4cfb4d1 protocol=llm gen_ai.operation.name=chat gen_ai.provider.name=openai gen_ai.request.model=gpt-5.4-nano gen_ai.response.model=gpt-5.4-nano-2026-03-17 agw.ai.original_model=gpt-5.4-nano gen_ai.usage.input_tokens=8 gen_ai.usage.cache_read.input_tokens=0 gen_ai.usage.output_tokens=14 gen_ai.usage.reasoning_tokens=0 agw.ai.usage.cost.total=0.0000191 gen_ai.usage.input_audio_tokens=0 gen_ai.usage.output_audio_tokens=0 duration=843ms request_path="/v1/chat/completions" status_code="200" llm_duration="0.842581376s" request_proc_duration="0.000229708s" response_proc_duration="0.000168375s" provider="openai" model="gpt-5.4-nano-2026-03-17" prompt_tokens="8" completion_tokens="14" total_cost_usd="0.0000191" llm_streaming=false llm_cached_tokens="0" llm_reasoning_tokens="0" client_ip="10.244.0.0" user_id="anonymous" jwt_all={}
```

Example output for embeddings:
```
2026-10-29T17:30:33.029599Z	info	request gateway=agentgateway-system/agentgateway-proxy listener=http route_rule=default route=agentgateway-system/openai endpoint=api.openai.com:443 src.addr=10.244.2.1:34061 http.method=POST http.host=172.22.255.254 http.path=/v1/embeddings http.version=HTTP/1.1 http.status=200 trace.id=23736b938cdebf28d759d64074dc903b span.id=f575d3685c0e42c1 protocol=llm gen_ai.operation.name=embeddings gen_ai.provider.name=openai gen_ai.request.model=text-embedding-3-small gen_ai.response.model=text-embedding-3-small agw.ai.original_model=text-embedding-3-small gen_ai.usage.input_tokens=10 agw.ai.usage.cost.total=0.0000002 duration=627ms request_path="/v1/embeddings" status_code="200" llm_duration="0.613805792s" request_proc_duration="0.00036675s" response_proc_duration="0.013347791s" provider="openai" model="text-embedding-3-small" prompt_tokens="10" completion_tokens="0" total_cost_usd="0.0000002" llm_streaming=false llm_cached_tokens="0" llm_reasoning_tokens="0" client_ip="10.244.2.1" user_id="anonymous" jwt_all={}
```

Both requests are logged as LLM traffic (`protocol=llm`). The chat line carries `gen_ai.operation.name=chat`, the requested and served models, input and output token counts, and per-request cost under `agw.ai.usage.cost.total`. The embeddings line carries `gen_ai.operation.name=embeddings`, the input token count, and its cost. The `/v1/models` request is answered by the gateway, so its line shows `reason=DirectResponse` and no upstream `endpoint`.

## Observability

### View Access Logs

The gateway logs every LLM request to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

The log line carries the model and token counts, plus a `trace.id` you can search for in the Solo UI's **Tracing** view.

## Advanced: Serve the APIs Under a Path Prefix

The previous configuration serves the OpenAI paths at the root of the Gateway. To serve them under your own prefix, add a rule with a `PathPrefix` match. The gateway strips the matched prefix and forwards the rest of the path, so `/openai/v1/embeddings` reaches OpenAI as `/v1/embeddings`. Attach the model to both rules with one `parentRefs` entry per rule.

Update the existing HTTPRoute and model:
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
    # Custom prefix: /openai/v1/... -> /v1/...
    - name: prefixed
      matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
    # Default rule for the standard OpenAI paths
    - name: default
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
  name: openai-all-models
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
      sectionName: prefixed
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
      sectionName: default
  match:
    model: "*"
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

### Test with Prefixed Paths

Test chat completions using `/openai/v1/chat/completions`:
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

Test embeddings using `/openai/v1/embeddings`:
```bash
curl -i "$GATEWAY_IP:8080/openai/v1/embeddings" \
  -H "content-type: application/json" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "The quick brown fox jumped over the lazy dog."
  }'
```

Test models listing using `/openai/v1/models`:
```bash
curl -i "$GATEWAY_IP:8080/openai/v1/models" \
  -H "content-type: application/json"
```

You can also still use the standard OpenAI paths directly thanks to the default rule:
```bash
curl -i "$GATEWAY_IP:8080/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

> **Note:** Model rules do not support the `URLRewrite` or `RequestRedirect` filters. Use a path prefix, as above, to give clients a custom path.

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaymodel -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```