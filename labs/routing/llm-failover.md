# Configure LLM Failover

In this lab, you'll configure priority failover with a virtual `AgentgatewayModel`. You'll deploy a mock openai server configured to return rate limit errors (priority 0) and use OpenAI as the failover (priority 1). When the mock model's health policy detects unhealthy responses, the mock is evicted, causing subsequent requests to route to the next priority

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Deploy a mock openai server configured to always return 429 rate limit errors
- Configure a model health policy with an `unhealthyCondition` CEL expression and `eviction` settings to define what triggers failover
- Configure a virtual `AgentgatewayModel` with `virtualModel.failover` targets
- Create priority failover configuration with mock-gpt-4o as priority 0 and OpenAI as priority 1
- Test failover from rate-limited backend to healthy OpenAI backend
- Observe failover behavior in logs and traces

For more advanced patterns (intra-group load balancing, 5XX eviction, and combined LB + per-provider eviction + inter-group failover), see [Advanced LLM Failover Patterns](llm-failover-advanced.md).

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

Configure the HTTPRoute and three models. The two concrete models, `mock-gpt-4o` and `openai-gpt-5-4-nano`, are `Internal`, so clients reach them only through the virtual model `resilient`, which lists them as failover targets. The health policy lives on the mock model. Without it, the mock is never evicted and failover will not trigger:

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
# Priority 0: Mock Server (always returns rate limit errors)
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
# Priority 1: OpenAI (failover when the mock is evicted)
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
# The model clients request
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

**Key Configuration Points:**
- The `policies.health` block on the mock model is what enables failover. Without it, the mock is never evicted regardless of error codes
- `unhealthyCondition: "response.code >= 500 || response.code == 429"` is a CEL expression that classifies both 5XX server errors and 429 rate limit errors as unhealthy
- `eviction.duration: 60s` sets how long an evicted model is removed from the pool. `consecutiveFailures: 1` means a single unhealthy response triggers eviction immediately
- Priority 0 is the mock server that always returns 429 errors. Lower priority values are preferred
- Priority 1 is OpenAI, the failover target
- Each concrete model carries its own credentials: `passthrough` for the mock and the `openai-secret` key for OpenAI

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

Send three requests for the `resilient` model to observe the failover pattern:

```bash
for i in 1 2 3; do
  echo "--- Request $i ---"
  curl -s -w "\nHTTP %{http_code}\n" "$GATEWAY_IP:8080/openai/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "resilient",
      "messages": [{"role": "user", "content": "What is 2+2?"}]
    }'
done
```

Note that the default response from the mock openai server will always be
```
{"error":{"message":"Rate limit reached for mock-gpt-4o in organization org-xxx on requests per min (RPM): Limit 3, Used 3, Requested 1.","type":"RateLimitError","param":null,"code":429}}
```

**Expected response pattern (with single replica):**
- Request 1: `429 Too Many Requests` from mock-gpt-4o
- Request 2: `200 OK` from OpenAI (failover successful)
- Request 3: `200 OK` from OpenAI (continues using failover)

**What's happening:**
1. The first request hits the mock server and receives a 429 error
2. The mock model's health policy evaluates `response.code == 429` → `true`, and since `consecutiveFailures: 1`, the mock is evicted for 60 seconds (`eviction.duration`)
3. All subsequent requests are routed to priority 1 (OpenAI) and receive successful 200 responses
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

The change in `endpoint` field between requests shows the failover from the mock server to OpenAI.

### View Traces in the Solo UI

To view distributed traces and see failover behavior across requests:

1. Port-forward to the Solo UI:
```bash
kubectl port-forward -n agentgateway-system svc/enterprise-agentgateway-ui 4000:80
```

2. Open http://localhost:4000 in your browser

3. Click **Tracing** in the left navigation

4. Compare traces from your two requests:
   - First trace: Shows attempt to mock-gpt-4o with 429 error span
   - Second trace: Shows successful routing to OpenAI backend with 200 response
5. You can see how the backend selection changes based on provider health

## Understanding Priority Group Failover

### How Priority Groups Work

1. Priority Ordering: The virtual model prefers targets with lower `priority` values (0 over 1, etc.). Targets that share a priority form a group, and the gateway picks among them by health and latency
2. Health Policy: `policies.health` on a concrete model defines what constitutes an unhealthy response and how eviction works. **Without a health policy, a model is never evicted and failover will not trigger.**
   - `unhealthyCondition` is a CEL expression evaluated against each response. If it returns `true`, the response counts toward eviction
   - `eviction.consecutiveFailures` sets how many consecutive unhealthy responses are required before eviction (use `1` for immediate eviction, `3` to tolerate transient errors)
   - `eviction.duration` sets the base removal time from the priority group. Duration increases with multiplicative backoff on repeated evictions
3. Health-Based Eviction: When a model's responses match the `unhealthyCondition` and thresholds are met, it's evicted from the pool
   - The first request that encounters an error will fail with that error code
   - The provider is evicted after processing the error response
   - Subsequent requests will skip evicted models and use the next priority
   - **Important**: Health state is local to each AgentGateway pod. With multiple replicas, you may see 1-2 failed requests before failover as different pods learn about the unhealthy state
4. Eviction Duration: The `eviction.duration` in the health policy controls how long a model is removed from the pool. After the period expires, the gateway will retry the primary backend to check if it has recovered. Duration increases with multiplicative backoff on repeated evictions, preventing rapid cycling on persistently failing backends
5. Across-Request Failover: Unlike retry policies that work within a single request, priority group failover works across multiple requests based on provider health state
   - With a single replica: expect 1 failed request, then failover to the next priority group
   - With multiple replicas: expect 1-2 failed requests before all pods mark the provider as unhealthy
   - Once a provider is evicted, all subsequent requests use the failover backend until the eviction period expires
6. CEL Expression Examples: The `unhealthyCondition` field supports flexible CEL expressions:
   - `"response.code >= 500 || response.code == 429"`: evict on server errors and rate limits
   - `"response.code >= 500"`: evict on server errors only
   - `"response.code >= 400"`: evict on any client or server error
   - `"true"`: evict on every response (testing only)
7. Production Use Case: This pattern is ideal for scenarios where you have:
   - Primary backends that may experience temporary rate limiting or server errors
   - Fallback backends as safety nets for subsequent requests
   - Different cost tiers (prefer cheaper model, fall back to more expensive when primary is unavailable)
   - Circuit-breaking behavior without explicit circuit breaker configuration

## Next Steps: Advanced Failover Patterns

The basic lab above demonstrates one failover from a single failing backend to a healthy fallback. For more advanced patterns (load balancing across multiple providers within a priority group, eviction on 5XX server errors, and proving the full intra-group LB + per-provider eviction + inter-group failover behavior end-to-end), see [Advanced LLM Failover Patterns](llm-failover-advanced.md).

## Cleanup

Delete the lab resources:
```bash
kubectl delete agentgatewaymodel -n agentgateway-system resilient openai-gpt-5-4-nano mock-gpt-4o
kubectl delete httproute -n agentgateway-system mock-ratelimit-failover
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o
```

If you ran any of the patterns in the [Advanced LLM Failover Patterns](llm-failover-advanced.md) lab, follow the Cleanup section there as well.

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
