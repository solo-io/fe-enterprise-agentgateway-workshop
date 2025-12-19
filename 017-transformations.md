# Transformations using Agentgateway

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Configure a basic response transformation using `EnterpriseAgentgatewayPolicy`
- Validate that the transformation occurs
- Extend our example by enriching response headers with additional context for observability and debugging

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

## Apply response transformation
We'll configure a `GlooTrafficPolicy` to capture a request header `x-user-id` and inject it into the response, demonstrating a basic response transformation using CEL expressions. Additionally, if no `x-user-id` is present, we will default to `x-user-id: anonymous`
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: openai-transformation
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - name: agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    transformation:
      response:
        set:
        - name: x-user-id
          value: default(request.headers["x-user-id"], "anonymous")
EOF
```

Make a curl request to the OpenAI endpoint again, this time including the header `x-user-id: bob`
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "x-user-id: bob" \
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

We should see `x-user-id: bob` in the response headers
```
HTTP/1.1 200 OK
date: Wed, 12 Nov 2025 17:40:06 GMT
content-type: application/json
access-control-expose-headers: X-Request-ID
openai-organization: solo-io-1
openai-processing-ms: 2349
openai-project: proj_dK610oHpvC5Kg7qXdeCl4Ou0
openai-version: 2020-10-01
x-envoy-upstream-service-time: 2434
x-ratelimit-limit-requests: 30000
x-ratelimit-limit-tokens: 150000000
x-ratelimit-remaining-requests: 29999
x-ratelimit-remaining-tokens: 149999990
x-ratelimit-reset-requests: 2ms
x-ratelimit-reset-tokens: 0s
x-request-id: req_d75d7669f6ba4e8cb646ee43dc017b5b
x-openai-proxy-wasm: v0.1
cf-cache-status: DYNAMIC
set-cookie: __cf_bm=b4U5IG8WCnjhaNB3o6S_dWbwNmi68o_TFcK22kbEHc4-1762969206-1.0.1.1-rskmmqj8YiHk8nm2RLocKOmqW9IQntusuD2J2c.YNIUN0v3ZxzGt014rY5Cd4y9Iak6Ep2.qxvcV_FGOXa5XJ6hCmS5hnfoOa82oazjr6vM; path=/; expires=Wed, 12-Nov-25 18:10:06 GMT; domain=.api.openai.com; HttpOnly; Secure; SameSite=None
set-cookie: _cfuvid=sXHeueh5Ny5lwXekT6.I_Gw948BzGfAJiIXe.v5iXhw-1762969206917-0.0.1.1-604800000; path=/; domain=.api.openai.com; HttpOnly; Secure; SameSite=None
strict-transport-security: max-age=31536000; includeSubDomains; preload
x-content-type-options: nosniff
server: cloudflare
cf-ray: 99d7cff76cedc366-SEA
alt-svc: h3=":443"; ma=86400
x-user-id: bob
content-length: 869
```

Note that if you test the curl request again without the `x-user-id` header we should see the response header `x-user-id: anonymous`

### Extending our example
Now that we’ve validated a basic header transformation, let’s enrich the response metadata to provide more observability and traceability. In this step, we’ll enhance the response by adding additional headers that tell us who sent the request, what model was used, and how it was routed

Here is the expected behavior of the transformation policy below
- add `x-user-id` to capture the user identifier (defaulting to `anonymous` if missing)
- add `x-llm-provider` to confirm which backend handled the request
- add `x-llm-request-model` to capture what model was requested
- add `x-request-method` for API behavior analysis
- add `x-request-path` to help distinguish which route processed the call

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: openai-transformation
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - name: agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    transformation:
      response:
        set:
          - name: x-user-id
            value: default(request.headers["x-user-id"], "anonymous")
          - name: x-llm-provider
            value: llm.provider
          - name: x-llm-request-model
            value: llm.requestModel
          - name: x-request-method
            value: request.method
          - name: x-request-path
            value: request.path
EOF
```

Make a curl request again
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

We should see the response headers we applied in the transformation policy
```
x-user-id: anonymous
x-llm-provider: openai
x-llm-request-model: gpt-4o-mini
x-request-method: POST
x-request-path: /openai
```

These fields make debugging and observability far easier — especially in multi-model or multi-provider setups — and can later be leveraged for tracing, rate limiting, or chargeback use cases.

## Port-forward to Grafana UI to view traces
Default credentials are admin:prom-operator
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:3000 or http://localhost:16686 in your browser, you should be able to see traces for our recent requests

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n enterprise-agentgateway openai-transformation
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret
```