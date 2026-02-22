# Test OpenAI Streaming Responses

In this lab, you'll test streaming responses from OpenAI through AgentGateway. Streaming allows you to receive LLM responses incrementally as they're generated, providing a better user experience for real-time applications.

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Configure routing to OpenAI with streaming support
- Send streaming requests to OpenAI
- Compare streaming vs non-streaming responses
- Observe streaming metrics in Grafana (TTFT, TPOT)

## What is Streaming?

Streaming responses allow LLM providers to send generated text incrementally as tokens are produced, rather than waiting for the complete response. This provides:

- **Faster perceived response time**: Users see output immediately
- **Better UX for long responses**: Progressive display of content
- **Lower latency to first token**: Time to First Token (TTFT) metrics
- **Token generation monitoring**: Tokens Per Output Token (TPOT) tracking

AgentGateway automatically supports streaming for all LLM providers without special configuration.

## Configure OpenAI Route

Create an OpenAI secret and routing configuration (same as lab 004):

```bash
export OPENAI_API_KEY=$OPENAI_API_KEY

kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create the OpenAI route and backend:

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
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

Get the Gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

## Test Streaming Response

Send a request with `"stream": true` to enable streaming:

```bash
curl "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -d '{
    "stream": true,
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant who explains concepts clearly."
      },
      {
        "role": "user",
        "content": "In a couple of sentences, explain what streaming responses are and why they are useful."
      }
    ]
  }'
```

**Expected output:**

You'll see Server-Sent Events (SSE) format with incremental chunks:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1744306752,"model":"gpt-4o-mini-2024-07-18","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1744306752,"model":"gpt-4o-mini-2024-07-18","choices":[{"index":0,"delta":{"content":"Streaming"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1744306752,"model":"gpt-4o-mini-2024-07-18","choices":[{"index":0,"delta":{"content":" responses"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1744306752,"model":"gpt-4o-mini-2024-07-18","choices":[{"index":0,"delta":{"content":" allow"},"finish_reason":null}]}

...

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1744306752,"model":"gpt-4o-mini-2024-07-18","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**Key observations:**
- Each `data:` line contains a JSON chunk with incremental content
- The `delta.content` field contains the new tokens
- The final chunk has `"finish_reason":"stop"`
- The stream ends with `data: [DONE]`

## Observability

### View Streaming Metrics in Grafana

AgentGateway captures streaming-specific metrics:

1. Port-forward to Grafana:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Navigate to **Dashboards > AgentGateway Dashboard**

4. Look for streaming metrics:
   - **TTFT (Time to First Token)**: How quickly the first token was received
   - **TPOT (Time Per Output Token)**: Average time per token generation
   - **Streaming request rates**: Percentage of requests using streaming
   - **Token generation throughput**: Tokens generated per second

### View Access Logs

Check AgentGateway logs to see streaming request details:

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 20 | jq 'select(.scope == "request")'
```

For streaming requests, you'll see:
- `"gen_ai.operation.name": "chat"`
- `"gen_ai.request.model": "gpt-4o-mini"`
- `"gen_ai.usage.input_tokens"` and `"gen_ai.usage.output_tokens"`
- Streaming-specific timing metrics

**Note**: Unlike non-streaming responses, streaming responses don't include the `usage` object in the SSE stream. Token usage is tracked in AgentGateway's access logs and metrics.

### View Traces

Streaming requests generate the same distributed traces as non-streaming:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Search for recent traces
4. Compare trace durations between streaming and non-streaming requests

Streaming traces show timing for:
- Time to first chunk (TTFT)
- Total streaming duration
- Individual token generation patterns

## Use Cases for Streaming

**When to use streaming:**
- Interactive chatbots and assistants
- Real-time code generation interfaces
- Live content generation for users
- Any UI where users want to see progress

**When to use non-streaming:**
- Batch processing jobs
- Backend API integrations
- Cases where you need the complete `usage` object in the response
- Simple request/response patterns without UI updates

## Cleanup

Delete the lab resources:
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```
