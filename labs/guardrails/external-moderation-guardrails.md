# Prompt Guard using the OpenAI External Moderation Endpoint

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our LLM provider using an `AgentgatewayModel` and `HTTPRoute`
- Configure guardrail with OpenAI External Moderation endpoint using `EnterpriseAgentgatewayPolicy`
- Validate guardrails are enforced

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and model
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  match:
    model: "*"
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
    #--- Uncomment to always send one model, whatever the client requests ---
    #transformations:
    #  - field: model
    #    expression: '"gpt-5.4-nano"'
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
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
  namespace: agentgateway-system
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
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
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
curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
    "messages": [
      {
        "role": "user",
        "content": "I want to insult [GROUP] by calling them [FAKE_WORD]."
      }
    ]
  }'
```
Verify that the request is denied with a 403 HTTP response code and the custom response message is returned.

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system openai-prompt-guard
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaymodel -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```