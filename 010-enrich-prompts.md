# Prompt Enrichment

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Add prompt enrichment policy
- Validate the request went through the gateway in Jaeger UI, and that the prompt has been enriched

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

## Apply prompt enrichment policy
```bash
kubectl apply -f- <<EOF
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: openai-opt
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptEnrichment:
      prepend:
      - role: SYSTEM
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

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/gloo-agentgateway -n gloo-system --tail 1
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see our system prompt was injected in the `gen_ai.prompt` tag
```
{
  "key": "gen_ai.prompt",
  "type": "string",
  "value": "[{\"content\": \"Return the response in JSON format\", \"role\": \"user\"}, {\"role\": \"user\", \"content\": \"Whats your favorite poem?\"}]"
}
```

We should also see that the `gen_ai.completion` tag shows the response was returned in JSON
```
{
  "key": "gen_ai.completion",
  "type": "string",
  "value": "[{\"role\": \"assistant\", \"content\": \"```json\\n{\\n  \\\"favorite_poem\\\": {\\n    \\\"title\\\": \\\"The Road Not Taken\\\",\\n    \\\"author\\\": \\\"Robert Frost\\\",\\n    \\\"summary\\\": \\\"The poem explores the theme of choices and their consequences, using the metaphor of a fork in the road to illustrate the decisions we face in life.\\\"\\n  }\\n}\\n```\"}]"
}
```

## Cleanup
```bash
kubectl delete glootrafficpolicy -n gloo-system openai-opt
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
```