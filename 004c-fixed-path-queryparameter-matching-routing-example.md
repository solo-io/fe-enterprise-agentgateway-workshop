# Configure Fixed Path + Query Parameter Matching Routing Example

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`, and `003`

## Lab Objectives
- Configure LLM routing example with fixed-path + queryparameter matcher to access endpoint
- Curl OpenAI endpoints through the agentgateway proxy
- Validate path to model mapping
- Cleanup routes to start fresh for the next lab

Create openai api-key secret if it has not been created already
```bash
kubectl create secret generic openai-secret -n gloo-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

### Configure LLM routing example with fixed path + header matching to access endpoints

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
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-3.5-turbo
      backendRefs:
        - name: openai-gpt-3.5-turbo
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-4o-mini
      backendRefs:
        - name: openai-gpt-4o-mini
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /openai
          queryParams:
          - type: Exact
            name: model
            value: gpt-4o
      backendRefs:
        - name: openai-gpt-4o
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
EOF
```

## curl /openai with the "model=gpt-3.5-turbo" query parameter
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=gloo-agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')


curl "$GATEWAY_IP:8080/openai?model=gpt-3.5-turbo" -H "content-type: application/json" -d'{
"model": "",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```
We should see that the response shows that the model used was `gpt-3.5-turbo-0125`

## curl /openai with the "model=gpt-4o-mini" query parameter
```bash
curl "$GATEWAY_IP:8080/openai?model=gpt-4o-mini" -H "content-type: application/json" -d'{
"model": "",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```
We should see that the response shows that the model used was `gpt-4o-mini-2024-07-18`

## curl /openai with the "model=gpt-4o" query parameter
```bash
curl "$GATEWAY_IP:8080/openai?model=gpt-4o" -H "content-type: application/json" -d'{
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