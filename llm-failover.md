# Configure LLM Failover

In this lab, you'll configure priority group failover using an `AgentgatewayPolicy` health policy. You'll deploy a mock openai server configured to return rate limit errors (priority group 1) and use OpenAI as the failover (priority group 2). When the health policy detects unhealthy responses, the backend is evicted from its priority group, causing subsequent requests to route to the next available group

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy a mock openai server configured to always return 429 rate limit errors
- Configure an `AgentgatewayPolicy` with `unhealthyCondition` CEL expression and `eviction` settings to define what triggers failover
- Configure an `AgentgatewayBackend` with priority groups
- Create priority group failover configuration with mock-gpt-4o as priority 1 and OpenAI as priority 2
- Test failover from rate-limited backend to healthy OpenAI backend
- Test failover from 5XX server errors using a separate mock server
- Observe failover behavior in logs and traces

## Deploy Mock Server with Rate Limiting

Deploy the vllm-sim mock server configured to always return 429 rate limit errors. This uses the same deployment structure as the [Mock OpenAI Server lab](configure-mock-openai-server.md), but adds failure injection flags:

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

Verify the deployment:
```bash
kubectl get pods -n agentgateway-system
kubectl get svc -n agentgateway-system
```

You should see the mock-gpt-4o pod running and its service available on port 8000.

**Note:** The mock server is configured with `--failure-injection-rate 100` and `--failure-types rate_limit`, which means it will always return 429 rate limit errors for every request.

## Configure OpenAI Secret

Create a Kubernetes secret with your OpenAI API key for the failover backend:

```bash
export OPENAI_API_KEY=$OPENAI_API_KEY

kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

## Create Priority Group Failover Configuration

Configure the AgentgatewayBackend with priority groups, HTTPRoute, and an AgentgatewayPolicy health policy. The `AgentgatewayPolicy` defines what constitutes an unhealthy response using a CEL expression and how long to evict the backend. Without a health policy, backends are never evicted and failover will not trigger:

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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mock-ratelimit-backend
  namespace: agentgateway-system
spec:
  ai:
    groups:
      # Priority Group 1: Mock Server (always returns rate limit errors)
      - providers:
          - name: mock-ratelimit-provider
            openai:
              model: "mock-gpt-4o"
            host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
            port: 8000
            path: "/v1/chat/completions"
            policies:
              auth:
                passthrough: {}
      # Priority Group 2: OpenAI (failover when group 1 is evicted)
      - providers:
          - name: openai-provider
            openai:
              model: "gpt-4o-mini"
            policies:
              auth:
                secretRef:
                  name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: mock-ratelimit-health
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: agentgateway.dev
    kind: AgentgatewayBackend
    name: mock-ratelimit-backend
  backend:
    health:
      unhealthyCondition: "response.code >= 500 || response.code == 429"
      eviction:
        duration: 60s
        consecutiveFailures: 1
EOF
```

**Key Configuration Points:**
- The `AgentgatewayPolicy` is what enables failover. Without it, backends are never evicted regardless of error codes
- `unhealthyCondition: "response.code >= 500 || response.code == 429"` is a CEL expression that classifies both 5XX server errors and 429 rate limit errors as unhealthy
- `eviction.duration: 60s` sets how long an evicted backend is removed from the pool. `consecutiveFailures: 1` means a single unhealthy response triggers eviction immediately
- Priority group 1 uses the mock server that always returns 429 errors
- Priority group 2 uses OpenAI as the failover backend

## Configure Single Replica for Consistent Testing

For this lab, it's important to use a single AgentGateway replica because **provider health state is local to each pod**. With multiple replicas, different pods maintain separate health states, which can lead to inconsistent failover behavior where some requests hit a pod that hasn't marked the provider as unhealthy yet.

Update the EnterpriseAgentgatewayParameters to set replicas to 1:

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --type=merge -p '{"spec":{"deployment":{"spec":{"replicas":1}}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
```

Wait for the deployment to roll out:

```bash
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

Verify only one pod is running:

```bash
kubectl get pods -n agentgateway-system
```

You should see only one agentgateway pod in Running state.

## Test Priority Group Failover

Now test the failover behavior. Priority group failover works across requests rather than within a single request.

Get the Gateway IP address:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### Testing Failover Behavior

Send multiple requests to observe the failover pattern:

```bash
curl -v "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }'
```

Note that the default response from the mock openai server will always be
```
{"error":{"message":"Rate limit reached for mock-gpt-4o in organization org-xxx on requests per min (RPM): Limit 3, Used 3, Requested 1.","type":"RateLimitError","param":null,"code":429}}
```

**Expected response pattern (with single replica):**
- **Request 1**: `429 Too Many Requests` from mock-gpt-4o
- **Request 2**: `200 OK` from OpenAI (failover successful)
- **Request 3**: `200 OK` from OpenAI (continues using failover)

**What's happening:**
1. The first request hits the mock server and receives a 429 error
2. The `AgentgatewayPolicy` evaluates `response.code == 429` → `true`, and since `consecutiveFailures: 1`, the backend is evicted for 60 seconds (`eviction.duration`)
3. All subsequent requests are routed to priority group 2 (OpenAI) and receive successful 200 responses
4. After 60 seconds, the gateway will retry the mock server to check if it has recovered
5. Since the mock server always returns 429, the cycle repeats with the eviction duration increasing via multiplicative backoff

## Observability

### View Metrics in Grafana

Use the AgentGateway Grafana dashboard to observe aggregated metrics:

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

The Grafana dashboard provides aggregated metrics including:
- HTTP status code distribution (429 vs 200 responses)
- Request rates over time
- Error rates and percentages
- Token usage by model
- Request duration percentiles

### View Access Logs

Check AgentGateway logs to see the failover behavior:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

In the access logs (entries with `"scope": "request"`), you can observe:

**First Request (429 error):**
- `"endpoint": "mock-gpt-4o-svc.agentgateway-system.svc.cluster.local:8000"`
- `"http.status": 429`
- `"duration": "~50ms"` (fast since mock server just returns an error)
- `"response.body"` showing the rate limit error message

**Second Request (successful failover):**
- `"endpoint": "api.openai.com:443"`
- `"http.status": 200`
- `"duration": "1372ms"` (longer due to actual LLM processing)
- `"gen_ai.response.model"`, `"gen_ai.usage.input_tokens"`, `"gen_ai.usage.output_tokens"` showing successful OpenAI response
- `"response.body"` containing the actual LLM completion

The change in `endpoint` field between requests clearly shows the failover from the mock server to OpenAI.

### View Traces in Grafana

To view distributed traces and see failover behavior across requests:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Compare traces from your two requests:
   - **First trace**: Shows attempt to mock-gpt-4o with 429 error span
   - **Second trace**: Shows successful routing to OpenAI backend with 200 response
5. You can see how the backend selection changes based on provider health

## Understanding Priority Group Failover

The priority group failover configuration demonstrates several key concepts:

### How Priority Groups Work

1. **Priority Ordering**: The gateway prefers providers in higher priority groups (group 1 over group 2, etc.)
2. **Health Policy (AgentgatewayPolicy)**: An `AgentgatewayPolicy` with `backend.health` defines what constitutes an unhealthy response and how eviction works. **Without a health policy, backends are never evicted and failover will not trigger.**
   - `unhealthyCondition` is a CEL expression evaluated against each response. If it returns `true`, the response counts toward eviction
   - `eviction.consecutiveFailures` sets how many consecutive unhealthy responses are required before eviction (use `1` for immediate eviction, `3` to tolerate transient errors)
   - `eviction.duration` sets the base removal time from the priority group. Duration increases with multiplicative backoff on repeated evictions
3. **Health-Based Eviction**: When a provider's responses match the `unhealthyCondition` and thresholds are met, it's evicted from the pool
   - The **first request** that encounters an error will fail with that error code
   - The provider is evicted after processing the error response
   - **Subsequent requests** will skip evicted providers and use the next priority group
   - This is an across-request mechanism, not a within-request retry
   - **Important**: Health state is local to each AgentGateway pod. With multiple replicas, you may see 1-2 failed requests before failover as different pods learn about the unhealthy state
4. **Eviction Duration**: The `eviction.duration` in the `AgentgatewayPolicy` controls how long a backend is removed from the pool. After the period expires, the gateway will retry the primary backend to check if it has recovered. Duration increases with multiplicative backoff on repeated evictions, preventing rapid cycling on persistently failing backends
5. **Across-Request Failover**: Unlike retry policies that work within a single request, priority group failover works across multiple requests based on provider health state
   - With a single replica: expect 1 failed request, then failover to the next priority group
   - With multiple replicas: expect 1-2 failed requests before all pods mark the provider as unhealthy
   - Once a provider is evicted, all subsequent requests use the failover backend until the eviction period expires
6. **CEL Expression Examples**: The `unhealthyCondition` field supports flexible CEL expressions:
   - `"response.code >= 500 || response.code == 429"` — evict on server errors and rate limits
   - `"response.code >= 500"` — evict on server errors only
   - `"response.code >= 400"` — evict on any client or server error
   - `"true"` — evict on every response (testing only)
7. **Production Use Case**: This pattern is ideal for scenarios where you have:
   - Primary backends that may experience temporary rate limiting or server errors
   - Fallback backends as safety nets for subsequent requests
   - Different cost tiers (prefer cheaper model, fall back to more expensive when primary is unavailable)
   - Circuit-breaking behavior without explicit circuit breaker configuration

## Extended Example: Intra-Priority-Group Failover

The previous example showed failover between priority groups (group 1 to group 2). Now let's test a more advanced scenario: **failover between multiple backends within the same priority group**.

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

The `AgentgatewayPolicy` (`mock-ratelimit-health`) created in the basic example already targets `mock-ratelimit-backend` by name, so it continues to apply here. No additional policy configuration is needed — the same `unhealthyCondition` and `eviction` settings govern this extended scenario.

### Test Scenario

This example demonstrates a real-world graceful degradation pattern:

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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
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
            policies:
              auth:
                passthrough: {}
          # OpenAI (should failover to this within the same priority group)
          - name: openai-provider-primary
            openai:
              model: "gpt-4o"
            policies:
              auth:
                secretRef:
                  name: openai-secret
      # Priority Group 2: OpenAI fallback (should NOT be reached)
      - providers:
          - name: openai-provider-fallback
            openai:
              model: "gpt-4o-mini"
            policies:
              auth:
                secretRef:
                  name: openai-secret
EOF
```

**Key differences from the basic example:**
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

This extended example demonstrates that:
1. **Intra-pool failover**: Failover works correctly between backends within the same priority group
2. **Backend ejection**: Unhealthy backends are ejected and not used in subsequent requests
3. **Priority group preference**: The gateway stays within the current priority group as long as ANY backend is healthy
4. **Graceful degradation**: Lower priority groups with less capable models (gpt-4o-mini) are only used when ALL backends in higher priority groups fail
5. **Quality preservation**: Users get responses from the more capable model (gpt-4o) when available, ensuring the best possible user experience

This pattern is crucial for building resilient AI gateway architectures that balance quality, cost, and availability. You can configure preferred high-quality models in Priority Group 1, while ensuring users still get *some* response (even if lower quality) from Priority Group 2 when the preferred tier is completely unavailable.

## Extended Example: 5XX Server Error Failover

The previous examples demonstrated failover triggered by 429 rate limit errors. Now let's test failover triggered by **5XX server errors**, showing that the `AgentgatewayPolicy` health policy can handle any error condition defined by the CEL expression.

### Why This Matters

LLM providers can fail with more than just rate limits. Server errors (500, 502, 503) indicate the backend is experiencing issues and should be temporarily removed from the pool. With the `AgentgatewayPolicy`, you can define exactly which error codes trigger eviction using CEL expressions, giving you fine-grained control over failover behavior.

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

Configure a separate HTTPRoute, AgentgatewayBackend, and AgentgatewayPolicy for the 5XX failover scenario. Note that this policy uses `unhealthyCondition: "response.code >= 500"` to evict only on server errors:

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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
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
            policies:
              auth:
                passthrough: {}
      # Priority Group 2: OpenAI (failover when group 1 is evicted)
      - providers:
          - name: openai-provider
            openai:
              model: "gpt-4o-mini"
            policies:
              auth:
                secretRef:
                  name: openai-secret
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: mock-5xx-health
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: agentgateway.dev
    kind: AgentgatewayBackend
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
2. The `AgentgatewayPolicy` evaluates `response.code >= 500` → `true` (503 >= 500), and since `consecutiveFailures: 1`, the backend is evicted immediately for 30 seconds
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

This example demonstrates that:
1. **Non-429 failover**: The `AgentgatewayPolicy` health policy enables failover for any error condition, not just rate limits
2. **CEL flexibility**: The `unhealthyCondition` expression can target specific error ranges (`>= 500`), individual codes (`== 429`), or combinations (`>= 500 || == 429`)
3. **Policy-controlled duration**: The policy's `eviction.duration` controls how long the backend is removed from the pool
4. **Multiplicative backoff**: Repeated evictions increase the eviction duration automatically, preventing rapid cycling on persistently failing backends

## Cleanup

Delete the lab resources:
```bash
kubectl delete httproute -n agentgateway-system mock-ratelimit-failover
kubectl delete agentgatewaybackend -n agentgateway-system mock-ratelimit-backend
kubectl delete agentgatewayPolicy -n agentgateway-system mock-ratelimit-health
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o
```

If you completed the 5XX Server Error Failover example, also clean up those resources:
```bash
kubectl delete httproute -n agentgateway-system mock-5xx-failover
kubectl delete agentgatewaybackend -n agentgateway-system mock-5xx-backend
kubectl delete agentgatewayPolicy -n agentgateway-system mock-5xx-health
kubectl delete -n agentgateway-system svc/mock-gpt-4o-500-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o-500
```

Restore the AgentGateway to the 2 replicas we originally set up:
```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --type=merge -p '{"spec":{"deployment":{"spec":{"replicas":2}}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
```
