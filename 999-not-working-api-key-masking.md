# Api-key Masking using Agentgateway

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure api-key AuthConfig to mask OpenAI api-key with an org-specific api-key 
- Validate api-key masking use case

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

## Configure api-key AuthConfig and secret
```bash
kubectl apply -f- <<EOF
apiVersion: v1
data:
  api-key: dGVhbTEta2V5
kind: Secret
metadata:
  labels:
    llm-provider: openai
  name: team1-apikey
  namespace: gloo-system
type: extauth.solo.io/apikey
---
apiVersion: extauth.solo.io/v1
kind: AuthConfig
metadata:
  name: apikey-auth
  namespace: gloo-system
spec:
  configs:
    - apiKeyAuth:
        # The request header name that holds the API key.
        # This field is optional and defaults to api-key if not present.
        headerName: authorization
        labelSelector:
          llm-provider: openai
---
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: api-key-auth
  namespace: gloo-system
spec:
  targetRefs:
    - name: gloo-agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  glooExtAuth:
    authConfigRef:
      name: apikey-auth
      namespace: gloo-system
EOF
```

Make a curl request to the OpenAI endpoint again, this time it should fail
```bash
curl $GATEWAY_IP:8080/openai -H "content-type: application/json" -d'{
"model": "gpt-4o-mini",
"messages": [
  {
    "role": "user",
    "content": "Whats your favorite poem?"
  }
]}'
```
Verify that the request is denied with a 4xx HTTP response code 

## Check access logs

- Check the logs of the proxy for access log information

```bash
kubectl logs -n gloo-system deploy/gloo-agentgateway -f
```

We should see access log information about our LLM request
```
2025-09-04T05:45:12.290026Z     info    request gateway=gloo-system/gloo-agentgateway listener=http route=gloo-system/openai endpoint=api.openai.com:443 src.addr=10.42.0.1:42865 http.method=POST http.host=192.168.107.2 http.path=/openai http.version=HTTP/1.1 http.status=403 duration=0ms
```

## Cleanup
```bash
kubectl delete glootrafficpolicy -n gloo-system api-key-auth
kubectl delete authconfig -n gloo-system apikey-auth
kubectl delete secret -n gloo-system team1-apikey
kubectl delete httproute -n gloo-system openai
kubectl delete backend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
```