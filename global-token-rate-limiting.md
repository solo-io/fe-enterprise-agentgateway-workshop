# Configure Input Token Based Rate Limiting

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `EnterpriseAgentgatewayBackend` and `HTTPRoute`
- Create an initial RateLimitConfig to implement token-based rate limiting (input tokens) using a shared global counter
- Use CEL expressions to key rate limits on request headers, JWT claims, client IP, and plan tiers
- Validate token-based rate limiting and per-user isolation

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and backend
```bash
kubectl apply -f - <<EOF
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
        #--- Uncomment to configure model override ---
        #model: ""
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## CEL actions and the descriptor model

Every rate limit scenario in this lab (except the baseline global counter) uses a **CEL action** to derive the descriptor value dynamically per request. Understanding the model makes every subsequent config self-explanatory.

A `RateLimitConfig` defines two things:
1. **`descriptors`** — a tree of key→value pairs, each leaf carrying a `rateLimit` (the budget). The rate limit service uses these to find the right counter bucket.
2. **`rateLimits[].actions`** — instructions for how to build the descriptor from the request. A `cel` action evaluates an expression against the live request context and emits the result as the descriptor value.

When a request arrives, the proxy evaluates the CEL expression, sends the resulting string to the rate limit service as a descriptor entry, and the service matches it against the configured tree to find the counter to decrement.

### CEL expressions available in `entRateLimit`

| Descriptor | CEL Expression | Description |
|---|---|---|
| Client IP | `source.address` | Source IP of the downstream connection |
| Request path | `request.path` | The request URI path |
| Request method | `request.method` | HTTP method |
| Header value | `request.headers["name"]` | Value of a specific header (case-insensitive) |
| JWT standard claim | `jwt.sub` | `sub` claim from a validated JWT (requires JWT auth policy) |
| JWT custom claim | `jwt.<claim_name>` | Any custom claim — e.g. `jwt.plan`, `jwt.org`, `jwt.team` |
| Static value | `"constant"` | Fixed string — used for the global shared counter |

## Configure global token-based rate limit of 10 input tokens per hour
Create rate limit config, note that this policy uses `type: TOKEN`
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: token-based-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: generic_key
      value: counter
      rateLimit:
        requestsPerUnit: 10
        unit: HOUR
    rateLimits:
    - actions:
      - genericKey:
          descriptorValue: counter
      type: TOKEN
EOF
```

Create EnterpriseAgentgatewayPolicy referencing the rate limit config we just created
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-based-rate-limit
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
        - name: token-based-rate-limit
EOF
```

## curl openai
Note that the following user prompt "Whats your favorite poem" contains 5 tokens based on the [OpenAI tokenizer](https://platform.openai.com/tokenizer)
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
You should be rate limited after several requests to the LLM because we will have hit our token-based rate limit of 10 input tokens per hour

## Configure header-based token rate limiting
Now let's configure a rate limit based on a custom header (`X-User-ID`) instead of a generic counter. The `cel` action evaluates `request.headers["X-User-ID"]` per request — each distinct header value becomes its own counter bucket, giving each user their own quota.

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n agentgateway-system token-based-rate-limit
```

Create a header-based rate limit config:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: openai-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: X-User-ID
      rateLimit:
        unit: MINUTE
        requestsPerUnit: 100
    rateLimits:
    - actions:
      - cel:
          expression: 'request.headers["X-User-ID"]'
          key: "X-User-ID"
      type: TOKEN
EOF
```

Update the EnterpriseAgentgatewayPolicy to reference the new rate limit config:
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-based-rate-limit
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
        - name: openai-rate-limit
EOF
```

## Test header-based rate limiting
Now curl with the X-User-ID header to test per-user rate limiting:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "X-User-ID: user-123" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

Each user (identified by their X-User-ID header value) will have their own token quota of 100 input tokens per minute. Try using different user IDs to see separate rate limit counters:
```bash
# Test with different user
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "X-User-ID: user-456" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## Configure multi-header based token rate limiting
Now let's configure rate limiting based on multiple headers using two `cel` actions. Each action contributes one dimension to a composite descriptor key — the rate limit applies to the **combination** of both values (e.g., `user-123` in `tenant-A` has a separate quota from `user-123` in `tenant-B`).

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n agentgateway-system openai-rate-limit
```

Create a multi-header rate limit config that limits based on both X-User-ID and X-Tenant-ID:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: multi-header-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: X-User-ID
      descriptors:
      - key: X-Tenant-ID
        rateLimit:
          unit: MINUTE
          requestsPerUnit: 50
    rateLimits:
    - actions:
      - cel:
          expression: 'request.headers["X-User-ID"]'
          key: "X-User-ID"
      - cel:
          expression: 'request.headers["X-Tenant-ID"]'
          key: "X-Tenant-ID"
      type: TOKEN
EOF
```

Update the EnterpriseAgentgatewayPolicy to reference the new rate limit config:
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-based-rate-limit
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
        - name: multi-header-rate-limit
EOF
```

## Test multi-header rate limiting
Test with user-123 in tenant-A:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "X-User-ID: user-123" \
  -H "X-Tenant-ID: tenant-A" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

Now test the same user-123 but in a different tenant (tenant-B). This should have a separate quota:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "X-User-ID: user-123" \
  -H "X-Tenant-ID: tenant-B" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

The rate limit is enforced on the combination of both headers. Each user-tenant combination gets its own quota of 50 input tokens per minute. If user-123 in tenant-A hits their limit, user-123 in tenant-B will still have their full quota available.

## Configure JWT claim-based token rate limiting

The header-based approach above works, but `X-User-ID` is just a request header — **any client can set it to any value**. A user who exhausts their quota can simply send a different `X-User-ID` and get a fresh budget, so a header is not a trustworthy identity for billing or quotas.

For a quota a client cannot evade, key the rate limit off a claim inside a **validated JWT** instead. The gateway verifies the token's signature *before* the rate limit service ever sees the claim, so the descriptor value cannot be spoofed. This reuses the same `type: TOKEN` (input token) rate limiting from above; the only change is that the descriptor value comes from a CEL expression reading the JWT's `sub` claim (`jwt.sub`) rather than a header.

Because JWT validation and the rate limit live in the **same** `EnterpriseAgentgatewayPolicy`, the token is validated first and its claims are available to the rate limit CEL expression.

> This section uses the workshop demo keypair under `lib/jwt/` (issuer `workshop.solo.io`, `kid: workshop-jwt-key-001`) and its token generator. Run the commands from the repository root, and ensure `openssl`, `base64`, and `bash` are available locally.

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n agentgateway-system multi-header-rate-limit
```

Create a rate limit config that keys on the `jwt.sub` claim via a CEL expression. The descriptor has a `key` but no fixed `value`, so each distinct `sub` value gets its own independent quota of 10 input tokens per minute:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: jwt-claim-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: jwt_sub
      rateLimit:
        unit: MINUTE
        requestsPerUnit: 10
    rateLimits:
    - actions:
      - cel:
          expression: 'jwt.sub'
          key: "jwt_sub"
      type: TOKEN
EOF
```

The CEL expression `jwt.sub` reads the `sub` claim from the validated token. Like the MCP tool rate limiting lab, the descriptor value is computed dynamically per request — but here it comes from a cryptographically verified claim instead of the request body or a header.

Now update the `EnterpriseAgentgatewayPolicy` to (1) validate incoming JWTs and (2) reference the new rate limit config. The `jwtAuthentication` block validates the token signature against the inline workshop JWKS; once validated, the `jwt.sub` claim is available to the rate limit CEL expression:
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-based-rate-limit
  namespace: agentgateway-system
spec:
  targetRefs:
    - name: agentgateway-proxy
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: workshop.solo.io
          jwks:
            inline: |
                {
                  "keys": [
                    {
                      "kty": "RSA",
                      "kid": "workshop-jwt-key-001",
                      "use": "sig",
                      "alg": "RS256",
                      "n": "x0WUh5Pyx5CS9piu7QPMtaB7d2cJPDhV1DJVOwdTOVi39g1eP0it1TKJ4kSvEWsAc-L1KOTsTjfEGNUfIdKfPk8E8_vY3JHBBrN1pg0iwEX31xGdAGOGkGks-oT5Ois2MXlHzMYz2Hhok0GfUTPc2W8V4_POexx-Kpsyac_6_V2mbsHy9W1jUBrVaaC0t8SeFuxeE39Huzys9moCN4dMfMOy18svga06aGtAbTo_MVtVthGXU_Bwe3GWSCOL62E2f8C4XHSo-9ttte-pqgjLYSnz9vUvYp4zSUMqQtZ-XVZ-n26XZNVIBDtB23hBlC8KHmDnMh5yZ2Ye2A7a5uXFNw",
                      "e": "AQAB"
                    }
                  ]
                }
    entRateLimit:
      global:
        rateLimitConfigRefs:
        - name: jwt-claim-rate-limit
EOF
```

### Mint two user tokens

Use the bundled generator to sign two tokens for two different users. They carry distinct `sub` claims (`analyst-user` and `economist-user`), so each maps to its own rate limit counter:
```bash
export USER_A_TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/analyst.json)
export USER_B_TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/economist.json)
```

Decode one to confirm its `sub` claim:
```bash
echo "$USER_A_TOKEN" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null; echo
```
```json
{
  "iss": "workshop.solo.io",
  "sub": "analyst-user",
  "exp": 4070908800,
  "persona": "analyst",
  "org": "equity-research",
  "team": "fundamentals"
}
```

### Test JWT claim-based rate limiting

Requests without a valid JWT are now rejected before they ever reach the rate limiter. Try without a token:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
The request fails with `authentication failure: no bearer token found`.

Now send requests as **User A**. The prompt `"Whats your favorite poem?"` is 5 input tokens, so against the 10-input-tokens-per-minute budget you'll be rate limited after a couple of requests:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $USER_A_TOKEN" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
Repeat until you receive a `429 Too Many Requests` — User A (`sub=analyst-user`) has exhausted their quota.

### Verify independent per-user counters

User B (`sub=economist-user`) has a completely separate counter. Even though User A is rate limited, User B's first requests still succeed:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $USER_B_TOKEN" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
Each user (identified by their verified `sub` claim) gets their own quota of 10 input tokens per minute — just like the header-based example, but the identity is now cryptographically verified rather than self-asserted.

### The claim cannot be spoofed

With the header-based config, a client could change `X-User-ID` to escape their quota. Here the counter follows the verified `jwt.sub`, not any header the client sends. Send User A's (already rate-limited) token but add an arbitrary `X-User-ID` — it lands in the **same** counter, because the descriptor value comes from the signed claim:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $USER_A_TOKEN" \
  -H "X-User-ID: someone-else" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
User A stays rate limited. The only way to obtain a separate quota is to present a different validly-signed token with a different `sub` claim — which a client cannot forge without the issuer's private key.

> **Tip:** To rate limit per team or tenant instead of per user, point the CEL expression at a different claim, for example `expression: 'jwt.team'` (the analyst token's `team` claim is `fundamentals`).

### Composite keys: rate limit on multiple JWT claims

Just like the multi-header example above, you can combine several JWT claims into one composite key by adding a second `cel` action and nesting the descriptors. The rate limit then applies to the **combination** of claim values — e.g. `org` + `team`, so `equity-research/fundamentals` has a separate quota from `equity-research/research`:

```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: jwt-claim-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: jwt_org
      descriptors:
      - key: jwt_team
        rateLimit:
          unit: MINUTE
          requestsPerUnit: 10
    rateLimits:
    - actions:
      - cel:
          expression: 'jwt.org'
          key: "jwt_org"
      - cel:
          expression: 'jwt.team'
          key: "jwt_team"
      type: TOKEN
EOF
```

The two `cel` actions populate `jwt_org` and `jwt_team`, and the nested descriptor structure means the 10-token/minute budget is tracked per `(org, team)` pair. A request whose JWT carries `org=equity-research, team=fundamentals` is counted separately from one with `org=equity-research, team=research` — exhausting one combination leaves the other untouched. Add more `cel` actions and nesting levels to key on any number of claims.

## Configure tier-based token quotas

The JWT claim examples above assign the same per-minute budget to every authenticated user. In practice, different consumers often have different entitlements — a **standard** plan might get 20 tokens/minute while a **premium** plan gets 200. Nest value descriptors in the `RateLimitConfig` to assign different limits per claim value, while a single `cel` action still keys the counter dynamically.

The `jwt.plan` expression reads the `plan` claim from the validated token — the same dot-notation as `jwt.sub`, applicable to any claim in the token payload.

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n agentgateway-system jwt-claim-rate-limit
```

Create a tier-based rate limit config with two value descriptors under the `plan` key:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: tier-plan-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: plan
      value: standard
      rateLimit:
        unit: MINUTE
        requestsPerUnit: 20
    - key: plan
      value: premium
      rateLimit:
        unit: MINUTE
        requestsPerUnit: 200
    rateLimits:
    - actions:
      - cel:
          expression: 'jwt.plan'
          key: "plan"
      type: TOKEN
EOF
```

Update the `EnterpriseAgentgatewayPolicy` to reference the new config. JWT validation is required so that `jwt.plan` is available to the CEL expression:
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-based-rate-limit
  namespace: agentgateway-system
spec:
  targetRefs:
    - name: agentgateway-proxy
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: workshop.solo.io
          jwks:
            inline: |
                {
                  "keys": [
                    {
                      "kty": "RSA",
                      "kid": "workshop-jwt-key-001",
                      "use": "sig",
                      "alg": "RS256",
                      "n": "x0WUh5Pyx5CS9piu7QPMtaB7d2cJPDhV1DJVOwdTOVi39g1eP0it1TKJ4kSvEWsAc-L1KOTsTjfEGNUfIdKfPk8E8_vY3JHBBrN1pg0iwEX31xGdAGOGkGks-oT5Ois2MXlHzMYz2Hhok0GfUTPc2W8V4_POexx-Kpsyac_6_V2mbsHy9W1jUBrVaaC0t8SeFuxeE39Huzys9moCN4dMfMOy18svga06aGtAbTo_MVtVthGXU_Bwe3GWSCOL62E2f8C4XHSo-9ttte-pqgjLYSnz9vUvYp4zSUMqQtZ-XVZ-n26XZNVIBDtB23hBlC8KHmDnMh5yZ2Ye2A7a5uXFNw",
                      "e": "AQAB"
                    }
                  ]
                }
    entRateLimit:
      global:
        rateLimitConfigRefs:
        - name: tier-plan-rate-limit
EOF
```

### Mint tier tokens

Mint tokens for both personas. The `plan` claim in each file determines which descriptor value the gateway matches:
```bash
export STANDARD_TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/standard-user.json)
export PREMIUM_TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/premium-user.json)
```

Decode the standard token to confirm the `plan` claim:
```bash
echo "$STANDARD_TOKEN" | cut -d. -f2 \
  | awk '{n=length($0)%4; if(n==2)pad="=="; else if(n==3)pad="="; else pad=""; print $0 pad}' \
  | tr '_-' '/+' | base64 -d 2>/dev/null; echo
```
```json
{"iss":"workshop.solo.io","sub":"standard-user","exp":4070908800,"plan":"standard"}
```

### Test tier-based rate limiting

Send requests as the standard user (20-token/minute budget). The prompt `"hi"` uses approximately 1 input token:
```bash
for i in $(seq 1 25); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer $STANDARD_TOKEN" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}' \
    "$GATEWAY_IP:8080/openai")
  echo "Request $i: HTTP $STATUS"
done
```
You will see `200` responses until the standard user's 20-token budget is exhausted, then `429 Too Many Requests`.

While the standard user is rate limited, the premium user's counter is completely independent — switch tokens to confirm the premium budget is untouched:
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $PREMIUM_TOKEN" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "hi"
      }
    ]
  }'
```
The premium user receives `200`. Each descriptor value (`plan=standard`, `plan=premium`) maintains its own Redis counter — exhausting one plan tier leaves all others unaffected.

## Configure IP-based token limiting

All previous sections required an `Authorization` header — unauthenticated requests were rejected by JWT validation before reaching the rate limiter. IP-based limiting is different: it fires at the network level **before** any identity check, so it applies to all traffic from a source address regardless of authentication status.

This is useful for protecting publicly-facing endpoints from unauthenticated token consumption (prompt injection scans, credential stuffing, etc.) — the gateway enforces the budget before it even knows who the caller is.

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n agentgateway-system tier-plan-rate-limit
```

Create an IP-based rate limit config keyed on `source.address`:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: ip-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: remote_address
      rateLimit:
        requestsPerUnit: 20
        unit: MINUTE
    rateLimits:
    - actions:
      - cel:
          expression: 'source.address'
          key: "remote_address"
      type: TOKEN
EOF
```

Update the `EnterpriseAgentgatewayPolicy` — remove JWT authentication so unauthenticated requests can reach the rate limiter:
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-based-rate-limit
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
        - name: ip-rate-limit
EOF
```

### Test IP-based rate limiting

Send requests without any `Authorization` header. The IP rate limiter fires on unauthenticated traffic:
```bash
for i in $(seq 1 25); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "content-type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}' \
    "$GATEWAY_IP:8080/openai")
  echo "Request $i: HTTP $STATUS"
done
```
You will see `200` responses until the 20-token budget for your source IP is exhausted, then `429 Too Many Requests` — with no `Authorization` header in any request.

Adding an `Authorization` header to a subsequent request does not bypass the counter — the budget is tracked by source IP, not by identity. The IP limit and identity-based limits are independent: you can stack both in the same policy to enforce a network-level ceiling alongside per-user quotas.

## Configure mixed time windows (burst + sustained)

A common quota pattern is a short-window burst limit (e.g. 20 tokens/minute) combined with a longer sustained limit (e.g. 50 tokens/hour). A user can burst up to the per-minute cap, but the hourly counter keeps accumulating — so even after the minute resets, they'll eventually hit the hourly ceiling and stay throttled until the hour rolls over.

The key implementation detail: this requires **two separate `RateLimitConfig` objects**, each listed in `rateLimitConfigRefs`. Both are checked independently on every request — a request is denied if either fires. Putting two `type: TOKEN` entries inside a single `RateLimitConfig` silently fails (neither limit fires), so the split-config approach is the correct pattern.

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n agentgateway-system ip-rate-limit
```

Create two configs — one per time window:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: rl-minute
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: jwt_sub_minute
      rateLimit:
        unit: MINUTE
        requestsPerUnit: 20
    rateLimits:
    - actions:
      - cel:
          expression: 'jwt.sub'
          key: "jwt_sub_minute"
      type: TOKEN
---
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: rl-hour
  namespace: agentgateway-system
spec:
  raw:
    descriptors:
    - key: jwt_sub_hour
      rateLimit:
        unit: HOUR
        requestsPerUnit: 50
    rateLimits:
    - actions:
      - cel:
          expression: 'jwt.sub'
          key: "jwt_sub_hour"
      type: TOKEN
EOF
```

Update the `EnterpriseAgentgatewayPolicy` to reference both configs. Both are checked on every request — whichever fires first wins:
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: token-based-rate-limit
  namespace: agentgateway-system
spec:
  targetRefs:
    - name: agentgateway-proxy
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: workshop.solo.io
          jwks:
            inline: |
                {
                  "keys": [
                    {
                      "kty": "RSA",
                      "kid": "workshop-jwt-key-001",
                      "use": "sig",
                      "alg": "RS256",
                      "n": "x0WUh5Pyx5CS9piu7QPMtaB7d2cJPDhV1DJVOwdTOVi39g1eP0it1TKJ4kSvEWsAc-L1KOTsTjfEGNUfIdKfPk8E8_vY3JHBBrN1pg0iwEX31xGdAGOGkGks-oT5Ois2MXlHzMYz2Hhok0GfUTPc2W8V4_POexx-Kpsyac_6_V2mbsHy9W1jUBrVaaC0t8SeFuxeE39Huzys9moCN4dMfMOy18svga06aGtAbTo_MVtVthGXU_Bwe3GWSCOL62E2f8C4XHSo-9ttte-pqgjLYSnz9vUvYp4zSUMqQtZ-XVZ-n26XZNVIBDtB23hBlC8KHmDnMh5yZ2Ye2A7a5uXFNw",
                      "e": "AQAB"
                    }
                  ]
                }
    entRateLimit:
      global:
        rateLimitConfigRefs:
        - name: rl-minute
        - name: rl-hour
EOF
```

### Phase 1: exhaust the per-minute burst

Reuse the analyst token from the JWT section:
```bash
export USER_TOKEN=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/analyst.json)
```

Send requests until you hit `429` — the 20-token/minute bucket is exhausted:
```bash
for i in $(seq 1 25); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}' \
    "$GATEWAY_IP:8080/openai")
  echo "Request $i: HTTP $STATUS"
done
```
After a few `200` responses you'll start seeing `429` — the per-minute limit fired. Note how many requests succeeded before the cutoff.

### Phase 2: minute resets, hour limit takes over

Wait 65 seconds for the minute window to roll over, then send more requests:
```bash
echo "Waiting for minute window to reset..."
sleep 65

for i in $(seq 1 10); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}' \
    "$GATEWAY_IP:8080/openai")
  echo "Request $i: HTTP $STATUS"
done
```

You'll see `200` responses again briefly — the minute bucket has reset. But the hourly counter never stopped: tokens from Phase 1 are still counted against the 50-token/hour budget, so you'll hit `429` again sooner than Phase 1 did. The two limits operate on independent Redis counters with independent windows; a request is denied when **either** fires.

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system token-based-rate-limit
kubectl delete rlc -n agentgateway-system \
  token-based-rate-limit \
  openai-rate-limit \
  multi-header-rate-limit \
  jwt-claim-rate-limit \
  tier-plan-rate-limit \
  ip-rate-limit \
  rl-minute \
  rl-hour \
  2>/dev/null || true
```