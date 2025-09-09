# Configure JWT Auth for our OpenAI Route

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure JWT Auth
- Validate JWT Auth

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n gloo-system \
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
  namespace: gloo-system
spec:
  parentRefs:
    - name: gloo-agentgateway
      namespace: gloo-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai-all-models
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-all-models
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      provider:
        openai:
          #--- Uncomment to configure model override ---
          #model: ""
          authToken:
            kind: "SecretRef"
            secretRef:
              name: openai-secret
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=gloo-agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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

Create Gloo traffic policy
```bash
kubectl apply -f- <<EOF
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: agentgateway-jwt-auth
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: gloo-agentgateway
  glooJWT:
    beforeExtAuth:
      providers:
        selfminted:
          issuer: https://dev.example.com
          jwks:
            local:
              key: |
                -----BEGIN PUBLIC KEY-----
                MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwrqLvf76rkErpNyvlYs4
                U8dq/2hcaMSRXXrFD38KQ3S/5ciXWn3+0w/bGvY2w0/9tBTVZmGnWj3vLiWHRAer
                NtvBHRUKE/c1AqRJ1RiPdPpQodUsS/ZK7BNDey250ZfsyU94EX/zZ4sROh5EGE1Y
                3+p860H8DLEofeTepKmHRu6yEuZl4GscbEg5+Bjb+k/LVW+UQCSQqkOyHxVwrrt2
                6gmKtWqW7/L9jZclmW+J5Jn+/7DUo5QkXxTIM4C9/01XA1ibWkyMhAx9wyZCFIKA
                rdmgZcqjWdsMfmRbwJGRst2658MwIZ3skYGTd8LiUTWnxTRpQ5TJoSzck4w8k+0l
                LwIDAQAB
                -----END PUBLIC KEY-----
EOF
```

Make a curl request to the OpenAI endpoint again (without a JWT), this time it should fail
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
Verify that the request is denied with a 403 HTTP response code 

## curl with valid JWT token
```bash
export DEV_TOKEN_1="eyJhbGciOiJSUzI1NiIsImtpZCI6ImpsOElCZTFYMjB6NF9JdEZpUEg3VEI5RF8tQmRXN1pRT2Fwa2t2ZFdpUWc9IiwidHlwIjoiSldUIn0.eyJpc3MiOiJodHRwczovL2Rldi5leGFtcGxlLmNvbSIsImV4cCI6NDgwNDMyNDczNiwiaWF0IjoxNjQ4NjUxMTM2LCJvcmciOiJpbnRlcm5hbCIsImVtYWlsIjoiZGV2MUBzb2xvLmlvIiwiZ3JvdXAiOiJlbmdpbmVlcmluZyIsInNjb3BlIjoiaXM6ZGV2ZWxvcGVyIn0.BVLsWoLObIf8r19HxEg6yOdqHrZ9WDRJOc-t9VmkluenLdwbMu2uQNLY_RkZApEAeylb00oZnmxa4wCAXNcTjbF6f6_TZgXE5pFZU1CdTKOB2b7bVlNToKFuJJBnqWJ7-bkRQEC5BptASR4bIK_E-sOHrfyXk7NG7ocPB6xqSDIYBRdUpWNJbyRemyhFfyOJ1j8pTR9CwmgrG9ROGSGT_ucXrmY7SzKbuFQtjA14wVQEWBlnFTori8TtfSiP6okkcCEiQE8u6nQ_J5NOJYbEKVkFAzSZJlICqsnMS9q5AXVQ2pDUo18eqjyGT2EfbWBgHK-ZC5DGn-9pU5OJ56AhTQ"

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $DEV_TOKEN_1" \
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

We should see that we get a response from the backend LLM when JWT is provided
```
{"id":"chatcmpl-CDwyrpA4JiYZtZqykYZoH6a4Ea7hL","choices":[{"index":0,"message":{"content":"I don't have personal preferences, but one widely admired poem is \"The Road Not Taken\" by Robert Frost. It explores themes of choice, individuality, and the paths we take in life. Many find its reflective nature and imagery to be profound. If you're interested, I can provide an analysis or discuss its themes!","role":"assistant"},"finish_reason":"stop"}],"created":1757441021,"model":"gpt-4o-mini-2024-07-18","service_tier":"default","system_fingerprint":"fp_8bda4d3a2c","object":"chat.completion","usage":{"prompt_tokens":12,"completion_tokens":63,"total_tokens":75,"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0},"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0}}}
```

## Port-forward to Jaeger UI
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for our recent requests

- The request without an JWT should have been rejected with a `http.status` of `403` and an `error` with `authentication failure: no bearer token found`
- The request with a JWT should be successful and you should see information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
kubectl delete glootrafficpolicy -n gloo-system agentgateway-jwt-auth
```