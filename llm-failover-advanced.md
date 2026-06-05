# Advanced LLM Failover Patterns

This lab walks through three advanced failover patterns on Enterprise AgentGateway: intra-priority-group failover, eviction on 5XX server errors, and the combined behavior that proves intra-group P2C load balancing, per-provider eviction, and inter-group failover work together.

If you have already completed the [LLM Failover](llm-failover.md) lab, you can skip the **Base Setup** section below — your resources are already in place.

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives

- Demonstrate intra-priority-group failover (load balancing across providers within a single group, with per-provider eviction)
- Trigger eviction on 5XX server errors via a separate mock and CEL expression
- Combine all behaviors in a single backend: intra-group P2C load balancing, per-provider eviction, and inter-group failover only when the entire current group is evicted

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

### Apply the Base Backend, Route, and Health Policy

Pattern 1 modifies the backend created here; subsequent patterns create their own. This block bootstraps the shared `mock-ratelimit-backend` + `mock-ratelimit-health` policy that Pattern 1 will mutate.

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
        - name: mock-ratelimit-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mock-ratelimit-backend
  namespace: agentgateway-system
spec:
  ai:
    groups:
      - providers:
          - name: mock-ratelimit-provider
            openai:
              model: "mock-gpt-4o"
            host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
            port: 8000
            path: "/v1/chat/completions"
      - providers:
          - name: openai-provider
            openai:
              model: "gpt-4o-mini"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-ratelimit-auth-mock
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-ratelimit-backend
    sectionName: mock-ratelimit-provider
  backend:
    auth:
      passthrough: {}
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-ratelimit-auth-openai
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-ratelimit-backend
    sectionName: openai-provider
  backend:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-ratelimit-health
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-ratelimit-backend
  backend:
    health:
      unhealthyCondition: "response.code >= 500 || response.code == 429"
      eviction:
        duration: 60s
        consecutiveFailures: 1
EOF
```

---

## Pattern 1: Intra-Priority-Group Failover

The simplest failover case is between priority groups (group 1 to group 2 when group 1 fails). This pattern tests a more nuanced scenario: **failover between multiple backends within the same priority group**.

### Why This Matters

When you have multiple backends in a single priority group:
- The gateway load balances between all healthy backends
- When one backend fails, it's ejected from the pool
- Traffic continues to healthy backends in the same priority group
- The gateway only moves to a lower priority group when ALL backends in the current group are unhealthy

This enables:
- **Heterogeneous backends**: Mix different provider types or model tiers in the same priority group
- **Partial failure handling**: Keep serving traffic even when some backends fail
- **Quality preservation**: Stay in the preferred tier with better models as long as ANY backend is healthy
- **Graceful degradation**: Fallback to lower-quality but functional models only when necessary

### Health Policy

The `EnterpriseAgentgatewayPolicy` (`mock-ratelimit-health`) created in the Base Setup already targets `mock-ratelimit-backend` by name, so it continues to apply here. No additional policy configuration is needed — the same `unhealthyCondition` and `eviction` settings govern this scenario.

### Test Scenario

This pattern demonstrates a real-world graceful degradation setup:

**Priority Group 1 (Preferred):**
1. Mock server (fails with 429)
2. OpenAI gpt-4o (healthy, more capable model)

**Priority Group 2 (Degraded Mode):**
1. OpenAI gpt-4o-mini (less capable but faster/cheaper fallback)

The key insight: Priority Group 2 uses a less proficient model (gpt-4o-mini) to ensure users get *some* response even if it's lower quality. However, since Priority Group 1 has a healthy backend (gpt-4o), Priority Group 2 should NOT be reached in this test.

### Update Configuration

First, restart the AgentGateway to clear any existing health state from previous tests:

```bash
kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

Now update the backend configuration to have multiple providers in Priority Group 1:

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
        - name: mock-ratelimit-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mock-ratelimit-backend
  namespace: agentgateway-system
spec:
  ai:
    groups:
      # Priority Group 1: Mock (fails) + OpenAI (healthy)
      - providers:
          # Mock Server (always returns rate limit errors)
          - name: mock-ratelimit-provider
            openai:
              model: "mock-gpt-4o"
            host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
            port: 8000
            path: "/v1/chat/completions"
          # OpenAI (should failover to this within the same priority group)
          - name: openai-provider-primary
            openai:
              model: "gpt-4o"
      # Priority Group 2: OpenAI fallback (should NOT be reached)
      - providers:
          - name: openai-provider-fallback
            openai:
              model: "gpt-4o-mini"
---
# Re-apply the per-provider auth policies for the new provider names. The
# passthrough EAGP from Base Setup still matches; the OpenAI EAGP now needs
# to target both openai-provider-primary and openai-provider-fallback.
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-ratelimit-auth-mock
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-ratelimit-backend
    sectionName: mock-ratelimit-provider
  backend:
    auth:
      passthrough: {}
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-ratelimit-auth-openai
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-ratelimit-backend
    sectionName: openai-provider-primary
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-ratelimit-backend
    sectionName: openai-provider-fallback
  backend:
    auth:
      secretRef:
        name: openai-secret
EOF
```

**Key differences from the Base Setup backend:**
- Priority Group 1 now has TWO providers: mock-ratelimit-provider and openai-provider-primary (gpt-4o)
- Priority Group 2 uses a less capable model (gpt-4o-mini) as a "degraded mode" fallback
- This demonstrates graceful degradation: prefer the more capable model, but ensure users get *some* response (even if lower quality) if all preferred backends fail
- In this test, Priority Group 2 should never be reached because Priority Group 1 has a healthy gpt-4o backend

### Test Intra-Priority-Group Failover

Send multiple requests to observe the failover pattern within Priority Group 1:

```bash
curl -s -w "\nHTTP Status: %{http_code}\n" \
  "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -d '{"model": "", "messages": [{"role": "user", "content": "What is 2+2?"}]}'
```

**Expected output pattern:**

Request 1:
```json
{"error":{"message":"Rate limit reached for mock-gpt-4o...","type":"RateLimitError","code":429}}
HTTP Status: 429
```

Request 2:
```json
{"model":"gpt-4o-2024-08-06","choices":[{"message":{"content":"Four.",...}],...}
HTTP Status: 200
```

Request 3:
```json
{"model":"gpt-4o-2024-08-06","choices":[{"message":{"content":"Four.",...}],...}
HTTP Status: 200
```

### Verify Failover in Logs

Check the AgentGateway logs to confirm which backends handled each request:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

**Expected log output:**

```json
{
  "status": 429,
  "endpoint": "mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000",
  "duration": "2ms"
}
{
  "status": 200,
  "endpoint": "api.openai.com:443",
  "duration": "861ms"
}
{
  "status": 200,
  "endpoint": "api.openai.com:443",
  "duration": "491ms"
}
```

**Key observations:**
- Request 1: Routed to `mock-gpt-4o-svc` → returned 429 → backend ejected
- Requests 2-3: Routed to `api.openai.com` (Priority Group 1's healthy gpt-4o backend) → returned 200
- The endpoint does NOT switch back to the mock server
- Priority Group 2 (with gpt-4o-mini) is never reached because Priority Group 1 has a healthy backend
- Users get responses from the more capable gpt-4o model, not the degraded gpt-4o-mini fallback

### What This Proves

This pattern demonstrates that:
1. **Intra-pool failover**: Failover works correctly between backends within the same priority group
2. **Backend ejection**: Unhealthy backends are ejected and not used in subsequent requests
3. **Priority group preference**: The gateway stays within the current priority group as long as ANY backend is healthy
4. **Graceful degradation**: Lower priority groups with less capable models (gpt-4o-mini) are only used when ALL backends in higher priority groups fail
5. **Quality preservation**: Users get responses from the more capable model (gpt-4o) when available, ensuring the best possible user experience

This pattern is crucial for building resilient AI gateway architectures that balance quality, cost, and availability. You can configure preferred high-quality models in Priority Group 1, while ensuring users still get *some* response (even if lower quality) from Priority Group 2 when the preferred tier is completely unavailable.

---

## Pattern 2: 5XX Server Error Failover

Pattern 1 demonstrated failover triggered by 429 rate limit errors. This pattern tests failover triggered by **5XX server errors**, showing that the `EnterpriseAgentgatewayPolicy` health policy can handle any error condition defined by the CEL expression.

### Why This Matters

LLM providers can fail with more than just rate limits. Server errors (500, 502, 503) indicate the backend is experiencing issues and should be temporarily removed from the pool. With the `EnterpriseAgentgatewayPolicy`, you can define exactly which error codes trigger eviction using CEL expressions, giving you fine-grained control over failover behavior.

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

Configure a separate HTTPRoute, EnterpriseAgentgatewayBackend, and EnterpriseAgentgatewayPolicy for the 5XX failover scenario. Note that this policy uses `unhealthyCondition: "response.code >= 500"` to evict only on server errors:

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
        - name: mock-5xx-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mock-5xx-backend
  namespace: agentgateway-system
spec:
  ai:
    groups:
      # Priority Group 1: Mock Server (always returns 500 errors)
      - providers:
          - name: mock-5xx-provider
            openai:
              model: "mock-gpt-4o-500"
            host: mock-gpt-4o-500-svc.agentgateway-system.svc.cluster.local
            port: 8000
            path: "/v1/chat/completions"
      # Priority Group 2: OpenAI (failover when group 1 is evicted)
      - providers:
          - name: openai-provider
            openai:
              model: "gpt-4o-mini"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-5xx-auth-mock
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-5xx-backend
    sectionName: mock-5xx-provider
  backend:
    auth:
      passthrough: {}
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-5xx-auth-openai
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-5xx-backend
    sectionName: openai-provider
  backend:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mock-5xx-health
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: mock-5xx-backend
  backend:
    health:
      unhealthyCondition: "response.code >= 500"
      eviction:
        duration: 30s
        consecutiveFailures: 1
EOF
```

**Key differences from the 429 example:**
- The `unhealthyCondition` uses `"response.code >= 500"` — only server errors trigger eviction, not rate limits
- The policy's `eviction.duration: 30s` controls how long the backend is removed from the pool
- The route uses `/openai-5xx` so both examples can coexist during testing

### Test 5XX Failover

Restart the AgentGateway to clear any existing health state:

```bash
kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

Send requests to observe the 5XX failover pattern:

```bash
curl -s -w "\nHTTP Status: %{http_code}\n" \
  "$GATEWAY_IP:8080/openai-5xx" \
  -H "Content-Type: application/json" \
  -d '{"model": "", "messages": [{"role": "user", "content": "What is 2+2?"}]}'
```

**Expected response pattern:**
- **Request 1**: `503 Service Unavailable` from mock-gpt-4o-500
- **Request 2**: `200 OK` from OpenAI (failover successful)
- **Request 3**: `200 OK` from OpenAI (continues using failover)

**What's happening:**
1. The first request hits the mock server and receives a 503 error
2. The `EnterpriseAgentgatewayPolicy` evaluates `response.code >= 500` → `true` (503 >= 500), and since `consecutiveFailures: 1`, the backend is evicted immediately for 30 seconds
3. Subsequent requests are routed to priority group 2 (OpenAI) and receive successful 200 responses
4. After 30 seconds, the gateway will retry the mock server to check if it has recovered
5. Since the mock server always returns 503, the cycle repeats with the eviction duration increasing via multiplicative backoff

### Verify 5XX Failover in Logs

Check the AgentGateway logs to confirm the failover behavior:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

**Expected log output:**

```json
{
  "status": 503,
  "endpoint": "mock-gpt-4o-500-svc.agentgateway-system.svc.cluster.local:8000",
  "duration": "2ms"
}
{
  "status": 200,
  "endpoint": "api.openai.com:443",
  "duration": "861ms"
}
```

**Key observations:**
- Request 1: Routed to `mock-gpt-4o-500-svc` → returned 503 → backend evicted
- Request 2+: Routed to `api.openai.com` → returned 200 (failover successful)
- The `unhealthyCondition` CEL expression correctly identified the 503 response as unhealthy (`503 >= 500`) and triggered eviction

### What This Proves

This pattern demonstrates that:
1. **Non-429 failover**: The `EnterpriseAgentgatewayPolicy` health policy enables failover for any error condition, not just rate limits
2. **CEL flexibility**: The `unhealthyCondition` expression can target specific error ranges (`>= 500`), individual codes (`== 429`), or combinations (`>= 500 || == 429`)
3. **Policy-controlled duration**: The policy's `eviction.duration` controls how long the backend is removed from the pool
4. **Multiplicative backoff**: Repeated evictions increase the eviction duration automatically, preventing rapid cycling on persistently failing backends

---

## Pattern 3: Combined LB + Per-Provider Eviction + Inter-Group Failover

The previous patterns each demonstrated one piece of the failover model. This pattern combines all three behaviors in a single backend to address a common real-world requirement: running multiple providers in the same priority group, where the platform must load balance across them, evict individual providers that return 429 or 5xx responses, and fail over to a lower-priority group **only** once the current group has no usable providers left.

The piece this pattern clarifies is *where health tracking lives*. Eviction is tracked **per provider** — each entry in `groups[].providers[]` is an individually evictable backend. The decision to move to the next priority group is derived from that per-provider state: it triggers only when every provider in the current group has been evicted. In other words, intra-group P2C load balancing, per-provider eviction, and inter-group failover are not three separate features — they are the same mechanism observed at different scopes.

This pattern proves the behavior end-to-end on the cluster by combining the 429 mock and the 503 mock in a single priority group, with OpenAI as the lower-priority fallback.

### Prerequisites

This pattern reuses resources from earlier in this lab:
- `mock-gpt-4o` deployment and `openai-secret` from the **Base Setup** section
- `mock-gpt-4o-500` deployment from **Pattern 2** above

If you have not, complete those sections before continuing.

### Apply the Combined Configuration

Create a new HTTPRoute, EnterpriseAgentgatewayBackend, and EnterpriseAgentgatewayPolicy that puts both mocks in Priority Group 1 and OpenAI in Priority Group 2. The route uses `/openai-combo` so it coexists with the earlier examples.

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
        - name: combined-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: combined-backend
  namespace: agentgateway-system
spec:
  ai:
    groups:
      # Priority Group 1: two providers (mock-429, mock-503)
      # P2C load balances between them; each is evicted independently
      - providers:
          - name: mock-429
            openai:
              model: "mock-gpt-4o"
            host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
            port: 8000
            path: "/v1/chat/completions"
          - name: mock-503
            openai:
              model: "mock-gpt-4o-500"
            host: mock-gpt-4o-500-svc.agentgateway-system.svc.cluster.local
            port: 8000
            path: "/v1/chat/completions"
      # Priority Group 2: healthy OpenAI fallback
      # Reached only after BOTH providers in group 1 are evicted
      - providers:
          - name: openai-fallback
            openai:
              model: "gpt-4o-mini"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: combined-auth-mocks
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: combined-backend
    sectionName: mock-429
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: combined-backend
    sectionName: mock-503
  backend:
    auth:
      passthrough: {}
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: combined-auth-openai
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: combined-backend
    sectionName: openai-fallback
  backend:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: combined-health
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayBackend
    name: combined-backend
  backend:
    health:
      unhealthyCondition: "response.code >= 500 || response.code == 429"
      eviction:
        duration: 60s
        consecutiveFailures: 1
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
  curl -s -w "\nHTTP_STATUS:%{http_code}\n" "$GATEWAY_IP:8080/openai-combo" \
    -H "Content-Type: application/json" \
    -d '{"model":"","messages":[{"role":"user","content":"Say hi in one word."}]}' \
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
HTTP=200  model=gpt-4o-mini-2024-07-18
=== Request 4 ===
HTTP=200  model=gpt-4o-mini-2024-07-18
=== Request 5 ===
HTTP=200  model=gpt-4o-mini-2024-07-18
```

The exact provider P2C picks first (mock-429 or mock-503) may vary, but the overall sequence will always be: one failure from the first picked provider, one failure from the second, then group 2 takes over.

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
| **Intra-group P2C load balancing** | Requests 1 and 2 hit *different* providers in group 1 (different endpoints, different error codes). P2C, not round-robin, but the practical effect is that both providers receive traffic before either is fully written off. |
| **Per-provider eviction** | Each provider is evicted independently on its first matching failure (`consecutiveFailures: 1`). The 503 and 429 mocks are tracked as separate backends — the policy applied to the backend `combined-backend` evaluates eviction per provider, not per group. |
| **Inter-group failover gated on whole-group eviction** | Group 2 (`api.openai.com`) is not used until **both** providers in group 1 are evicted. Request 3 is the first request after the second mock is evicted, and it is the first to hit OpenAI. |
| **CEL `unhealthyCondition` covers both codes** | A single policy with `response.code >= 500 \|\| response.code == 429` correctly classified the 503 from mock-503 and the 429 from mock-429 as unhealthy. |

---

## Cleanup

Delete the resources created in this lab. Skip any sections you did not run.

Pattern 2 resources:
```bash
kubectl delete httproute -n agentgateway-system mock-5xx-failover
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mock-5xx-backend
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mock-5xx-health mock-5xx-auth-mock mock-5xx-auth-openai
kubectl delete -n agentgateway-system svc/mock-gpt-4o-500-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o-500
```

Pattern 3 resources:
```bash
kubectl delete httproute -n agentgateway-system combined-failover
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system combined-backend
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system combined-health combined-auth-mocks combined-auth-openai
```

Base Setup resources (skip if you want to keep them for the [LLM Failover](llm-failover.md) lab):
```bash
kubectl delete httproute -n agentgateway-system mock-ratelimit-failover
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mock-ratelimit-backend
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mock-ratelimit-health mock-ratelimit-auth-mock mock-ratelimit-auth-openai
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o
```

Restore the AgentGateway to the 2 replicas we originally set up:
```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --type=merge -p '{"spec":{"deployment":{"spec":{"replicas":2}}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
```
