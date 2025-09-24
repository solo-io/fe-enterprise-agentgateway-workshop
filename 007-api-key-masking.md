# Api-key Masking using Agentgateway

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure api-key AuthConfig to mask OpenAI api-key with an org-specific api-key 
- Validate api-key masking use case

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

## Configure api-key AuthConfig and secret
```bash
kubectl apply -f- <<EOF
apiVersion: v1
data:
  api-key: dGVhbTEta2V5
kind: Secret
metadata:
  labels:
    llm-provider: openai
  name: team1-apikey
  namespace: gloo-system
type: extauth.solo.io/apikey
---
apiVersion: extauth.solo.io/v1
kind: AuthConfig
metadata:
  name: apikey-auth
  namespace: gloo-system
spec:
  configs:
    - apiKeyAuth:
        # The request header name that holds the API key.
        # This field is optional and defaults to api-key if not present.
        headerName: authorization
        labelSelector:
          llm-provider: openai
---
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: api-key-auth
  namespace: gloo-system
spec:
  targetRefs:
    - name: gloo-agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  glooExtAuth:
    authConfigRef:
      name: apikey-auth
      namespace: gloo-system
EOF
```

## curl without api-key
Make a curl request to the OpenAI endpoint again, this time it should fail
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
Verify that the request is denied with a 4xx HTTP response code 

## curl with api-key
Make a curl request to the OpenAI endpoint, this time with the header `Authorization: team1-key`
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: team1-key" \
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

This request should succeed
```
{"id":"chatcmpl-CDYZl9fmDSiDRwdhx3ZEQV0pCB8an","choices":[{"index":0,"message":{"content":"I don’t have personal feelings or favorites, but I can certainly share a well-loved poem! One that many people appreciate is \"The Road Not Taken\" by Robert Frost. It explores themes of choice, individuality, and the passage of time. Would you like a summary or an analysis of it?","role":"assistant"},"finish_reason":"stop"}],"created":1757347209,"model":"gpt-4o-mini-2024-07-18","service_tier":"default","system_fingerprint":"fp_e665f7564b","object":"chat.completion","usage":{"prompt_tokens":12,"completion_tokens":60,"total_tokens":72,"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0},"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0}}}
```

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/gloo-agentgateway -n gloo-system --tail 1
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for our recent requests

- The request without an api-key should have been rejected with a `http.status` of `403` and an `error` with `authorization failed`
- The request with an api-key should be successful and you should see information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more


## Cleanup
```bash
kubectl delete glootrafficpolicy -n gloo-system api-key-auth
kubectl delete authconfig -n gloo-system apikey-auth
kubectl delete secret -n gloo-system team1-apikey
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
```