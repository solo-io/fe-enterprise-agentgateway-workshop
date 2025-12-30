# Prompt Enrichment

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Add prompt enrichment policy using `EnterpriseAgentgatewayPolicy`
- Validate the request went through the gateway in Jaeger UI, and that the prompt has been enriched

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

## Apply prompt enrichment policy
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: openai-opt
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  backend:
    ai:
      prompt:
        prepend:
          - role: system
            content: "Return the response in JSON format"
EOF
```

## curl openai
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

We should see that the response was returned in JSON format
```
{"id":"chatcmpl-CJ1ZTRQCOuIwe8yiaRnpLPDyvE4QX","choices":[{"index":0,"message":{"content":"```json\n{\n  \"favorite_poem\": {\n    \"title\": \"The Road Not Taken\",\n    \"author\": \"Robert Frost\",\n    \"summary\": \"The poem explores the theme of choices and their consequences, using the metaphor of a fork in the road to illustrate the decisions we face in life.\"\n  }\n}\n```","role":"assistant"},"finish_reason":"stop"}],"created":1758650307,"model":"gpt-4o-mini-2024-07-18","service_tier":"default","system_fingerprint":"fp_560af6e559","object":"chat.completion","usage":{"prompt_tokens":22,"completion_tokens":67,"total_tokens":89,"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0},"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0}}}
```

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n enterprise-agentgateway openai-opt
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret
```