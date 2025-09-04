# Basic Guardrails using Agentgateway

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure built-in guardrails
- Validate guardrails are enforced

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

## Reject inappropriate requests
```bash
kubectl apply -f- <<EOF
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: openai-prompt-guard
  namespace: gloo-system
  labels:
    app: ai-gateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      request:
        customResponse:
          message: "Rejected due to inappropriate content"
        regex:
          action: REJECT
          matches:
          - pattern: "credit card"
            name: "CC"
EOF
```

Make a curl request to the OpenAI endpoint again, this time it should fail
```bash
curl $GATEWAY_IP:8080/openai -H "content-type: application/json" -d'{
"model": "gpt-4o-mini",
"messages": [
  {
    "role": "user",
    "content": "Can you give me some examples of Master Card credit card numbers?"
  }
]}'
```
Verify that the request is denied with a 403 HTTP response code and the custom response message is returned.

## Check access logs

- Check the logs of the proxy for access log information

```bash
kubectl logs -n gloo-system deploy/gloo-agentgateway -f
```

We should see access log information about our LLM request
```
2025-09-04T05:45:12.290026Z     info    request gateway=gloo-system/gloo-agentgateway listener=http route=gloo-system/openai endpoint=api.openai.com:443 src.addr=10.42.0.1:42865 http.method=POST http.host=192.168.107.2 http.path=/openai http.version=HTTP/1.1 http.status=403 duration=0ms
```

## Mask inappropriate responses (NOT WORKING)
To avoid information from being leaked, we can also configure a prompt guard on the response to mask sensitive information such as credit cards, SSN, and other types of PII data

```bash
kubectl apply -f- <<EOF
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: openai-prompt-guard
  namespace: gloo-system
  labels:
    app: ai-gateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      response:
        regex:
          action: MASK
          builtins:
          - CREDIT_CARD
EOF
```

## curl openai
```bash
curl $GATEWAY_IP:8080/openai -H "content-type: application/json" -d'{
"model": "gpt-4o-mini",
"messages": [
  {
    "role": "user",
    "content": "What type of number is 5105105105105100?"
  }
]}'
```

## Cleanup
```bash
kubectl delete trafficpolicy -n gloo-system openai-prompt-guard
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
```