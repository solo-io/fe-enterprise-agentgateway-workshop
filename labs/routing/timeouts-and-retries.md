# Configure Timeouts and Retries with Backoff

In this lab, you'll learn how timeout, retry, and backoff policies interact together. You'll see how the gateway retries failed requests with configurable delays between attempts until the overall timeout is exceeded.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Deploy two mock OpenAI servers: one healthy, and one that answers every request with `503`
- Configure a combined timeout and retry policy with backoff
- Observe how timeouts, retries, and backoff work together
- Test different backoff configurations to see their impact on retry behavior
- See retry attempts in logs when retries exhaust the timeout

## Create the Mock vLLM Servers

Deploy the healthy mock server:

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

Deploy a second mock server that answers every request with `503 Service Unavailable`. `--failure-injection-rate 100` makes the vLLM simulator fail every request, and `--failure-types server_error` makes each failure a `503`. Both mocks run for the whole lab, so the gateway already knows the failing mock's endpoint when you test retries:

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o-503
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-4o-503
  template:
    metadata:
      labels:
        app: mock-gpt-4o-503
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
        - --failure-injection-rate
        - "100"
        - --failure-types
        - server_error
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
  name: mock-gpt-4o-503-svc
  namespace: agentgateway-system
  labels:
    app: mock-gpt-4o-503
spec:
  selector:
    app: mock-gpt-4o-503
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
      name: http
  type: ClusterIP
EOF
```

## Configure Route and Model

Create an HTTPRoute and an `AgentgatewayModel` for each mock server. `/openai` goes to the healthy mock and `/openai-503` goes to the failing one:

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
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: mock-openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-openai
  match:
    model: "*"
  provider: OpenAI
  baseURL: http://mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
    transformations:
      - field: model
        expression: '"mock-gpt-4o"'
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai-503
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai-503
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: mock-openai-503
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-openai-503
  match:
    model: "*"
  provider: OpenAI
  baseURL: http://mock-gpt-4o-503-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
    transformations:
      - field: model
        expression: '"mock-gpt-4o"'
EOF
```

## Scale AgentGateway to 1 Replica

To make log observation easier, scale the AgentGateway deployment to 1 replica:

```bash
kubectl patch enterpriseagentgatewayparameters -n agentgateway-system agentgateway-config \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/deployment/spec/replicas", "value": 1}]'

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
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-openai-503
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

The gateway retries a 503 up to 10 times with a 25ms delay between each retry, but stops when the 100ms request timeout is exceeded. With the backoff, fewer retries occur before the timeout.

## Test Baseline (Successful Request)

Verify the mock server is working:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
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
kubectl logs -n agentgateway-system deploy/agentgateway-proxy --tail 20
```

Back in the original terminal, send a request to the failing mock:

```bash
curl -i "$GATEWAY_IP:8080/openai-503/v1/chat/completions" \
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

Check the agentgateway logs, you should see that the request was retried a few times before the timeout. The access log is a single logfmt line; these are the relevant fields from it:

```
http.status=504 retry.attempt=3 error="request timeout" reason=Timeout duration=101ms
```

### What Happened?

1. Request sent to the failing mock server
2. The mock returns 503 Service Unavailable
3. Gateway retries automatically (up to 10 attempts configured)
4. Only 3 retries completed: the 25ms backoff delays consume the timeout window quickly
5. 100ms timeout exceeded after retry attempt 3
6. 504 Gateway Timeout returned to client

With a short timeout (100ms) and backoff delays (25ms), the timeout is reached before the max retry attempts (10), so only 3 retries complete.

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
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-openai-503
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

The longer 2s timeout leaves room for several 200ms backoff delays before the request times out.

Send another request to the failing mock:

```bash
curl -i "$GATEWAY_IP:8080/openai-503/v1/chat/completions" \
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
kubectl logs -n agentgateway-system deploy/agentgateway-proxy --tail 20
```

You should see output similar to:

```
http.status=504 retry.attempt=9 error="request timeout" reason=Timeout duration=2001ms
```

### Observing Backoff Behavior

With a 200ms backoff and 2s timeout, the gateway will make approximately:
- Initial request: 0ms
- Retry 1: ~200ms
- Retry 2: ~400ms
- Retry 3: ~600ms
- ...continuing until timeout

The total time consumed = (retry attempts × backoff delay) + network latency. With 200ms backoff and 2s timeout, you should see around **9 retry attempts** before the timeout is reached.

## Cleanup

Scale AgentGateway back to 2 replicas:

```bash
kubectl patch enterpriseagentgatewayparameters -n agentgateway-system agentgateway-config \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/deployment/spec/replicas", "value": 2}]'
```

Delete the lab resources:

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system timeout-retry-policy
kubectl delete httproute -n agentgateway-system mock-openai mock-openai-503
kubectl delete agentgatewaymodel -n agentgateway-system mock-openai mock-openai-503
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc svc/mock-gpt-4o-503-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o deploy/mock-gpt-4o-503
```
