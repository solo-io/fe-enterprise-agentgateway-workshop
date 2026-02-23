# Configure Timeouts and Retries with Backoff

In this lab, you'll learn how timeout, retry, and backoff policies interact together. You'll see how the gateway retries failed requests with configurable delays between attempts until the overall timeout is exceeded, demonstrating resilient request handling.

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`.

## Lab Objectives
- Deploy a mock OpenAI server
- Configure a combined timeout and retry policy with backoff
- Observe how timeouts, retries, and backoff work together
- Test different backoff configurations to see their impact on retry behavior
- See retry attempts in logs when retries exhaust the timeout

## Create a Mock vLLM Server

Deploy the mock server:

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
      - args:
        - --model
        - mock-gpt-4o
        - --port
        - "8000"
        - --max-loras
        - "2"
        - --lora-modules
        - '{"name": "food-review-1"}'
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        name: vllm-sim
        env:
          - name: POD_NAME
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.name
          - name: POD_NAMESPACE
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.namespace
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

## Configure Route and Backend

Create the HTTPRoute and AgentgatewayBackend:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai
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
        - name: mock-openai
          group: agentgateway.dev
          kind: AgentgatewayBackend
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mock-openai
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-4o"
      host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth:
      passthrough: {}
EOF
```

## Scale AgentGateway to 1 Replica

To make log observation easier, scale the AgentGateway deployment to 1 replica:

```bash
kubectl patch enterpriseagentgatewayparameters -n agentgateway-system agentgateway-config \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/deployment/spec/replicas", "value": 1}]'

kubectl rollout restart deploy/agentgateway-proxy -n agentgateway-system

kubectl get deploy -n agentgateway-system
```

## Configure Timeout and Retry Policy

Create a combined policy with timeout (100ms), retry (10 attempts), and backoff (25ms) configuration:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: timeout-retry-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-openai
  traffic:
    timeouts:
      request: 100ms
    retry:
      attempts: 10
      backoff: 25ms
      codes:
        - 503  # Service Unavailable
EOF
```

Verify the policy is attached:

```bash
kubectl get enterpriseagentgatewaypolicies -n agentgateway-system
```

Expected output:
```
NAMESPACE                 NAME                   ACCEPTED   ATTACHED
agentgateway-system   timeout-retry-policy   True       True
```

### Understanding the Configuration

- **Timeout**: 100ms total request timeout
- **Retry attempts**: 10 retries on 503 errors
- **Retry backoff**: 25ms delay between retry attempts
- **How they interact**: The gateway will retry up to 10 times with a 25ms delay between each retry, but will stop when the 100ms timeout is exceeded. With the backoff, fewer retries will occur before the timeout.

## Test Baseline (Successful Request)

Verify the mock server is working:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Say hello"
      }
    ]
  }'
```

You should receive a successful `200 OK` response.

## Trigger Timeout and Retry Behavior

Start tailing the AgentGateway logs in one terminal:

```bash
kubectl logs -f deploy/agentgateway-proxy -n agentgateway-system | jq 'select(.["retry.attempt"]) | {retry: .["retry.attempt"], status: ."http.status", duration, error}'
```

Back in the original terminal, scale the mock server deployment to 0 to trigger 503 errors:

```bash
kubectl scale deployment/mock-gpt-4o -n agentgateway-system --replicas=0
```

Make a request:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Say hello"
      }
    ]
  }'
```

Expected response:
```
HTTP/1.1 504 Gateway Timeout
content-type: text/plain
transfer-encoding: chunked
date: Wed, 07 Jan 2026 22:47:38 GMT

request timeout
```

Check the agentgateway logs, you should see that the request was retried a few times before the timeout
```json
{
  "retry": 3,
  "status": 504,
  "duration": "103ms",
  "error": "request timeout"
}
```

### What Happened?

1. **Request sent** to scaled-down backend (no pods available)
2. **503 Service Unavailable** returned
3. **Gateway retries** automatically (up to 10 attempts configured)
4. **Only 3 retries completed** - the 25ms backoff delays consume the timeout window quickly
5. **100ms timeout exceeded** after retry attempt 3
6. **504 Gateway Timeout** returned to client

This demonstrates that with a short timeout (100ms) and backoff delays (25ms), the timeout is reached before the max retry attempts (10), limiting actual retries to just 3.

## Test Retry Backoff with Longer Timeout

Now let's configure a policy with a longer timeout to observe the backoff behavior more clearly:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: timeout-retry-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-openai
  traffic:
    timeouts:
      request: 2s
    retry:
      attempts: 10
      backoff: 200ms
      codes:
        - 503  # Service Unavailable
EOF
```

This configuration sets:
- **2s timeout**: Longer window to observe retry behavior
- **10 retry attempts**: Maximum retries before giving up
- **200ms backoff**: Observable delay between each retry attempt

Make another request with the mock server still scaled to 0:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Say hello"
      }
    ]
  }'
```

Check the logs to see the retry attempts with backoff delays:

```bash
kubectl logs -f deploy/agentgateway-proxy -n agentgateway-system | jq 'select(.["retry.attempt"]) | {retry: .["retry.attempt"], status: ."http.status", duration, error}'
```

You should see output similar to:
```json
{
  "retry": 7,
  "status": 504,
  "duration": "2001ms",
  "error": "request timeout"
}
```

### Observing Backoff Behavior

With a 200ms backoff and 2s timeout, the gateway will make approximately:
- Initial request: 0ms
- Retry 1: ~200ms
- Retry 2: ~400ms
- Retry 3: ~600ms
- ...continuing until timeout

The total time consumed = (retry attempts × backoff delay) + network latency. With 200ms backoff and 2s timeout, you should see around **7 retry attempts** before the timeout is reached, demonstrating that the timeout limit is hit before the configured 10 max attempts.

## Cleanup

Scale AgentGateway back to 2 replicas:

```bash
kubectl patch enterpriseagentgatewayparameters -n agentgateway-system agentgateway-config \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/deployment/spec/replicas", "value": 2}]'

kubectl rollout restart deploy/agentgateway-proxy -n agentgateway-system
```

Delete the lab resources:

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system timeout-retry-policy
kubectl delete httproute -n agentgateway-system mock-openai
kubectl delete agentgatewaybackend -n agentgateway-system mock-openai
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o
```
