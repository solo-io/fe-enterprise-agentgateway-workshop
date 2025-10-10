# Configure Input Token Based Rate Limiting

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Create an initial RateLimitConfig to implement token-based rate limiting (input tokens) using a simple counter (e.g. all users get 10 tokens per hour)
- Validate token-based rate limiting

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

## Configure global request rate limit of 10 input tokens per hour
Create rate limit config, note that this policy uses `type: TOKEN`
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: token-based-rate-limit
  namespace: gloo-system
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

Create GlooTrafficPolicy referencing the rate limit config we just created
```bash
kubectl apply -f- <<EOF
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: token-based-rate-limit
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: gloo-agentgateway
  glooRateLimit:
    global:
      rateLimitConfigRef:
        name: token-based-rate-limit
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

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/gloo-agentgateway -n gloo-system --tail 1
```

Example output, you should see that the `http.status=429`
```
2025-09-24T18:24:36.916204Z     info    request gateway=gloo-system/gloo-agentgateway listener=http route=gloo-system/openai src.addr=10.42.0.1:41015 http.method=POST http.host=192.168.107.2 http.path=/openai http.version=HTTP/1.1 http.status=429 trace.id=0e107053e94f6759febedbd0992c95ce span.id=414ed2d771f63b5a duration=0ms
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for our recent requests

- The rate limited requests should have been rejected with a `http.status` of `429`


## Cleanup
```bash
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
kubectl delete glootrafficpolicy -n gloo-system token-based-rate-limit
kubectl delete rlc -n gloo-system token-based-rate-limit
```