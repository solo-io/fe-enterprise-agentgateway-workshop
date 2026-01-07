# Configure Timeouts and Retries

In this lab, you'll learn how timeout and retry policies interact together. You'll see how the gateway retries failed requests until the overall timeout is exceeded, demonstrating resilient request handling.

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`.

## Lab Objectives
- Deploy a mock OpenAI server
- Configure a combined timeout and retry policy
- Observe how timeouts and retries work together
- See `retry.attempt=10` in logs when retries exhaust the timeout

## Create a Mock vLLM Server

Deploy the mock server:

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o
  namespace: enterprise-agentgateway
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
  namespace: enterprise-agentgateway
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
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
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
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-4o"
      host: mock-gpt-4o-svc.enterprise-agentgateway.svc.cluster.local
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
kubectl patch enterpriseagentgatewayparameters -n enterprise-agentgateway agentgateway-params \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/deployment/spec/replicas", "value": 1}]'

kubectl rollout restart deploy/agentgateway -n enterprise-agentgateway

kubectl get deploy -n enterprise-agentgateway
```

## Configure Timeout and Retry Policy

Create a combined policy with both timeout (100ms) and retry (10 attempts) configuration:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: timeout-retry-policy
  namespace: enterprise-agentgateway
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
      codes:
        - 503  # Service Unavailable
EOF
```

Verify the policy is attached:

```bash
kubectl get enterpriseagentgatewaypolicies -n enterprise-agentgateway
```

Expected output:
```
NAMESPACE                 NAME                   ACCEPTED   ATTACHED
enterprise-agentgateway   timeout-retry-policy   True       True
```

### Understanding the Configuration

- **Timeout**: 100ms total request timeout
- **Retry attempts**: 10 retries on 503 errors
- **How they interact**: The gateway will retry up to 10 times, but will stop when the 100ms timeout is exceeded

## Test Baseline (Successful Request)

Verify the mock server is working:

```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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
kubectl logs -f deploy/agentgateway -n enterprise-agentgateway | jq 'select(.["retry.attempt"]) | {retry: .["retry.attempt"], status: ."http.status", duration, error}'
```

Back in the original terminal, scale the mock server deployment to 0 to trigger 503 errors:

```bash
kubectl scale deployment/mock-gpt-4o -n enterprise-agentgateway --replicas=0
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

Check the agentgateway logs, you should see that the request was retried 10 times before the timeout
```json
{
  "retry": 10,
  "status": 504,
  "duration": "101ms",
  "error": "request timeout"
}
```

### What Happened?

1. **Request sent** to scaled-down backend (no pods available)
2. **503 Service Unavailable** returned
3. **Gateway retries** automatically (up to 10 attempts configured)
4. **Retries continue** until the 100ms timeout is exceeded
5. **504 Gateway Timeout** returned to client
6. **All retry attempts logged** showing `retry.attempt=10`

## Cleanup

Scale AgentGateway back to 2 replicas:

```bash
kubectl patch enterpriseagentgatewayparameters -n enterprise-agentgateway agentgateway-params \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/deployment/spec/replicas", "value": 2}]'

kubectl rollout restart deploy/agentgateway -n enterprise-agentgateway
```

Delete the lab resources:

```bash
kubectl delete enterpriseagentgatewaypolicy -n enterprise-agentgateway timeout-retry-policy
kubectl delete httproute -n enterprise-agentgateway mock-openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway mock-openai
kubectl delete -n enterprise-agentgateway svc/mock-gpt-4o-svc
kubectl delete -n enterprise-agentgateway deploy/mock-gpt-4o
```
