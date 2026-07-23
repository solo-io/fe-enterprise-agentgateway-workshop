# Virtual Keys using Agentgateway

## Prerequisites
This lab assumes that you have completed `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Issue per-user virtual keys (alice and bob) with independent token budgets
- Enforce per-user token budgets via token-based rate limiting
- Demonstrate budget isolation: alice exhausting her budget does not affect bob
- Observe per-user token usage in access logs

## About virtual keys

Virtual key management is a common feature in AI gateway solutions (LiteLLM, Portkey) that issues API keys to users or applications with independent token budgets and cost tracking.

Agentgateway achieves virtual keys by composing three capabilities:

1. **API key authentication** — validates incoming keys and extracts per-key metadata
2. **Token-based rate limiting** — enforces independent per-key token budgets
3. **Observability** — tracks per-user spending via access logs and metrics

The key security advantage over header-based rate limiting: the budget key (`user_id`) is extracted from the API key credential by the gateway — the client cannot forge or override it.

## Set up the OpenAI backend

Create the OpenAI credential secret. The gateway uses this to authenticate upstream — callers never see it.

```bash
kubectl create secret generic openai-secret -n agentgateway-system \
  --from-literal="Authorization=Bearer $OPENAI_API_KEY" \
  --dry-run=client -oyaml | kubectl apply -f -
```

Create the backend and route:

```bash
kubectl apply -f- <<EOF
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
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

Verify both resources are accepted:

```bash
kubectl get enterpriseagentgatewaybackend openai-all-models -n agentgateway-system
kubectl get httproute openai -n agentgateway-system \
  -o jsonpath='{range .status.parents[*].conditions[*]}{.type}={.status}{"\n"}{end}'
```

The backend should show `ACCEPTED   True`. The route (which has no status column in plain `kubectl get`) should print `Accepted=True` and `ResolvedRefs=True`.

## Create per-user API keys

Create one Secret per user, each carrying the label `app: llm-virtual-keys`. The auth policy discovers keys by this label (next section) instead of by a single Secret name, so the keys can live in separate Secrets — owned and RBAC-scoped by different teams — and new users are onboarded by adding another labeled Secret, with no edit to a central Secret or the policy.

Each entry stores the API key and a `user_id` that the gateway extracts for rate limiting — the client never supplies it. (A single Secret may hold multiple entries; one-per-user is used here to demonstrate discovery across Secrets.)

```bash
kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: alice-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  alice: |
    {
      "key": "sk-alice-abc123def456",
      "metadata": {
        "user_id": "alice"
      }
    }
---
apiVersion: v1
kind: Secret
metadata:
  name: bob-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  bob: |
    {
      "key": "sk-bob-xyz789uvw012",
      "metadata": {
        "user_id": "bob"
      }
    }
EOF
```

> **Note — unique entry ids:** Each entry key (the `alice` / `bob` map key) must be unique across all labeled Secrets. If the same entry id appears in two matched Secrets, the resulting key set is undefined.

## Configure API key authentication

Create an `EnterpriseAgentgatewayPolicy` that requires API key authentication for all gateway traffic. Instead of naming a single Secret with `secretRef`, use `secretSelector` to discover **every** Secret in the namespace carrying the `app: llm-virtual-keys` label and union their entries into the valid-key set. The `mode: Strict` setting rejects any request that does not present a recognized key.

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: api-key-auth
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    apiKeyAuthentication:
      mode: Strict
      secretSelector:
        matchLabels:
          app: llm-virtual-keys
EOF
```

`secretRef` and `secretSelector` are mutually exclusive — set exactly one. (`secretRef` by name is covered in the public docs; this lab uses the label-selector approach so keys can be spread across Secrets and discovered automatically.)

## Test authentication

Export the gateway IP:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

Send a request as alice:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-alice-abc123def456" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Send a request as bob:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-bob-xyz789uvw012" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Both should return `HTTP 200`. Verify that an unknown key is rejected:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-invalid-key" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Expected: `HTTP 401`.

## Add per-user token budgets

Token budgets use **global rate limiting** against the rate limiter that already ships with the enterprise install. You declare the budget in a `RateLimitConfig` CRD; the controller pushes it to `rate-limiter-enterprise-agentgateway`, and counters are stored in the shared `ext-cache` Redis — so budgets stay consistent across every gateway replica with no extra components to deploy.

### Configure the token budget

Create a `RateLimitConfig` that defines the `token-budgets` domain, the per-user limit, and how to extract the user identity.

```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: token-budgets
  namespace: agentgateway-system
spec:
  raw:
    domain: token-budgets
    descriptors:
      - key: user_id
        rateLimit:
          unit: HOUR
          requestsPerUnit: 100
    rateLimits:
      - actions:
          - cel:
              expression: 'apiKey.user_id'
              key: "user_id"
        type: TOKEN
EOF
```

Two fields make this a **token** budget rather than a request budget:

- `type: TOKEN` — counts LLM tokens against the limit instead of requests. Without it, `requestsPerUnit: 100` would mean *100 requests per hour*; with it, it means *100 tokens per hour*.
- `cel.expression: 'apiKey.user_id'` — extracts the user identity embedded in the API key credential. The key's `metadata` block is flattened onto `apiKey`, so the `user_id` field is referenced as **`apiKey.user_id`** (not `apiKey.metadata.user_id`). Clients cannot forge or override this value — it comes from the validated credential, not from a request header.

### Create the token budget policy

Create an `EnterpriseAgentgatewayPolicy` that attaches the token budget to the gateway via the `entRateLimit` API.

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-budget-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    entRateLimit:
      global:
        rateLimitConfigRefs:
          - name: token-budgets
EOF
```

## Test budget isolation

> **Note — resetting rate limit counters:** Token counters are stored in `ext-cache` (Redis) and persist for the duration of the budget window. If alice hits `429` on the first request, reset the counters:
> ```bash
> kubectl rollout restart deployment/ext-cache-enterprise-agentgateway -n agentgateway-system
> kubectl rollout status deployment/ext-cache-enterprise-agentgateway -n agentgateway-system
> ```

The defining property of virtual keys is budget isolation. Exhaust alice's budget, then verify bob is unaffected.

Send multiple requests as alice until her 100-token hourly budget is exhausted:

```bash
for i in {1..20}; do
  echo "--- Alice request $i ---"
  curl -s -o /dev/null -w "HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-alice-abc123def456" \
    -d '{
      "model": "gpt-4o-mini",
      "messages": [{"role": "user", "content": "What is 1+1?"}]
    }'
done
```

You should see `HTTP 200` responses until the 100-token budget is exhausted, then `HTTP 429`.

Verify bob still has his full budget:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-bob-xyz789uvw012" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }'
```

Expected: `HTTP 200`. Bob's token budget is tracked independently from alice's.

## Advanced configuration

These sections extend the lab. Each is independent — apply whichever patterns are relevant. Each replaces the `token-budgets` RateLimitConfig from above.

> **Note:** The `RateLimitConfig` CRD is watched by the controller and pushed to the rate limiter automatically — no restart is needed when you change it. Existing counters in `ext-cache` are **not** reset by a config change, though; to clear budgets between tests: `kubectl rollout restart deployment/ext-cache-enterprise-agentgateway -n agentgateway-system`

### Tiered budgets based on user type

Embed the budget tier directly in the API key credential so users cannot self-upgrade their quota. The `tier` field in the key metadata is set by whoever creates the key — the client never touches it.

1. Update alice to `premium` tier and add a `free` user charlie:

```bash
kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: alice-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  alice: |
    {
      "key": "sk-alice-abc123def456",
      "metadata": {
        "user_id": "alice",
        "tier": "premium"
      }
    }
---
apiVersion: v1
kind: Secret
metadata:
  name: bob-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  bob: |
    {
      "key": "sk-bob-xyz789uvw012",
      "metadata": {
        "user_id": "bob",
        "tier": "free"
      }
    }
---
apiVersion: v1
kind: Secret
metadata:
  name: charlie-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  charlie: |
    {
      "key": "sk-charlie-ghi345jkl678",
      "metadata": {
        "user_id": "charlie",
        "tier": "free"
      }
    }
EOF
```

2. Replace the `RateLimitConfig` with tiered budget descriptors. Free users get 50 tokens/hour; premium users get 500:

```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: token-budgets
  namespace: agentgateway-system
spec:
  raw:
    domain: token-budgets
    descriptors:
      - key: tier
        value: "free"
        descriptors:
          - key: user_id
            rateLimit:
              unit: HOUR
              requestsPerUnit: 50
      - key: tier
        value: "premium"
        descriptors:
          - key: user_id
            rateLimit:
              unit: HOUR
              requestsPerUnit: 500
    rateLimits:
      - actions:
          - cel:
              expression: 'apiKey.tier'
              key: "tier"
          - cel:
              expression: 'apiKey.user_id'
              key: "user_id"
        type: TOKEN
EOF
```

3. The `token-budget-policy` is unchanged — it already references `token-budgets` by name. Apply it again if needed:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-budget-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    entRateLimit:
      global:
        rateLimitConfigRefs:
          - name: token-budgets
EOF
```

4. Exhaust charlie's free budget, then verify alice's premium budget is unaffected:

```bash
# Exhaust charlie's 50-token free budget
for i in {1..15}; do
  echo "--- Charlie request $i ---"
  curl -s -o /dev/null -w "HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-charlie-ghi345jkl678" \
    -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is 1+1?"}]}'
done
```

Charlie hits `429` after her free-tier budget is exhausted. Verify alice's premium budget is untouched:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-alice-abc123def456" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Still working?"}]}'
```

Expected: `HTTP 200`.

### Multi-tenant virtual keys

Scope budgets to a `(tenant_id, user_id)` pair so that `alice` in `tenant-a` has an independent budget from `alice` in `tenant-b`. Add `tenant_id` to each key's metadata. Because this reuses alice's key string under a new tenant entry, first clear the previous virtual-key Secrets so the old (tenant-less) entries don't linger with the same key value, then create the tenant-scoped Secrets:

```bash
kubectl delete secret -n agentgateway-system -l app=llm-virtual-keys

kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: alice-tenant-a-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  alice-tenant-a: |
    {
      "key": "sk-alice-abc123def456",
      "metadata": {
        "user_id": "alice",
        "tenant_id": "tenant-a"
      }
    }
---
apiVersion: v1
kind: Secret
metadata:
  name: alice-tenant-b-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  alice-tenant-b: |
    {
      "key": "sk-alice-tenant-b-000111",
      "metadata": {
        "user_id": "alice",
        "tenant_id": "tenant-b"
      }
    }
EOF
```

Replace the `RateLimitConfig` with tenant-scoped descriptors:

```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: token-budgets
  namespace: agentgateway-system
spec:
  raw:
    domain: token-budgets
    descriptors:
      - key: tenant_id
        descriptors:
          - key: user_id
            rateLimit:
              unit: HOUR
              requestsPerUnit: 100
    rateLimits:
      - actions:
          - cel:
              expression: 'apiKey.tenant_id'
              key: "tenant_id"
          - cel:
              expression: 'apiKey.user_id'
              key: "user_id"
        type: TOKEN
EOF
```

The `token-budget-policy` is unchanged — it references `token-budgets` by name.

Exhaust alice's budget in `tenant-a`, then verify requests using `tenant-b` still succeed — the budget key is the `(tenant, user)` pair, not the user alone:

```bash
# Exhaust alice in tenant-a
for i in {1..20}; do
  echo "--- Alice (tenant-a) request $i ---"
  curl -s -o /dev/null -w "HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-alice-abc123def456" \
    -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is 1+1?"}]}'
done

# Alice in tenant-b still has her full budget
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-alice-tenant-b-000111" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello from tenant-b!"}]}'
```

Expected: `HTTP 200` for tenant-b despite tenant-a being exhausted.

### Observability

Token usage visibility, per-user/per-group cost attribution, and budget tracking through the Solo UI's Cost Management dashboard are covered in the standalone `llm-cost-management` lab.

## Cleanup

```bash
# Policies and rate limit config added in this lab
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system api-key-auth token-budget-policy --ignore-not-found
kubectl delete ratelimitconfig -n agentgateway-system token-budgets --ignore-not-found
kubectl delete secret -n agentgateway-system -l app=llm-virtual-keys --ignore-not-found

# Backend resources set up at the start of this lab
kubectl delete httproute -n agentgateway-system openai --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret --ignore-not-found
```
