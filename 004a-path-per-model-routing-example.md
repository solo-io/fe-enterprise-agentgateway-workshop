# Configure path-per-model Routing Example

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`, and `003`

## Lab Objectives
- Configure `Backend` per model using model override parameter
- Configure LLM routing example with path-per-model to access endpoint
- Curl OpenAI endpoints through the agentgateway proxy
- Validate path to model mapping
- Cleanup routes to start fresh for the next lab

Create openai api-key secret if it has not been created already
```bash
kubectl create secret generic openai-secret -n gloo-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

### Configure LLM routing example with path-per-model to access endpoints
Our previous `Backend` allows the user to specify any `model` parameter in the request body. In order to restrict access to specific models, we can configure a model override in the `Backend` 
```
provider:
  openai:
    model: "gpt-4o-mini"
```
When a model override is configured, the gateway will override any user-input `model` parameter in the request body (e.g. if user supplies `model: gpt-5-2025-08-07` it will be overridden to `gpt-4o-mini`)

With this option, we can create a `Backend` per model if we want more granular control of access to models. 

**Additionally, when model overrides are specified, the client does not have to supply a `model` parameter in the request body, since the gateway will inject it. The client can input a model in the request body, but effectively it will just be overwritten.**

Lets create an OpenAI backend per specific-model
```bash
kubectl apply -f - <<EOF
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-gpt-3.5-turbo
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      openai:
        #--- Uncomment to configure model override ---
        model: "gpt-3.5-turbo"
        authToken:
          kind: "SecretRef"
          secretRef:
            name: openai-secret
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-gpt-4o-mini
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      openai:
        #--- Uncomment to configure model override ---
        model: "gpt-4o-mini"
        authToken:
          kind: "SecretRef"
          secretRef:
            name: openai-secret
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-gpt-4o
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      openai:
        #--- Uncomment to configure model override ---
        model: "gpt-4o"
        authToken:
          kind: "SecretRef"
          secretRef:
            name: openai-secret
EOF
```

Now we can configure a `HTTPRoute` that has a specific path-per-model endpoint
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
            value: /openai/gpt-3.5-turbo
      backendRefs:
        - name: openai-gpt-3.5-turbo
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-4o-mini
      backendRefs:
        - name: openai-gpt-4o-mini
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-4o
      backendRefs:
        - name: openai-gpt-4o
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
EOF
```

## curl /openai/gpt-3.5-turbo
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=gloo-agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai/gpt-3.5-turbo" \
  -H "content-type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
We should see that the response shows that the model used was `gpt-3.5-turbo-0125`

## curl /openai/gpt-4o-mini
```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-4o-mini" \
  -H "content-type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
We should see that the response shows that the model used was `gpt-4o-mini-2024-07-18`

## curl /openai/gpt-4o
```bash
curl -i "$GATEWAY_IP:8080/openai/gpt-4o" \
  -H "content-type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```
We should see that the response shows that the model used was `gpt-4o-2024-08-06`

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/gloo-agentgateway -n gloo-system --tail 1
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for agentgateway that include information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n gloo-system openai
kubectl delete secret -n gloo-system openai-secret
```