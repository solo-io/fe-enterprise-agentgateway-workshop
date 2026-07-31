# Configure OpenAI Embeddings
Configure access to multiple OpenAI API endpoints such as for chat completions, embeddings, and models through the EnterpriseAgentgatewayBackend.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure AI routes to handle different OpenAI API endpoints (chat completions, embeddings, models)
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

Create openai route and backend with AI routes configuration
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
    - backendRefs:
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
    ai:
      routes:
        "/v1/chat/completions": "Completions"
        "/v1/embeddings": "Passthrough"
        "/v1/models": "Passthrough"
        "*": "Passthrough"
EOF
```

The `policies.ai.routes` configuration allows you to route different OpenAI API endpoints through the gateway:
- `/v1/chat/completions`: `"Completions"` - The completions API is currently supported for AI gateway processing (metrics, logging, guardrails, prompt engineering)
- `/v1/embeddings`: `"Passthrough"` - Proxies embeddings requests through the gateway
- `/v1/models`: `"Passthrough"` - Proxies model listing requests through the gateway
- `*`: `"Passthrough"` - Default passthrough for any other paths

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

Example output for chat completions, which the backend maps to `Completions`:
```
2026-07-29T22:48:02.532619Z	info	request gateway=agentgateway-system/agentgateway-proxy listener=http route=agentgateway-system/openai endpoint=api.openai.com:443 src.addr=10.244.0.0:1599 http.method=POST http.host=172.18.255.254 http.path=/v1/chat/completions http.version=HTTP/1.1 http.status=200 trace.id=789dc792d6aa18f780f31bb03f4be6c4 span.id=1379e8fbe67f388a protocol=llm gen_ai.operation.name=chat gen_ai.provider.name=openai gen_ai.request.model=gpt-5.4-nano gen_ai.response.model=gpt-5.4-nano-2026-03-17 gen_ai.usage.input_tokens=11 gen_ai.usage.cache_read.input_tokens=0 gen_ai.usage.output_tokens=82 agw.ai.usage.cost.total=0.0001047 gen_ai.usage.output_audio_tokens=0 duration=1128ms llm.streaming=false llm.cached_tokens=0 llm.reasoning_tokens=0 llm.prompt=[{"role": "user", "content": "Whats your favorite poem?"}] llm.completion="..."
```

Example output for embeddings, which the backend maps to `Passthrough`:
```
2026-07-29T22:48:03.41909Z	info	request gateway=agentgateway-system/agentgateway-proxy listener=http route=agentgateway-system/openai endpoint=api.openai.com:443 src.addr=10.244.1.1:61033 http.method=POST http.host=172.18.255.254 http.path=/v1/embeddings http.version=HTTP/1.1 http.status=200 trace.id=8540d5f05bd8bc668d3d39007058ae88 span.id=96a543cf0942ec24 protocol=llm duration=463ms
```

The route type you assign in `policies.ai.routes` decides how much the gateway records:

- `Completions` routes are parsed as LLM traffic, so the log line carries `gen_ai.operation.name=chat`, the requested and served models, input and output token counts, per-request cost under `agw.ai.usage.cost.total`, and the prompt and completion text as `llm.prompt` and `llm.completion`.
- `Passthrough` routes are proxied without LLM parsing, so their log lines carry only the HTTP fields — method, path, status, and duration. Token usage for embeddings is still returned to the client in the response body's `usage` object, but the gateway does not extract it.

Use `Passthrough` when you want the gateway to front an endpoint for auth, routing, and TLS, and `Completions` when you also want token accounting, cost attribution, and guardrails.

## Observability

### View Access Logs

The gateway logs every LLM request to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

The log line carries the model and token counts, plus a `trace.id` you can search for in the Solo UI's **Tracing** view.

## Advanced: Using Path Rewrites

The previous configuration requires clients to use the exact OpenAI API paths (`/v1/chat/completions`, `/v1/embeddings`). You can use path rewrites to create custom paths that get rewritten to the correct OpenAI endpoints.

Update the existing HTTPRoute with path rewrite rules:
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
    # Custom path for chat completions: /openai/chat -> /v1/chat/completions
    - matches:
        - path:
            type: PathPrefix
            value: /openai/chat
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /v1/chat/completions
      backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
    # Custom path for embeddings: /openai/embeddings -> /v1/embeddings
    - matches:
        - path:
            type: PathPrefix
            value: /openai/embeddings
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /v1/embeddings
      backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
    # Custom path for models: /openai/models -> /v1/models
    - matches:
        - path:
            type: PathPrefix
            value: /openai/models
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /v1/models
      backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
    # Default route for standard OpenAI paths (no rewrite needed)
    - backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

### Test with Rewritten Paths

Now you can use the simplified custom paths:

Test chat completions using `/openai/chat`:
```bash
curl -i "$GATEWAY_IP:8080/openai/chat" \
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

Test embeddings using `/openai/embeddings`:
```bash
curl -i "$GATEWAY_IP:8080/openai/embeddings" \
  -H "content-type: application/json" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "The quick brown fox jumped over the lazy dog."
  }'
```

Test models listing using `/openai/models`:
```bash
curl -i "$GATEWAY_IP:8080/openai/models" \
  -H "content-type: application/json"
```

The gateway will rewrite these paths to the correct OpenAI API endpoints before forwarding the requests. The AI routes configuration in the backend will still match on the rewritten paths (`/v1/chat/completions`, `/v1/embeddings`, etc.).

You can also still use the standard OpenAI paths directly thanks to the default rule:
```bash
curl -i "$GATEWAY_IP:8080/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```