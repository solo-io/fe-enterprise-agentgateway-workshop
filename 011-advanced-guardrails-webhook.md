# Advanced Guardrails Webhook Endpoint

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Deploy guardrails webhook
- Add advanced guardrails webhook policy
- Validate that prompts are appropriately rejects or masked by the webhook endpoint
- Validate the request went through the gateway in Jaeger UI, and that the prompt has been enriched

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n gloo-system \
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
  namespace: gloo-system
spec:
  parentRefs:
    - name: gloo-agentgateway
      namespace: gloo-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai-all-models
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-all-models
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      openai:
        #--- Uncomment to configure model override ---
        #model: ""
        authToken:
          kind: "SecretRef"
          secretRef:
            name: openai-secret
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=gloo-agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
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

## Deploy guardrails webhook
The Gloo Documentation links to a [Sample Python Webhook Server to receive AI Gateway Guardrail Webook Calls](https://github.com/solo-io/gloo-gateway-use-cases/tree/main/ai-guardrail-webhook-server) which is meant to be extended and modified to create a working example. 

Thie following simple example guardrail webhook below has been extended to implement the following guardrails
- PII Patterns
- Banned Words
- Toxic Phrases

```bash
kubectl apply -f- <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  labels:
    account: ai-guardrail
  name: ai-guardrail
  namespace: gloo-system
---
apiVersion: v1
kind: Service
metadata:
  name: ai-guardrail-webhook
  namespace: gloo-system
  labels:
    app: ai-guardrail
spec:
  selector:
    app: ai-guardrail-webhook
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ai-guardrail-webhook
  namespace: gloo-system
  labels:
    app: ai-guardrail
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ai-guardrail-webhook
  template:
    metadata:
      labels:
        app: ai-guardrail-webhook
    spec:
      containers:
      - name: webhook
        image: ably7/ai-guardrail-webhook-server:0.0.5
        ports:
        - containerPort: 8000
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"
      serviceAccountName: ai-guardrail
EOF
```

Check that the ai-guardrail-webhook has been deployed
```bash
kubectl get pods -n gloo-system -l app=ai-guardrail-webhook
```

## Apply prompt enrichment policy
```bash
kubectl apply -f- <<EOF
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: openai-opt
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      request:
        customResponse:
          message: "Your request was rejected due to inappropriate content"
          statusCode: 403
        webhook:
          host:
            host: "ai-guardrail-webhook.gloo-system.svc.cluster.local"
            port: 8000
      response:
        webhook:
          host:
            host: "ai-guardrail-webhook.gloo-system.svc.cluster.local"
            port: 8000
EOF
```

## curl openai

We will use the same request again, and it should not be blocked because it shouldn't violate any of our rules
```bash
curl -i "$GATEWAY_IP:8080/openai" \
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

To see that the request went to the webhook endoint we can tail the logs of that service
```bash
kubectl logs -n gloo-system deploy/ai-guardrail-webhook --tail 5
```

Example output
```
2025-09-23 18:50:54,285 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.gloo-system.svc.cluster.local:8000, content-length: 346
2025-09-23 18:50:54,285 [INFO] 📥 Incoming /request webhook
2025-09-23 18:50:54,286 [INFO] → Message[0] role=assistant: I don't have personal preferences, but one poem that many people admire is "The Road Not Taken" by Robert Frost. It evokes themes of choice and individuality, and its imagery resonates with a lot of readers. If you're interested in a particular type of poem or theme, I can suggest more!
2025-09-23 18:50:54,286 [INFO] ✅ PassAction returned (request)
INFO:     10.42.0.10:48306 - "POST /request HTTP/1.1" 200 OK
```

## Malicious request - toxic language

Now we will test a malicious request that contains toxic language that should be blocked by the webhook endpoint
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "You are stupid chatbot and i hate you"
      }
    ]
  }'
```

This request should be rejected
```
HTTP/1.1 403 Forbidden
content-length: 63
date: Tue, 23 Sep 2025 18:55:37 GMT

Rejected due to toxic language: matched phrase 'you are stupid'
```

To see that the request went to the webhook endoint we can tail the logs of that service
```bash
kubectl logs -n gloo-system deploy/ai-guardrail-webhook --tail 5
```

Example logs
```
2025-09-23 19:07:33,365 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.gloo-system.svc.cluster.local:8000, content-length: 89
2025-09-23 19:07:33,366 [INFO] 📥 Incoming /request webhook
2025-09-23 19:07:33,366 [INFO] → Message[0] role=user: You are stupid chatbot and i hate you
2025-09-23 19:07:33,366 [WARNING] ⛔ RejectAction triggered: toxic phrase matched: 'you are stupid'
```

## Malicious request - banned word

Now we will test a malicious request that contains toxic language that should be blocked by the webhook endpoint
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "This story promotes violence and should be stopped."
      }
    ]
  }'
```

This request should be rejected
```
HTTP/1.1 403 Forbidden
content-length: 62
date: Tue, 23 Sep 2025 19:02:23 GMT

Rejected due to inappropriate content: matched word 'violence'
```

To see that the request went to the webhook endoint we can tail the logs of that service
```bash
kubectl logs -n gloo-system deploy/ai-guardrail-webhook --tail 15
```

Example logs
```
2025-09-23 19:08:31,886 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.gloo-system.svc.cluster.local:8000, content-length: 103
2025-09-23 19:08:31,886 [INFO] 📥 Incoming /request webhook
2025-09-23 19:08:31,886 [INFO] → Message[0] role=user: This story promotes violence and should be stopped.
2025-09-23 19:08:31,886 [WARNING] ⛔ RejectAction triggered: banned word matched: 'violence'
```

## Mask credit cards
Now we will test a malicious request that contains a known credit card pattern that should be masked by the webhook endpoint
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Repeat the following exactly: Here is my number: 4111 1111 1111 1111."
      }
    ]
  }'
```

The response should be masked
```
{"id":"chatcmpl-CJ2akrEd2lusaGgJoGdVwGcpFCz6Y","choices":[{"index":0,"message":{"content":"Here is my number: ****.","role":"assistant"},"finish_reason":"stop"}],"created":1758654230,"model":"gpt-4o-mini-2024-07-18","service_tier":"default","system_fingerprint":"fp_560af6e559","object":"chat.completion","usage":{"prompt_tokens":19,"completion_tokens":7,"total_tokens":26,"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0},"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0}}}
```

To see that the request went to the webhook endoint we can tail the logs of that service
```bash
kubectl logs -n gloo-system deploy/ai-guardrail-webhook --tail 15
```

Example logs
```
2025-09-23 19:09:14,306 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.gloo-system.svc.cluster.local:8000, content-length: 121
2025-09-23 19:09:14,306 [INFO] 📥 Incoming /request webhook
2025-09-23 19:09:14,306 [INFO] → Message[0] role=user: Repeat the following exactly: Here is my number: 4111 1111 1111 1111.
2025-09-23 19:09:14,306 [INFO] 🔒 Matched PII pattern: \b(?:\d[ -]*?){13,16}\b
2025-09-23 19:09:14,307 [INFO] 🔒 Masking content: Repeat the following exactly: Here is my number: 4111 1111 1111 1111. → Repeat the following exactly: Here is my number: ****.
2025-09-23 19:09:14,307 [INFO] ✅ MaskAction returned (request)
INFO:     10.42.0.10:56944 - "POST /request HTTP/1.1" 200 OK
2025-09-23 19:09:15,375 [INFO] ✨ Adding trace for gloo-ai-request-webhook
2025-09-23 19:09:15,379 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.gloo-system.svc.cluster.local:8000, content-length: 81
2025-09-23 19:09:15,379 [INFO] 📥 Incoming /request webhook
2025-09-23 19:09:15,379 [INFO] → Message[0] role=assistant: Here is my number: ****.
2025-09-23 19:09:15,379 [INFO] ✅ PassAction returned (request)
```

## Mask SSN Numbers
Now we will test a malicious request that contains an email, which should be masked by the webhook endpoint
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Repeat the following exactly: You can email me at support@example.com"
      }
    ]
  }'
```

The response should be masked
```
{"id":"chatcmpl-CJ2co9pjeLrGgQzY3RGl53Xnwp0qp","choices":[{"index":0,"message":{"content":"You can email me at ****","role":"assistant"},"finish_reason":"stop"}],"created":1758654358,"model":"gpt-4o-mini-2024-07-18","service_tier":"default","system_fingerprint":"fp_560af6e559","object":"chat.completion","usage":{"prompt_tokens":18,"completion_tokens":6,"total_tokens":24,"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0},"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0}}}
```

To see that the request went to the webhook endoint we can tail the logs of that service
```bash
kubectl logs -n gloo-system deploy/ai-guardrail-webhook --tail 15
```

Example logs
```
2025-09-23 19:10:13,428 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.gloo-system.svc.cluster.local:8000, content-length: 121
2025-09-23 19:10:13,428 [INFO] 📥 Incoming /request webhook
2025-09-23 19:10:13,428 [INFO] → Message[0] role=user: Repeat the following exactly: You can email me at support@example.com
2025-09-23 19:10:13,428 [INFO] 🔒 Matched PII pattern: \b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b
2025-09-23 19:10:13,428 [INFO] 🔒 Masking content: Repeat the following exactly: You can email me at support@example.com → Repeat the following exactly: You can email me at ****
2025-09-23 19:10:13,429 [INFO] ✅ MaskAction returned (request)
INFO:     10.42.0.10:40274 - "POST /request HTTP/1.1" 200 OK
2025-09-23 19:10:14,931 [INFO] ✨ Adding trace for gloo-ai-request-webhook
2025-09-23 19:10:14,934 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.gloo-system.svc.cluster.local:8000, content-length: 81
2025-09-23 19:10:14,934 [INFO] 📥 Incoming /request webhook
2025-09-23 19:10:14,935 [INFO] → Message[0] role=assistant: You can email me at ****
2025-09-23 19:10:14,935 [INFO] ✅ PassAction returned (request)
```

## Port-forward to Jaeger UI
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see that our rejected responses have a `http.status` of `403` and for our masked responses the `gen_ai.completion` tag will show the masked value

Example of a masked response trace in Jaeger
```
{
  "key": "gen_ai.completion",
  "type": "string",
  "value": "[{\"content\": \"You can email me at ****\", \"role\": \"assistant\"}]"
}
```

## Cleanup
```bash
kubectl delete sa -n gloo-system ai-guardrail
kubectl delete service -n gloo-system ai-guardrail-webhook
kubectl delete deployment -n gloo-system ai-guardrail-webhook
kubectl delete glootrafficpolicy -n gloo-system openai-opt
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
```