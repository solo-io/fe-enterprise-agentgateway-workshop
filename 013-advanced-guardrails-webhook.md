# Advanced Guardrails Webhook Endpoint

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `AgentgatewayBackend` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Deploy guardrails webhook
- Add advanced guardrails webhook policy
- Validate that prompts are appropriately rejects or masked by the webhook endpoint
- Validate the request went through the gateway in the Grafana UI, and that the prompt has been enriched

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
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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
The following documentation links to a [Sample Python Webhook Server to receive AI Gateway Guardrail Webook Calls](https://github.com/solo-io/gloo-gateway-use-cases/tree/main/ai-guardrail-webhook-server) which is meant to be extended and modified to create a working example. 

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
  namespace: agentgateway-system
---
apiVersion: v1
kind: Service
metadata:
  name: ai-guardrail-webhook
  namespace: agentgateway-system
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
  namespace: agentgateway-system
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
kubectl get pods -n agentgateway-system -l app=ai-guardrail-webhook
```

## Apply prompt guard policy
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: openai-prompt-guard
  namespace: agentgateway-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  backend:
    ai:
      promptGuard:
        request:
          - webhook:
              backendRef:
                name: ai-guardrail-webhook
                namespace: agentgateway-system
                kind: Service
                port: 8000
        response:
          - webhook:
              backendRef:
                name: ai-guardrail-webhook
                namespace: agentgateway-system
                kind: Service
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
kubectl logs -n agentgateway-system deploy/ai-guardrail-webhook --tail 5
```

Example output
```
2025-09-23 18:50:54,285 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.agentgateway-system.svc.cluster.local:8000, content-length: 346
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
kubectl logs -n agentgateway-system deploy/ai-guardrail-webhook --tail 5
```

Example logs
```
2025-09-23 19:07:33,365 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.agentgateway-system.svc.cluster.local:8000, content-length: 89
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
kubectl logs -n agentgateway-system deploy/ai-guardrail-webhook --tail 15
```

Example logs
```
2025-09-23 19:08:31,886 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.agentgateway-system.svc.cluster.local:8000, content-length: 103
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
kubectl logs -n agentgateway-system deploy/ai-guardrail-webhook --tail 15
```

Example logs
```
2025-09-23 19:09:14,306 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.agentgateway-system.svc.cluster.local:8000, content-length: 121
2025-09-23 19:09:14,306 [INFO] 📥 Incoming /request webhook
2025-09-23 19:09:14,306 [INFO] → Message[0] role=user: Repeat the following exactly: Here is my number: 4111 1111 1111 1111.
2025-09-23 19:09:14,306 [INFO] 🔒 Matched PII pattern: \b(?:\d[ -]*?){13,16}\b
2025-09-23 19:09:14,307 [INFO] 🔒 Masking content: Repeat the following exactly: Here is my number: 4111 1111 1111 1111. → Repeat the following exactly: Here is my number: ****.
2025-09-23 19:09:14,307 [INFO] ✅ MaskAction returned (request)
INFO:     10.42.0.10:56944 - "POST /request HTTP/1.1" 200 OK
2025-09-23 19:09:15,375 [INFO] ✨ Adding trace for ai-request-webhook
2025-09-23 19:09:15,379 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.agentgateway-system.svc.cluster.local:8000, content-length: 81
2025-09-23 19:09:15,379 [INFO] 📥 Incoming /request webhook
2025-09-23 19:09:15,379 [INFO] → Message[0] role=assistant: Here is my number: ****.
2025-09-23 19:09:15,379 [INFO] ✅ PassAction returned (request)
```

## Mask Emails
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
kubectl logs -n agentgateway-system deploy/ai-guardrail-webhook --tail 15
```

Example logs
```
2025-09-23 19:10:13,428 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.agentgateway-system.svc.cluster.local:8000, content-length: 121
2025-09-23 19:10:13,428 [INFO] 📥 Incoming /request webhook
2025-09-23 19:10:13,428 [INFO] → Message[0] role=user: Repeat the following exactly: You can email me at support@example.com
2025-09-23 19:10:13,428 [INFO] 🔒 Matched PII pattern: \b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b
2025-09-23 19:10:13,428 [INFO] 🔒 Masking content: Repeat the following exactly: You can email me at support@example.com → Repeat the following exactly: You can email me at ****
2025-09-23 19:10:13,429 [INFO] ✅ MaskAction returned (request)
INFO:     10.42.0.10:40274 - "POST /request HTTP/1.1" 200 OK
2025-09-23 19:10:14,931 [INFO] ✨ Adding trace for ai-request-webhook
2025-09-23 19:10:14,934 [INFO] 📬 Request headers: content-type: application/json, host: ai-guardrail-webhook.agentgateway-system.svc.cluster.local:8000, content-length: 81
2025-09-23 19:10:14,934 [INFO] 📥 Incoming /request webhook
2025-09-23 19:10:14,935 [INFO] → Message[0] role=assistant: You can email me at ****
2025-09-23 19:10:14,935 [INFO] ✅ PassAction returned (request)
```

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

### View Metrics and Traces in Grafana

For a comprehensive view of metrics and traces, use the AgentGateway Grafana dashboard installed in lab 002.

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

### View Traces in Grafana

To view distributed traces with LLM-specific spans:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Filter traces by service, operation, or trace ID to find AgentGateway requests

Traces include LLM-specific spans with information like `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more.

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout:

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 1
```

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

### (Optional) View Traces in Jaeger

If you installed Jaeger in lab `/install-on-openshift/002-set-up-monitoring-tools-ocp.md` instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans including `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete sa -n agentgateway-system ai-guardrail
kubectl delete service -n agentgateway-system ai-guardrail-webhook
kubectl delete deployment -n agentgateway-system ai-guardrail-webhook
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system openai-prompt-guard
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```