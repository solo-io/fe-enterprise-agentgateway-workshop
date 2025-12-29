# Configure OpenAI Embeddings with AI Routes

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure AI routes to handle different OpenAI API endpoints (chat completions, embeddings, models)
- Test both chat completions and embeddings through the agentgateway proxy
- Validate the requests went through the gateway in Jaeger UI

### Configure Required Variables
Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
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
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
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
  namespace: enterprise-agentgateway
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
        "/v1/embeddings": "Embeddings"
        "/v1/models": "Models"
        "*": "Passthrough"
EOF
```

The `policies.ai.routes` configuration allows you to route different OpenAI API endpoints through the gateway:
- `/v1/chat/completions` - Routes to OpenAI's chat completions endpoint
- `/v1/embeddings` - Routes to OpenAI's embeddings endpoint for generating text embeddings
- `/v1/models` - Routes to OpenAI's models listing endpoint
- `*` - Passthrough for any other paths

## Test OpenAI Chat Completions and Embeddings

Export the gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
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

### Test Embeddings
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

## View all metrics
All metrics
```bash
echo
echo "Objective: curl /metrics endpoint and show all metrics"
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
``` 

Filter for number of requests served through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for number of requests served through the gateway"
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_requests_total && kill $!
``` 

Total input and output token usage through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for input/output token usage through the gateway"
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_gen_ai_client_token_usage_sum && kill $!
``` 
You can tell the difference between the two metrics from the `gen_ai_token_type="input/output"` label

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 5
```

Example output for chat completions:
```
{
    "level": "info",
    "time": "2025-12-29T19:19:05.858661Z",
    "scope": "request",
    "gateway": "enterprise-agentgateway/agentgateway",
    "listener": "http",
    "route": "enterprise-agentgateway/openai",
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
    "gateway": "enterprise-agentgateway/agentgateway",
    "listener": "http",
    "route": "enterprise-agentgateway/openai",
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

## Port-forward to Grafana UI to view traces
Default credentials are admin:prom-operator
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:3000 or http://localhost:16686 in your browser. You should be able to see traces for agentgateway that include information such as:
- For chat completions: `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`
- For embeddings: `gen_ai.operation.name=embeddings`, `llm.request.model`, input tokens, and more

## Advanced: Using Path Rewrites

The previous configuration requires clients to use the exact OpenAI API paths (`/v1/chat/completions`, `/v1/embeddings`). You can use path rewrites to create custom paths that get rewritten to the correct OpenAI endpoints.

Update the existing HTTPRoute with path rewrite rules:
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
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
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret
```