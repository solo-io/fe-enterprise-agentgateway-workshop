# Advanced LLM Failover Patterns

This lab walks through three advanced failover patterns on Enterprise AgentGateway: intra-priority-group failover, eviction on 5XX server errors, and the combined behavior that proves intra-group P2C load balancing, per-provider eviction, and inter-group failover work together.

If you have already completed the [LLM Failover](llm-failover.md) lab, you can skip the **Base Setup** section below; your resources are already in place.

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives

- Demonstrate intra-priority-group failover (load balancing across models that share a priority, with per-model eviction)
- Trigger eviction on 5XX server errors via a separate mock and CEL expression
- Combine all behaviors in a single virtual model: load balancing within a priority, per-model eviction, and failover to the next priority only when every model at the current priority is evicted

## Base Setup

The patterns below reuse a single 429-returning mock server, an OpenAI secret, and a single-replica AgentGateway deployment. If you already have these from the [LLM Failover](llm-failover.md) lab, skip this section.

### Deploy Mock Server with Rate Limiting

Deploy the vllm-sim mock server configured to always return 429 rate limit errors:

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
        # Failure Injection - 100% rate limit errors
        - --failure-injection-rate
        - "100"
        - --failure-types
        - "rate_limit"
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

### Configure OpenAI Secret

Create a Kubernetes secret with your OpenAI API key for the failover backend:

```bash
export OPENAI_API_KEY=$OPENAI_API_KEY

kubectl create secret generic openai-secret -n agentgateway-system \
  --from-literal="Authorization=Bearer $OPENAI_API_KEY" \
  --dry-run=client -oyaml | kubectl apply -f -
```

### Configure Single Replica for Consistent Testing

Provider health state is local to each pod. With multiple replicas, different pods maintain separate health states, which can lead to inconsistent failover behavior. Scale to one replica for predictable test results:

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --type=merge -p '{"spec":{"deployment":{"spec":{"replicas":1}}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

### Export the Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### Apply the Base Models and Route

Pattern 1 modifies the `resilient` virtual model created here; later patterns create their own. Clients request the model `resilient`, and the virtual model fails over from the mock (priority 0) to OpenAI (priority 1). The health policy lives on the mock model.

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-ratelimit-failover
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
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-ratelimit-failover
  match:
    model: mock-gpt-4o
  visibility: Internal
  provider: OpenAI
  baseURL: http://mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
    health:
      unhealthyCondition: "response.code >= 500 || response.code == 429"
      eviction:
        duration: 60s
        consecutiveFailures: 1
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-gpt-5-4-nano
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-ratelimit-failover
  match:
    model: gpt-5.4-nano
  visibility: Internal
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: resilient
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-ratelimit-failover
  match:
    model: resilient
  virtualModel:
    failover:
      targets:
        - modelRef:
            name: mock-gpt-4o
          priority: 0
        - modelRef:
            name: openai-gpt-5-4-nano
          priority: 1
EOF
```

---

## Pattern 1: Intra-Priority-Group Failover

The simplest failover case is between priorities (priority 0 to priority 1 when priority 0 fails). This pattern tests a more nuanced scenario: failover between multiple models that share a priority.

### Why This Matters

When several failover targets share a priority:
- The gateway load balances between all healthy models at that priority, scoring them by health and latency
- When one model fails, it's ejected from the pool
- Traffic continues to healthy models at the same priority
- The gateway only moves to the next priority when ALL models at the current priority are unhealthy

This enables:
- Heterogeneous models: Mix different provider types or model tiers at the same priority
- Partial failure handling: Keep serving traffic even when some models fail
- Quality preservation: Stay in the preferred tier with better models as long as ANY of them is healthy
- Graceful degradation: Fall back to lower-quality but functional models only when necessary

### Health Policy

The `mock-gpt-4o` model created in the Base Setup carries the health policy, so it continues to apply here. No additional policy configuration is needed; the same `unhealthyCondition` and `eviction` settings govern this scenario. To make another model evictable, add the same `policies.health` block to it.

### Test Scenario

This pattern demonstrates a real-world graceful degradation setup:

**Priority 0 (Preferred):**
1. Mock server (fails with 429)
2. OpenAI gpt-5.6-terra (healthy, more capable model)

**Priority 1 (Degraded Mode):**
1. OpenAI gpt-5.4-nano (less capable but faster/cheaper fallback)

Priority 1 uses a less proficient model (gpt-5.4-nano) to ensure users get *some* response even if it's lower quality. However, since priority 0 has a healthy model (gpt-5.6-terra), priority 1 should NOT be reached in this test.

### Update Configuration

First, restart the AgentGateway to clear any existing health state from previous tests:

```bash
kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

Now add a gpt-5.6-terra model and put it at priority 0 next to the mock:

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-gpt-5-6-terra
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-ratelimit-failover
  match:
    model: gpt-5.6-terra
  visibility: Internal
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: resilient
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-ratelimit-failover
  match:
    model: resilient
  virtualModel:
    failover:
      targets:
        - modelRef:
            name: mock-gpt-4o
          priority: 0
        - modelRef:
            name: openai-gpt-5-6-terra
          priority: 0
        - modelRef:
            name: openai-gpt-5-4-nano
          priority: 1
EOF
```

**Key differences from the Base Setup:**
- Priority 0 now has TWO models: `mock-gpt-4o` and `openai-gpt-5-6-terra` (gpt-5.6-terra)
- Priority 1 uses a less capable model (gpt-5.4-nano) as a "degraded mode" fallback
- This demonstrates graceful degradation: prefer the more capable model, but ensure users get *some* response (even if lower quality) if all preferred models fail
- In this test, priority 1 should never be reached because priority 0 has a healthy gpt-5.6-terra model

### Test Intra-Priority-Group Failover

Send three requests to observe the failover pattern within priority 0:

```bash
for i in 1 2 3; do
  echo "=== Request $i ==="
  curl -s -w "\nHTTP Status: %{http_code}\n" \
    "$GATEWAY_IP:8080/openai/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model": "resilient", "messages": [{"role": "user", "content": "What is 2+2?"}]}'
done
```

**Expected output pattern:**

```
=== Request 1 ===
{"error":{"message":"Rate limit reached for mock-gpt-4o...","type":"RateLimitError","code":429}}
HTTP Status: 429
=== Request 2 ===
{"model":"gpt-5.6-terra","choices":[{"message":{"content":"Four.",...}],...}
HTTP Status: 200
=== Request 3 ===
{"model":"gpt-5.6-terra","choices":[{"message":{"content":"Four.",...}],...}
HTTP Status: 200
```

### Verify Failover in Logs

Check the AgentGateway logs to confirm which models handled each request:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

**Expected log output** (trimmed to the fields that matter):

```
endpoint=mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000 http.path=/openai/v1/chat/completions http.status=429 duration=2ms
endpoint=api.openai.com:443 http.path=/openai/v1/chat/completions http.status=200 duration=861ms
endpoint=api.openai.com:443 http.path=/openai/v1/chat/completions http.status=200 duration=491ms
```

**Key observations:**
- Request 1: Routed to `mock-gpt-4o-svc` → returned 429 → mock ejected
- Requests 2-3: Routed to `api.openai.com` (priority 0's healthy gpt-5.6-terra model) → returned 200
- The endpoint does NOT switch back to the mock server
- Priority 1 (with gpt-5.4-nano) is never reached because priority 0 has a healthy model
- Users get responses from the more capable gpt-5.6-terra model, not the degraded gpt-5.4-nano fallback

### What This Proves

This pattern demonstrates that:
1. Intra-pool failover: Failover works between models that share a priority
2. Model ejection: The gateway ejects an unhealthy model and skips it on subsequent requests
3. Priority preference: The gateway stays at the current priority as long as ANY model there is healthy
4. Graceful degradation: The gateway drops to the next priority, and its less capable models (gpt-5.4-nano), only when every model at the preferred priorities has failed
5. Quality preservation: Users get responses from the more capable model (gpt-5.6-terra) when available

The result trades answer quality for availability only when it has to: priority 0 carries the models you prefer, and priority 1 still returns an answer, at lower quality, once every model in the preferred tier is down.

---

## Pattern 2: 5XX Server Error Failover

Pattern 1 demonstrated failover triggered by 429 rate limit errors. This pattern tests failover triggered by 5XX server errors. A model's health policy can handle any error condition defined by the CEL expression.

### Why This Matters

LLM providers can fail with more than just rate limits. Server errors (500, 502, 503) indicate the backend is experiencing issues and should be temporarily removed from the pool. With a model's health policy, you can define exactly which error codes trigger eviction using CEL expressions.

### Deploy Mock Server with Server Errors

Deploy a second mock server configured to always return 503 Service Unavailable errors:

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o-500
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-4o-500
  template:
    metadata:
      labels:
        app: mock-gpt-4o-500
    spec:
      containers:
      - args:
        - --model
        - mock-gpt-4o-500
        - --port
        - "8000"
        - --max-loras
        - "2"
        - --lora-modules
        - '{"name": "food-review-1"}'
        # Failure Injection - 100% server errors (returns 503)
        - --failure-injection-rate
        - "100"
        - --failure-types
        - "server_error"
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
  name: mock-gpt-4o-500-svc
  namespace: agentgateway-system
  labels:
    app: mock-gpt-4o-500
spec:
  selector:
    app: mock-gpt-4o-500
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
      name: http
  type: ClusterIP
EOF
```

Verify the deployment:
```bash
kubectl get pods -n agentgateway-system -l app=mock-gpt-4o-500
```

### Create 5XX Failover Configuration

Configure a separate HTTPRoute and models for the 5XX failover scenario. The virtual model also matches `resilient`, but it attaches to its own route, so it does not collide with the Base Setup model. Note that the mock's health policy uses `unhealthyCondition: "response.code >= 500"` to evict only on server errors:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-5xx-failover
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai-5xx
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
  name: mock-gpt-4o-500
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-5xx-failover
  match:
    model: mock-gpt-4o-500
  visibility: Internal
  provider: OpenAI
  baseURL: http://mock-gpt-4o-500-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
    health:
      unhealthyCondition: "response.code >= 500"
      eviction:
        duration: 30s
        consecutiveFailures: 1
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-gpt-5-4-nano-5xx
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-5xx-failover
  match:
    model: gpt-5.4-nano
  visibility: Internal
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: resilient-5xx
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-5xx-failover
  match:
    model: resilient
  virtualModel:
    failover:
      targets:
        - modelRef:
            name: mock-gpt-4o-500
          priority: 0
        - modelRef:
            name: openai-gpt-5-4-nano-5xx
          priority: 1
EOF
```

**Key differences from the 429 example:**
- The `unhealthyCondition` uses `"response.code >= 500"`: only server errors trigger eviction, not rate limits
- The `eviction.duration: 30s` controls how long the mock is removed from the pool
- The route uses `/openai-5xx` so both examples can coexist during testing

### Test 5XX Failover

Restart the AgentGateway to clear any existing health state:

```bash
kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

Send three requests to observe the 5XX failover pattern:

```bash
for i in 1 2 3; do
  echo "=== Request $i ==="
  curl -s -w "\nHTTP Status: %{http_code}\n" \
    "$GATEWAY_IP:8080/openai-5xx/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model": "resilient", "messages": [{"role": "user", "content": "What is 2+2?"}]}'
done
```

**Expected response pattern:**
- Request 1: `503 Service Unavailable` from mock-gpt-4o-500
- Request 2: `200 OK` from OpenAI (failover successful)
- Request 3: `200 OK` from OpenAI (continues using failover)

**What's happening:**
1. The first request hits the mock server and receives a 503 error
2. The mock model's health policy evaluates `response.code >= 500` → `true` (503 >= 500), and since `consecutiveFailures: 1`, the mock is evicted immediately for 30 seconds
3. Subsequent requests are routed to priority 1 (OpenAI) and receive successful 200 responses
4. After 30 seconds, the gateway will retry the mock server to check if it has recovered
5. Since the mock server always returns 503, the cycle repeats with the eviction duration increasing via multiplicative backoff

### Verify 5XX Failover in Logs

Check the AgentGateway logs to confirm the failover behavior:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

**Expected log output** (trimmed to the fields that matter):

```
endpoint=mock-gpt-4o-500-svc.agentgateway-system.svc.cluster.local:8000 http.path=/openai-5xx/v1/chat/completions http.status=503 duration=2ms
endpoint=api.openai.com:443 http.path=/openai-5xx/v1/chat/completions http.status=200 duration=861ms
```

**Key observations:**
- Request 1: Routed to `mock-gpt-4o-500-svc` → returned 503 → mock evicted
- Request 2+: Routed to `api.openai.com` → returned 200 (failover successful)
- The `unhealthyCondition` CEL expression correctly identified the 503 response as unhealthy (`503 >= 500`) and triggered eviction

### What This Proves

This pattern demonstrates that:
1. Non-429 failover: The model health policy enables failover for any error condition, not just rate limits
2. CEL flexibility: The `unhealthyCondition` expression can target specific error ranges (`>= 500`), individual codes (`== 429`), or combinations (`>= 500 || == 429`)
3. Policy-controlled duration: The health policy's `eviction.duration` controls how long the model is removed from the pool
4. Multiplicative backoff: Repeated evictions increase the eviction duration automatically, preventing rapid cycling on persistently failing models

---

## Pattern 3: Combined LB + Per-Model Eviction + Inter-Priority Failover

The previous patterns each demonstrated one piece of the failover model. This pattern combines all three behaviors in a single virtual model to address a common real-world requirement: running multiple models at the same priority, where the platform must load balance across them, evict individual models that return 429 or 5xx responses, and fail over to the next priority **only** once the current priority has no usable models left.

The piece this pattern clarifies is *where health tracking lives*. Eviction is tracked **per model**: each concrete `AgentgatewayModel` with a `policies.health` block is individually evictable. The decision to move to the next priority is derived from that per-model state: it triggers only when every model at the current priority has been evicted. In other words, load balancing within a priority, per-model eviction, and failover across priorities are the same mechanism observed at different scopes.

This pattern proves the behavior end-to-end on the cluster by putting the 429 mock and the 503 mock at priority 0, with OpenAI at priority 1 as the fallback.

### Prerequisites

This pattern reuses resources from earlier in this lab:
- `mock-gpt-4o` deployment and `openai-secret` from the **Base Setup** section
- `mock-gpt-4o-500` deployment from **Pattern 2** above

If you have not, complete those sections before continuing.

### Apply the Combined Configuration

Create a new HTTPRoute and models that put both mocks at priority 0 and OpenAI at priority 1. The route uses `/openai-combo` so it coexists with the earlier examples.

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: combined-failover
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai-combo
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
  name: combo-mock-429
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: combined-failover
  match:
    model: mock-gpt-4o
  visibility: Internal
  provider: OpenAI
  baseURL: http://mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
    health:
      unhealthyCondition: "response.code >= 500 || response.code == 429"
      eviction:
        duration: 60s
        consecutiveFailures: 1
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: combo-mock-503
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: combined-failover
  match:
    model: mock-gpt-4o-500
  visibility: Internal
  provider: OpenAI
  baseURL: http://mock-gpt-4o-500-svc.agentgateway-system.svc.cluster.local:8000/v1
  policies:
    auth:
      passthrough: {}
    health:
      unhealthyCondition: "response.code >= 500 || response.code == 429"
      eviction:
        duration: 60s
        consecutiveFailures: 1
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: combo-openai-gpt-5-4-nano
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: combined-failover
  match:
    model: gpt-5.4-nano
  visibility: Internal
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: resilient-combo
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: combined-failover
  match:
    model: resilient
  virtualModel:
    failover:
      targets:
        - modelRef:
            name: combo-mock-429
          priority: 0
        - modelRef:
            name: combo-mock-503
          priority: 0
        - modelRef:
            name: combo-openai-gpt-5-4-nano
          priority: 1
EOF
```

Restart the proxy to clear any health state from previous tests:

```bash
kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

### Test the Combined Behavior

Send five requests in sequence:

```bash
for i in 1 2 3 4 5; do
  echo "=== Request $i ==="
  curl -s -w "\nHTTP_STATUS:%{http_code}\n" "$GATEWAY_IP:8080/openai-combo/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model":"resilient","messages":[{"role":"user","content":"Say hi in one word."}]}' \
    | jq -r 'if .model then "model=\(.model)" else "error.type=\(.error.type) error.code=\(.error.code)" end' 2>/dev/null
  sleep 1
done
```

**Expected response pattern:**

```
=== Request 1 ===
HTTP=503  error.type=InternalServerError  error.code=503
=== Request 2 ===
HTTP=429  error.type=RateLimitError       error.code=429
=== Request 3 ===
HTTP=200  model=gpt-5.4-nano-2026-03-17
=== Request 4 ===
HTTP=200  model=gpt-5.4-nano-2026-03-17
=== Request 5 ===
HTTP=200  model=gpt-5.4-nano-2026-03-17
```

The mock the gateway picks first (429 or 503) may vary, but the overall sequence will always be: one failure from the first picked mock, one failure from the second, then priority 1 takes over.

### Verify in the Access Logs

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --tail 50 \
  | grep "route=agentgateway-system/combined-failover"
```

You will see the endpoint change across the five requests:

```
endpoint=mock-gpt-4o-500-svc.agentgateway-system.svc.cluster.local:8000  http.status=503
endpoint=mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000      http.status=429
endpoint=api.openai.com:443                                              http.status=200
endpoint=api.openai.com:443                                              http.status=200
endpoint=api.openai.com:443                                              http.status=200
```

### What This Proves

| Behavior | Observed |
|---|---|
| **Load balancing within a priority** | Requests 1 and 2 hit *different* models at priority 0 (different endpoints, different error codes). The gateway scores models by health and latency rather than round-robin, but the practical effect is that both models receive traffic before either is fully written off. |
| **Per-model eviction** | Each mock is evicted independently on its first matching failure (`consecutiveFailures: 1`). The 503 and 429 mocks are separate `AgentgatewayModel` resources, each with its own health policy and health state. |
| **Failover gated on whole-priority eviction** | Priority 1 (`api.openai.com`) is not used until **both** models at priority 0 are evicted. Request 3 is the first request after the second mock is evicted, and it is the first to hit OpenAI. |
| **CEL `unhealthyCondition` covers both codes** | The expression `response.code >= 500 \|\| response.code == 429` classified the 503 from `combo-mock-503` and the 429 from `combo-mock-429` as unhealthy. |

---

## Cleanup

Delete the resources created in this lab. Skip any sections you did not run.

Pattern 2 resources:
```bash
kubectl delete agentgatewaymodel -n agentgateway-system resilient-5xx openai-gpt-5-4-nano-5xx mock-gpt-4o-500
kubectl delete httproute -n agentgateway-system mock-5xx-failover
kubectl delete -n agentgateway-system svc/mock-gpt-4o-500-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o-500
```

Pattern 3 resources:
```bash
kubectl delete agentgatewaymodel -n agentgateway-system resilient-combo combo-openai-gpt-5-4-nano combo-mock-503 combo-mock-429
kubectl delete httproute -n agentgateway-system combined-failover
```

Base Setup resources (skip if you want to keep them for the [LLM Failover](llm-failover.md) lab):
```bash
kubectl delete agentgatewaymodel -n agentgateway-system resilient openai-gpt-5-6-terra openai-gpt-5-4-nano mock-gpt-4o --ignore-not-found
kubectl delete httproute -n agentgateway-system mock-ratelimit-failover
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o
```

Restore the AgentGateway to the 2 replicas we originally set up:
```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --type=merge -p '{"spec":{"deployment":{"spec":{"replicas":2}}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system

kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

Confirm both replicas are back before moving on to another lab:
```bash
kubectl get deploy agentgateway-proxy -n agentgateway-system \
  -o jsonpath='replicas={.status.readyReplicas}{"\n"}'
```
