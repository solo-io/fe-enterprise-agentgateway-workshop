# Configure LLM Failover

In this lab, you'll configure priority group failover using the mock openai server from earlier labs that has been configured to return rate limit errors (priority group 1) and OpenAI (priority group 2). When the primary backend returns rate limit errors, it's marked as unhealthy, causing subsequent requests to route to the secondary priority group

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Deploy a mock openai server configured to always return 429 rate limit errors
- Configure HTTPRoute with `ResponseHeaderModifier` to add `Retry-After` header (Agentgateway expects this header to trigger failover)
- Configure an `AgentgatewayBackend` with priority groups
- Create priority group failover configuration with mock-gpt-4o as priority 1 and OpenAI as priority 2
- Test failover from rate-limited backend to healthy OpenAI backend
- Observe failover behavior in logs and traces

## Deploy Mock Server with Rate Limiting

Deploy the vllm-sim mock server configured to always return 429 rate limit errors. This uses the same deployment structure as lab 003, but adds failure injection flags:

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
kubectl get pods -n agentgateway-system | grep mock-gpt-4o
kubectl get svc -n agentgateway-system | grep mock-gpt-4o-svc
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

Configure the AgentgatewayBackend with priority groups and HTTPRoute. The HTTPRoute includes a `ResponseHeaderModifier` filter to add the `Retry-After` header that the gateway needs to determine how long to mark the provider as unhealthy:

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
      filters:
        - type: ResponseHeaderModifier
          responseHeaderModifier:
            add:
              - name: Retry-After
                value: "60"
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
      # Priority Group 2: OpenAI (failover when group 1 returns 429)
      - providers:
          - name: openai-provider
            openai:
              model: "gpt-4o-mini"
            policies:
              auth:
                secretRef:
                  name: openai-secret
EOF
```

**Key Configuration Points:**
- The `ResponseHeaderModifier` filter adds `Retry-After: 60` to all responses to mimic a typical 429 response from LLM Providers, which tells the gateway to mark the provider as unhealthy for 60 seconds when it receives a 429
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
kubectl get pods -n agentgateway-system | grep "^agentgateway-"
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
1. The first request hits the mock server and receives a 429 error with `Retry-After: 60` header
2. The gateway marks the mock-gpt-4o provider as unhealthy for 60 seconds
3. All subsequent requests are routed to priority group 2 (OpenAI) and receive successful 200 responses
4. After 60 seconds, the gateway will retry the mock server to check if it has recovered
5. Since the mock server always returns 429, the cycle repeats with the gateway periodically checking if the primary backend has recovered

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
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 50 | jq .
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
   - **First trace**: Shows attempt to mock-gpt-4o with 429 error span and Retry-After header
   - **Second trace**: Shows successful routing to OpenAI backend with 200 response
5. You can see how the backend selection changes based on provider health

## Understanding Priority Group Failover

The priority group failover configuration demonstrates several key concepts:

### How Priority Groups Work

1. **Priority Ordering**: The gateway prefers providers in higher priority groups (group 1 over group 2, etc.)
2. **Health-Based Failover**: When a provider returns certain error codes (like 429), it's marked as unhealthy
   - The **first request** that encounters an error will fail with that error code
   - The provider is marked as unhealthy after processing the error response
   - **Subsequent requests** will skip unhealthy providers and use the next priority group
   - This is an across-request mechanism, not a within-request retry
   - **Important**: Health state is local to each AgentGateway pod. With multiple replicas, you may see 1-2 failed requests before failover as different pods learn about the unhealthy state
3. **Retry-After Header**: The `Retry-After` header tells the gateway how long to mark a provider as unhealthy
   - In this lab, we use an HTTPRoute `ResponseHeaderModifier` to add this header
   - The gateway honors this value and won't retry the unhealthy provider until the period expires
   - After the period expires, the gateway will retry the primary backend to check if it has recovered
4. **Across-Request Failover**: Unlike retry policies that work within a single request, priority group failover works across multiple requests based on provider health state
   - With a single replica: expect 1 failed request, then failover to the next priority group
   - With multiple replicas: expect 1-2 failed requests before all pods mark the provider as unhealthy
   - Once a provider is marked unhealthy, all subsequent requests use the failover backend until the unhealthy period expires
5. **Production Use Case**: This pattern is ideal for scenarios where you have:
   - Primary backends that may experience temporary rate limiting
   - Fallback backends as safety nets for subsequent requests
   - Different cost tiers (prefer cheaper model, fall back to more expensive when primary is unavailable)
   - Circuit-breaking behavior without explicit circuit breaker configuration

### Using ResponseHeaderModifier

The HTTPRoute's `ResponseHeaderModifier` filter is a powerful Gateway API feature that allows you to:
- Add headers to responses from backends
- Modify or remove existing headers
- Implement cross-cutting concerns at the routing layer

In this lab, we use it to add the `Retry-After` header that the mock server doesn't include by default, demonstrating how you can adapt third-party backends to work with your gateway's requirements without modifying the backend itself.

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
      filters:
        - type: ResponseHeaderModifier
          responseHeaderModifier:
            add:
              - name: Retry-After
                value: "60"
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
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 10 | \
  jq 'select(.scope == "request") | {status: ."http.status", endpoint: .endpoint, duration: .duration}'
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

## Cleanup

Delete the lab resources:
```bash
kubectl delete httproute -n agentgateway-system mock-ratelimit-failover
kubectl delete agentgatewaybackend -n agentgateway-system mock-ratelimit-backend
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o
```

Restore the AgentGateway to the 2 replicas we originally set up:
```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --type=merge -p '{"spec":{"deployment":{"spec":{"replicas":2}}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
```
