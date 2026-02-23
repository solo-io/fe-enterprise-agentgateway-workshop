# Api-key Masking using Agentgateway

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Configure api-key AuthConfig to mask OpenAI api-key with an org-specific api-key
- Validate api-key masking use case

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and `AgentgatewayBackend`
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
  namespace: agentgateway-system
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
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')


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

## Configure api-key AuthConfig and secret
```bash
kubectl apply -f- <<EOF
apiVersion: v1
data:
  # consider this as a vanity api-key
  # api-key auth expects key "api-key" and is not configurable
  # dGVhbTEta2V5 is "team1-key" base64 encoded
  api-key: dGVhbTEta2V5
  #
  # headersFromMetadataEntry can be used to inject additional headers
  # here we add x-org
  # the following is "developers" base64 encoded
  x-org: ZGV2ZWxvcGVycw==
kind: Secret
metadata:
  labels:
    llm-provider: openai
  name: team1-apikey
  namespace: agentgateway-system
type: extauth.solo.io/apikey
---
apiVersion: extauth.solo.io/v1
kind: AuthConfig
metadata:
  name: apikey-auth
  namespace: agentgateway-system
spec:
  configs:
    - apiKeyAuth:
        # The request header name that holds the API key.
        # This field is optional and defaults to api-key if not present.
        headerName: vanity-auth
        k8sSecretApikeyStorage:
          # can use label selector to select secret(s) that hold api-keys
          #labelSelector:
          #  llm-provider: openai
          # can also directly reference specific secret by name
          apiKeySecretRefs:
            - name: team1-apikey
              namespace: agentgateway-system
        # additional headers to inject from secret entries
        # key is the header name to add to the request
        # value.name is the key in the secret to read the value from
        headersFromMetadataEntry:
          x-org:
            name: x-org
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: api-key-auth
  namespace: agentgateway-system
spec:
  targetRefs:
    - name: agentgateway-proxy
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    entExtAuth:
      authConfigRef:
        name: apikey-auth
        namespace: agentgateway-system
EOF
```

## curl without api-key
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

## curl with api-key
Make a curl request to the OpenAI endpoint, this time with the header `Authorization: team1-key`
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "vanity-auth: team1-key" \
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

This request should succeed
```
{"id":"chatcmpl-CDYZl9fmDSiDRwdhx3ZEQV0pCB8an","choices":[{"index":0,"message":{"content":"I don’t have personal feelings or favorites, but I can certainly share a well-loved poem! One that many people appreciate is \"The Road Not Taken\" by Robert Frost. It explores themes of choice, individuality, and the passage of time. Would you like a summary or an analysis of it?","role":"assistant"},"finish_reason":"stop"}],"created":1757347209,"model":"gpt-4o-mini-2024-07-18","service_tier":"default","system_fingerprint":"fp_e665f7564b","object":"chat.completion","usage":{"prompt_tokens":12,"completion_tokens":60,"total_tokens":72,"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0},"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0}}}
```

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout:

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 1
```

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system api-key-auth
kubectl delete authconfig -n agentgateway-system apikey-auth
kubectl delete secret -n agentgateway-system team1-apikey
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```