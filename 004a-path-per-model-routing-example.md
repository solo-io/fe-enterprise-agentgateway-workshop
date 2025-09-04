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
      provider:
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
      provider:
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
      provider:
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


curl $GATEWAY_IP:8080/openai/gpt-3.5-turbo -H "content-type: application/json" -d'{
"model": "",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```
We should see that the response shows that the model used was `gpt-3.5-turbo-0125`

## curl /openai/gpt-4o-mini
```bash
curl $GATEWAY_IP:8080/openai/gpt-4o-mini -H "content-type: application/json" -d'{
"model": "",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```
We should see that the response shows that the model used was `gpt-4o-mini-2024-07-18`

## curl /openai/gpt-4o
```bash
curl $GATEWAY_IP:8080/openai/gpt-4o -H "content-type: application/json" -d'{
"model": "",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```
We should see that the response shows that the model used was `gpt-4o-2024-08-06`

# Check access logs

- Check the logs of the proxy for access log information

```bash
kubectl logs -n gloo-system deploy/gloo-agentgateway -f
```
We should see access log information about our LLM requests such as `http.path` and `llm.response.model`

## Cleanup
```bash
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-gpt-3.5-turbo
kubectl delete backend -n gloo-system openai-gpt-4o
kubectl delete backend -n gloo-system openai-gpt-4o-mini
kubectl delete secret -n gloo-system openai-secret
```