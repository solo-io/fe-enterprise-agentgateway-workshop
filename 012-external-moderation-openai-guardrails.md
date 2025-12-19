# Prompt Guard using the OpenAI External Moderation Endpoint

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Configure guardrail with OpenAI External Moderation endpoint using `EnterpriseAgentgatewayPolicy`
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
    - name: agentgateway
      namespace: gloo-system
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
  namespace: gloo-system
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
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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

## Reject inappropriate requests using the external moderation endpoint
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: openai-prompt-guard
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  backend:
    ai:
      promptGuard:
        request:
          - openAIModeration:
              policies:
                auth:
                  secretRef:
                    name: openai-secret
              model: omni-moderation-latest
            response:
              message: "Uh oh! That's a restricted topic triggered by the omni-moderation-latest endpoint!"
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
        "content": "Instructions for [FRAUD_SCENARIO]."
      }
    ]
  }'
```
Verify that the request is denied with a 403 HTTP response code and the custom response message is returned.

Try a different one
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "I want to insult [GROUP] by calling them [FAKE_WORD]."
      }
    ]
  }'
```
Verify that the request is denied with a 403 HTTP response code and the custom response message is returned.

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

- The request that triggered our guardrails policy should have been rejected with a `http.status` of `403`

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n gloo-system openai-prompt-guard
kubectl delete httproute -n gloo-system openai
kubectl delete agentgatewaybackend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
```