# Configure Input Token Based Rate Limiting

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Create an initial RateLimitConfig to implement token-based rate limiting (input tokens) using a simple counter (e.g. all users get 10 tokens per hour)
- Validate token-based rate limiting

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
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
        - name: openai-all-models
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: enterprise-agentgateway
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
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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

## Configure global token-based rate limit of 10 input tokens per hour
Create rate limit config, note that this policy uses `type: TOKEN`
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: token-based-rate-limit
  namespace: enterprise-agentgateway
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
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - name: agentgateway
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
Now let's configure a rate limit based on a custom header (X-User-ID) instead of a generic counter. This allows different users to have their own rate limit quotas.

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n enterprise-agentgateway token-based-rate-limit
```

Create a header-based rate limit config:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: openai-rate-limit
  namespace: enterprise-agentgateway
spec:
  raw:
    descriptors:
    - key: X-User-ID
      rateLimit:
        unit: MINUTE
        requestsPerUnit: 100
    rateLimits:
    - actions:
      - requestHeaders:
          descriptorKey: "X-User-ID"
          headerName: "X-User-ID"
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
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - name: agentgateway
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
Now let's configure rate limiting based on multiple headers. This creates a composite key where the rate limit applies to the combination of both header values (e.g., user-123 in tenant-A has a separate quota from user-123 in tenant-B).

First, delete the previous rate limit config:
```bash
kubectl delete rlc -n enterprise-agentgateway openai-rate-limit
```

Create a multi-header rate limit config that limits based on both X-User-ID and X-Tenant-ID:
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: multi-header-rate-limit
  namespace: enterprise-agentgateway
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
      - requestHeaders:
          descriptorKey: "X-User-ID"
          headerName: "X-User-ID"
      - requestHeaders:
          descriptorKey: "X-Tenant-ID"
          headerName: "X-Tenant-ID"
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
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - name: agentgateway
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

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret
kubectl delete enterpriseagentgatewaypolicy -n enterprise-agentgateway token-based-rate-limit
kubectl delete rlc -n enterprise-agentgateway token-based-rate-limit openai-rate-limit multi-header-rate-limit
```