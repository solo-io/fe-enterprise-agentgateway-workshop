# Configure Basic Routing to OpenAI

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure RateLimitConfig
- Validate basic rate limiting

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


curl $GATEWAY_IP:8080/openai -H "content-type: application/json" -d'{
"model": "gpt-4o-mini",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```

Create rate limit config
```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: per-user-counter-minute
  namespace: gloo-system
spec:
  raw:
    descriptors:
    - key: generic_key
      value: counter
      rateLimit:
        requestsPerUnit: 10
        unit: MINUTE
    rateLimits:
    - actions:
      - genericKey:
          descriptorValue: counter
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
        name: per-user-counter-minute
EOF
```

## Check access logs

- Check the logs of the proxy for access log information

```bash
kubectl logs -n gloo-system deploy/gloo-agentgateway -f
```

We should see access log information about our LLM request
```
2025-09-03T23:28:43.168548Z     info    request gateway=gloo-system/gloo-agentgateway listener=http route=gloo-system/openai endpoint=api.openai.com:443 src.addr=10.42.0.1:29683 http.method=POST http.host=192.168.107.2 http.path=/openai http.version=HTTP/1.1 http.status=200 llm.provider=openai llm.request.model=gpt-3.5-turbo llm.request.tokens=12 llm.response.model=gpt-3.5-turbo-0125 llm.response.tokens=16 duration=947ms
```

## curl openai
```bash
curl $GATEWAY_IP:8080/openai -H "content-type: application/json" -d'{
"model": "gpt-4o-mini",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```

## Cleanup
```bash
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
kubectl delete glootrafficpolicy -n gloo-system token-based-rate-limit
kubectl delete rlc -n gloo-system per-user-counter-minute
```