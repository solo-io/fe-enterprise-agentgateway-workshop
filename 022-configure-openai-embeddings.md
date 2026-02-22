# Configure OpenAI Embeddings
Configure access to multiple OpenAI API endpoints such as for chat completions, embeddings, and models through the AgentgatewayBackend.

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
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
    "model": "gpt-4o-mini",
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

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 5
```

Example output for chat completions:
```
{
    "level": "info",
    "time": "2025-12-29T19:19:05.858661Z",
    "scope": "request",
    "gateway": "agentgateway-system/agentgateway-proxy",
    "listener": "http",
    "route": "agentgateway-system/openai",
    "endpoint": "api.openai.com:443",
    "src.addr": "10.42.0.1:37349",
    "http.method": "POST",
    "http.host": "192.168.107.2",
    "http.path": "/v1/chat/completions",
    "http.version": "HTTP/1.1",
    "http.status": 200,
    "trace.id": "8d57dc753938207f00f6aa72c75ad010",
    "span.id": "8e33ce4fb0ee0f5b",
    "protocol": "llm",
    "duration": "3323ms",
    "request.body": {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": "Whats your favorite poem?"
            }
        ]
    },
    "response.body": {
        "object": "chat.completion",
        "usage": {
            "completion_tokens": 50,
            "prompt_tokens": 12,
            "prompt_tokens_details": {
                "cached_tokens": 0,
                "audio_tokens": 0
            },
            "completion_tokens_details": {
                "rejected_prediction_tokens": 0,
                "reasoning_tokens": 0,
                "accepted_prediction_tokens": 0,
                "audio_tokens": 0
            },
            "total_tokens": 62
        },
        "created": 1767035943,
        "id": "chatcmpl-CsD3f0Dp1jpYBOFmxJVBccFZkq0IQ",
        "choices": [
            {
                "logprobs": null,
                "message": {
                    "annotations": [],
                    "refusal": null,
                    "content": "I don’t have personal preferences, but one timeless poem that many people appreciate is \"The Road Not Taken\" by Robert Frost. Its exploration of choices and their impact on life resonates with many readers. Would you like a summary or analysis of it?",
                    "role": "assistant"
                },
                "index": 0,
                "finish_reason": "stop"
            }
        ],
        "system_fingerprint": "fp_29330a9688",
        "model": "gpt-4o-mini-2024-07-18",
        "service_tier": "default"
    },
    "rq.headers.user-agent": "curl/8.7.1",
    "rq.headers.content-type": "application/json",
    "rq.headers.content-length": "144",
    "rq.headers.accept": "*/*",
    "rq.headers.all": {
        "user-agent": "curl/8.7.1",
        "content-type": "application/json",
        "content-length": "144",
        "accept": "*/*"
    }
}
```

Example output for embeddings:
```
{
    "level": "info",
    "time": "2025-12-29T19:19:07.905670Z",
    "scope": "request",
    "gateway": "agentgateway-system/agentgateway-proxy",
    "listener": "http",
    "route": "agentgateway-system/openai",
    "endpoint": "api.openai.com:443",
    "src.addr": "10.42.0.1:18322",
    "http.method": "POST",
    "http.host": "192.168.107.2",
    "http.path": "/v1/embeddings",
    "http.version": "HTTP/1.1",
    "http.status": 200,
    "trace.id": "0e6269f279c4bc3065e53f5ade553ca7",
    "span.id": "e4f4ccfce8a2cc4b",
    "protocol": "llm",
    "duration": "1013ms",
    "request.body": {
        "input": "The quick brown fox jumped over the lazy dog.",
        "model": "text-embedding-3-small"
    },
    "response.body": {
        "object": "list",
        "usage": {
            "prompt_tokens": 10,
            "total_tokens": 10
        },
        "data": [
            {
                "embedding": [
                    -0.012501234,
                    -0.0137081165,
                    <...omitted...>,
                    0.018354936
                ],
                "index": 0,
                "object": "embedding"
            }
        ],
        "model": "text-embedding-3-small"
    },
    "rq.headers.accept": "*/*",
    "rq.headers.user-agent": "curl/8.7.1",
    "rq.headers.content-type": "application/json",
    "rq.headers.content-length": "105",
    "rq.headers.all": {
        "accept": "*/*",
        "user-agent": "curl/8.7.1",
        "content-type": "application/json",
        "content-length": "105"
    }
}
```

Notice the `gen_ai.operation.name` field changes based on the endpoint:
- `chat` for `/v1/chat/completions`
- `embeddings` for `/v1/embeddings`

## Observability

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout:

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 1
```

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

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
          group: agentgateway.dev
          kind: AgentgatewayBackend
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
          group: agentgateway.dev
          kind: AgentgatewayBackend
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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
    # Default route for standard OpenAI paths (no rewrite needed)
    - backendRefs:
        - name: openai-all-models
          group: agentgateway.dev
          kind: AgentgatewayBackend
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
    "model": "gpt-4o-mini",
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
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```