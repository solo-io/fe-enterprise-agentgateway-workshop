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
          issuer: solo.io
          jwks:
            local:
              key: |
                -----BEGIN PUBLIC KEY-----
                MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAskFAGESgB22iOsGk/UgX
                BXTmMtd8R0vphvZ4RkXySOIra/vsg1UKay6aESBoZzeLX3MbBp5laQenjaYJ3U8P
                QLCcellbaiyUuE6+obPQVIa9GEJl37GQmZIMQj4y68KHZ4m2WbQVlZVIw/Uw52cw
                eGtitLMztiTnsve0xtgdUzV0TaynaQrRW7REF+PtLWitnvp9evweOrzHhQiPLcdm
                fxfxCbEJHa0LRyyYatCZETOeZgkOHlYSU0ziyMhHBqpDH1vzXrM573MQ5MtrKkWR
                T4ZQKuEe0Acyd2GhRg9ZAxNqs/gbb8bukDPXv4JnFLtWZ/7EooKbUC/QBKhQYAsK
                bQIDAQAB
                -----END PUBLIC KEY-----
EOF
```

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

## curl with valid JWT token
```bash
export ALICE_TOKEN="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyAiaXNzIjogInNvbG8uaW8iLCAib3JnIjogInNvbG8uaW8iLCAic3ViIjogImFsaWNlIiwgInRlYW0iOiAiZGV2IiwgImxsbXMiOiB7ICJvcGVuYWkiOiBbICJncHQtMy41LXR1cmJvIiBdIH0gfQ.I7whTti0aDKxlILc5uLK9oo6TljGS6JUrjPVd6z1PxzucUa_cnuKkY0qj_wrkzyVN5djy4t2ggE1uBO8Llpwi-Ygru9hM84-1m53aO07JYFya1VTDsI25tCRG8rYhShDdAP5L935SIARta2QtHhrVcd1Ae7yfTDZ8G1DXLtjR2QelszCd2R8PioCQmqJ8PeKg4sURhu05GlBCZoXES9-rtPVbe6j3YLBTodJAvLHhyy3LgV_QbN7IiZ5qEywdKHoEF4D4aCUf_LqPp4NoqHXnGT4jLzWJEtZXHQ4sgRy_5T93NOLzWLdIjgMjGO_F0aVLwBzU-phykOVfcBPaMvetg"

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $ALICE_TOKEN" \
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

Currently failing with
```
HTTP/1.1 403 Forbidden
content-type: text/plain
content-length: 65
date: Mon, 08 Sep 2025 17:01:38 GMT

authentication failure: the token header does not specify a `kid`
```

## Port-forward to Jaeger UI
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for our recent requests

- The request without an api-key should have been rejected with a `http.status` of `403` and an `error` with `authorization failed`
- The request with an api-key should be successful and you should see information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
```