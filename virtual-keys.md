# Virtual Keys using Agentgateway

## Pre-requisites
This lab assumes that you have completed `001` and `api-key-masking`. `002` is optional but recommended if you want to observe metrics and traces.

The following resources from the `api-key-masking` lab should still be running:
- `openai-secret` — upstream OpenAI credentials
- `openai-all-models` AgentgatewayBackend and `openai` HTTPRoute
- `apikey-auth` AuthConfig and `api-key-auth` EnterpriseAgentgatewayPolicy

## Lab Objectives
- Issue per-user virtual keys (alice and bob) with independent token budgets
- Enforce per-user token budgets via token-based rate limiting
- Demonstrate budget isolation: alice exhausting her budget does not affect bob
- Observe per-user token usage in access logs

## About virtual keys

Virtual key management is a common feature in AI gateway solutions (LiteLLM, Portkey) that issues API keys to users or applications with independent token budgets and cost tracking. The `api-key-masking` lab showed how to issue a single team key to abstract the upstream OpenAI credential. This lab extends that pattern to per-user keys with budget enforcement.

Agentgateway achieves virtual keys by composing three capabilities:

1. **API key authentication** — already configured in the `api-key-masking` lab
2. **Token-based rate limiting** — enforces independent per-key token budgets
3. **Observability** — tracks per-user spending via access logs and metrics

## Create per-user API keys

Create API key secrets for alice and bob. The `api-key-group: llm-users` label lets the AuthConfig discover all user keys via label selector — adding a new user is as simple as creating a new secret with this label.

```bash
kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: user-alice-key
  namespace: agentgateway-system
  labels:
    api-key-group: llm-users
type: extauth.solo.io/apikey
stringData:
  api-key: sk-alice-abc123def456
---
apiVersion: v1
kind: Secret
metadata:
  name: user-bob-key
  namespace: agentgateway-system
  labels:
    api-key-group: llm-users
type: extauth.solo.io/apikey
stringData:
  api-key: sk-bob-xyz789uvw012
EOF
```

## Update AuthConfig to use label selector

Update `apikey-auth` to discover user keys by label instead of referencing a specific secret. This replaces the single `team1-apikey` reference from the `api-key-masking` lab with a dynamic selector that automatically includes any secret labeled `api-key-group: llm-users`.

```bash
kubectl apply -f- <<EOF
apiVersion: extauth.solo.io/v1
kind: AuthConfig
metadata:
  name: apikey-auth
  namespace: agentgateway-system
spec:
  configs:
    - apiKeyAuth:
        headerName: vanity-auth
        k8sSecretApikeyStorage:
          labelSelector:
            api-key-group: llm-users
EOF
```

## Test authentication with virtual keys

Verify that alice and bob can both authenticate with their respective keys. Note the `X-User-ID` header — this identifies each user for independent token budget tracking.

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

Send a request as alice:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "vanity-auth: sk-alice-abc123def456" \
  -H "X-User-ID: alice" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Send a request as bob:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "vanity-auth: sk-bob-xyz789uvw012" \
  -H "X-User-ID: bob" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Both requests should succeed with a 200 response.

## Add per-user token budgets

Add the second pillar of virtual keys: token budget enforcement. Create a `RateLimitConfig` that enforces a limit of 100 input tokens per hour, keyed by the `X-User-ID` header. Each unique user ID gets its own independent counter. The `unit` field controls the budget window — `HOUR` suits sandbox quotas, `DAY` for production cost control, `MINUTE` for burst protection.

```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: virtual-key-budgets
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: X-User-ID
      rateLimit:
        unit: HOUR
        requestsPerUnit: 100
    rateLimits:
    - actions:
      - requestHeaders:
          descriptorKey: "X-User-ID"
          headerName: "X-User-ID"
      type: TOKEN
EOF
```

Create an `EnterpriseAgentgatewayPolicy` to enforce the token budgets. This stacks on top of the existing auth policy — requests must first pass authentication, then have remaining budget available.

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: virtual-key-budget-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - name: agentgateway-proxy
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    entRateLimit:
      global:
        rateLimitConfigRefs:
        - name: virtual-key-budgets
EOF
```

## Test budget isolation

> **Note — resetting rate limit counters:** Token counters are stored in `ext-cache` (Redis) and persist for the duration of the budget window. Any requests made during the auth testing steps above already consumed tokens against each user's budget. If alice hits `429` on the first request, reset the counters before continuing:
> ```bash
> kubectl rollout restart deployment/ext-cache-enterprise-agentgateway -n agentgateway-system
> kubectl rollout status deployment/ext-cache-enterprise-agentgateway -n agentgateway-system
> ```

The defining property of virtual keys is that each user's budget is independent. Run several requests as alice to exhaust her 100-token hourly budget, then verify bob's budget is unaffected.

Send multiple requests as alice until her budget is exhausted:
```bash
for i in {1..20}; do
  echo "--- Alice request $i ---"
  curl -s -o /dev/null -w "HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "vanity-auth: sk-alice-abc123def456" \
    -H "X-User-ID: alice" \
    -d '{
      "model": "gpt-4o-mini",
      "messages": [{"role": "user", "content": "What is 1+1?"}]
    }'
done
```

You should see `HTTP 200` responses until alice's 100-token budget is exhausted, then `HTTP 429`.

Now verify bob still has his full budget:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "vanity-auth: sk-bob-xyz789uvw012" \
  -H "X-User-ID: bob" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }'
```

Bob's request succeeds with a 200 response. His token budget is tracked independently from alice's.

## Advanced configuration

These sections extend the lab. Each is independent — apply whichever patterns are relevant, replacing the `virtual-key-budgets` `RateLimitConfig` as needed.

### Multi-tenant virtual keys

Scope budgets to a user+tenant combination so that `alice` in `tenant-a` has an independent budget from `alice` in `tenant-b`. The `X-Tenant-ID` header is passed by the client alongside `X-User-ID` to identify the tenant context.

Delete the existing budget config and replace it with a two-key descriptor:
```bash
kubectl delete ratelimitconfig -n agentgateway-system virtual-key-budgets

kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: virtual-key-budgets
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: X-User-ID
      descriptors:
      - key: X-Tenant-ID
        rateLimit:
          unit: HOUR
          requestsPerUnit: 100
    rateLimits:
    - actions:
      - requestHeaders:
          descriptorKey: "X-User-ID"
          headerName: "X-User-ID"
      - requestHeaders:
          descriptorKey: "X-Tenant-ID"
          headerName: "X-Tenant-ID"
      type: TOKEN
EOF
```

> **Note:** Replacing the `RateLimitConfig` does not reset the Redis counters. If needed, restart `ext-cache` to start with a clean slate: `kubectl rollout restart deployment/ext-cache-enterprise-agentgateway -n agentgateway-system`

Test that alice has a separate budget per tenant:
```bash
# Alice in tenant-a
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "vanity-auth: sk-alice-abc123def456" \
  -H "X-User-ID: alice" \
  -H "X-Tenant-ID: tenant-a" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}]}'

# Same user, different tenant — separate budget counter
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "vanity-auth: sk-alice-abc123def456" \
  -H "X-User-ID: alice" \
  -H "X-Tenant-ID: tenant-b" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Exhaust alice's budget in `tenant-a` and verify requests to `tenant-b` still succeed — the budget key is the `(user, tenant)` pair, not the user alone.

### Tiered budgets based on user type

Embed the budget tier directly in the API key credential so users cannot self-upgrade their own quota. This reuses the `headersFromMetadataEntry` mechanism from the `api-key-masking` lab — the tier is stored in the secret's `stringData` and automatically injected as a request header by the auth system before rate limiting is evaluated.

1. Update alice to `premium` tier and add a new `free` user charlie:

```bash
kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: user-alice-key
  namespace: agentgateway-system
  labels:
    api-key-group: llm-users
type: extauth.solo.io/apikey
stringData:
  api-key: sk-alice-abc123def456
  x-user-tier: premium
---
apiVersion: v1
kind: Secret
metadata:
  name: user-charlie-key
  namespace: agentgateway-system
  labels:
    api-key-group: llm-users
type: extauth.solo.io/apikey
stringData:
  api-key: sk-charlie-ghi345jkl678
  x-user-tier: free
EOF
```

2. Update `apikey-auth` to inject `x-user-tier` from the secret into every request:

```bash
kubectl apply -f- <<EOF
apiVersion: extauth.solo.io/v1
kind: AuthConfig
metadata:
  name: apikey-auth
  namespace: agentgateway-system
spec:
  configs:
    - apiKeyAuth:
        headerName: vanity-auth
        k8sSecretApikeyStorage:
          labelSelector:
            api-key-group: llm-users
        headersFromMetadataEntry:
          x-user-tier:
            name: x-user-tier
EOF
```

The `x-user-tier` header is set by the gateway from the credential — the client never sees or controls it.

3. Replace the `RateLimitConfig` with tiered descriptors. Free users get 50 tokens/hour; premium users get 500:

```bash
kubectl delete ratelimitconfig -n agentgateway-system virtual-key-budgets

kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: virtual-key-budgets
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: X-User-Tier
      value: "free"
      descriptors:
      - key: X-User-ID
        rateLimit:
          unit: HOUR
          requestsPerUnit: 50
    - key: X-User-Tier
      value: "premium"
      descriptors:
      - key: X-User-ID
        rateLimit:
          unit: HOUR
          requestsPerUnit: 500
    rateLimits:
    - actions:
      - requestHeaders:
          descriptorKey: "X-User-Tier"
          headerName: "X-User-Tier"
      - requestHeaders:
          descriptorKey: "X-User-ID"
          headerName: "X-User-ID"
      type: TOKEN
EOF
```

> **Note:** If needed, restart `ext-cache` to ensure counters are clean: `kubectl rollout restart deployment/ext-cache-enterprise-agentgateway -n agentgateway-system`

4. Exhaust charlie's free budget, then verify alice's premium budget is unaffected:

```bash
# Exhaust charlie's 50-token free budget
for i in {1..15}; do
  echo "--- Charlie request $i ---"
  curl -s -o /dev/null -w "HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "vanity-auth: sk-charlie-ghi345jkl678" \
    -H "X-User-ID: charlie" \
    -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is 1+1?"}]}'
done
```

Charlie hits 429 after her free-tier budget is exhausted. Verify alice's premium budget is untouched:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "vanity-auth: sk-alice-abc123def456" \
  -H "X-User-ID: alice" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Still working?"}]}'
```

Alice succeeds because her 500-token premium budget is tracked independently from charlie's free-tier counter.

### Observability

Token usage visibility is covered in the standalone `llm-cost-tracking` lab, which includes access log inspection and Prometheus PromQL queries for per-user consumption and cost.

## Cleanup
```bash
# Virtual keys resources added in this lab
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system virtual-key-budget-policy
kubectl delete ratelimitconfig -n agentgateway-system virtual-key-budgets
kubectl delete secret -n agentgateway-system user-alice-key user-bob-key user-charlie-key
# Base resources from api-key-masking
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system api-key-auth
kubectl delete authconfig -n agentgateway-system apikey-auth
kubectl delete secret -n agentgateway-system team1-apikey
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```
