# Configure Routing to AWS Bedrock Provider

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our AWS Access Key credentials
- Create a route to AWS Bedrock as our backend LLM provider using a `Backend` and `HTTPRoute`
- Curl AWS Bedrock through the agentgateway proxy
- Validate the request went through the gateway in Jaeger UI

## Export AWS Credentials
Log in to AWS console and export the following variables
```bash
export AWS_ACCESS_KEY_ID="<aws access key id>"
export AWS_SECRET_ACCESS_KEY="<aws secret access key>"
export AWS_SESSION_TOKEN="<aws session token>"
```

echo the vars to make sure that they were exported
```bash
echo $AWS_ACCESS_KEY_ID
echo $AWS_SECRET_ACCESS_KEY
echo $AWS_SESSION_TOKEN
```

Create a secret containing an AWS access key
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: bedrock-secret
  namespace: gloo-system
type: Opaque
stringData:
  accessKey: ${AWS_ACCESS_KEY_ID}
  secretKey: ${AWS_SECRET_ACCESS_KEY}
  sessionToken: ${AWS_SESSION_TOKEN}
EOF
```

Create AWS Bedrock route and backend. For this setup we will configure multiple `Backends` using a single provider (AWS Bedrock) in a path-per-model routing configuration
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: bedrock-titan
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      bedrock:
        model: amazon.titan-tg1-large
        region: us-west-2
        auth:
          type: Secret
          secretRef:
            name: bedrock-secret
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: bedrock-haiku3.5
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      bedrock:
        model: anthropic.claude-3-5-haiku-20241022-v1:0
        region: us-west-2
        auth:
          type: Secret
          secretRef:
            name: bedrock-secret
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: bedrock-llama3-8b
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      bedrock:
        model: meta.llama3-1-8b-instruct-v1:0
        region: us-west-2
        auth:
          type: Secret
          secretRef:
            name: bedrock-secret
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bedrock
  namespace: gloo-system
  labels:
    example: bedrock-route
spec:
  parentRefs:
    - name: gloo-agentgateway
      namespace: gloo-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /bedrock/haiku
      backendRefs:
        - name: bedrock-haiku3.5
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /bedrock/titan
      backendRefs:
        - name: bedrock-titan
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
    - matches:
        - path:
            type: PathPrefix
            value: /bedrock/llama3-8b
      backendRefs:
        - name: bedrock-llama3-8b
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
    # catch-all will route to the bedrock titan upstream
    - matches:
        - path:
            type: Exact
            value: /bedrock
      backendRefs:
        - name: bedrock-titan
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
EOF
```

## curl AWS Bedrock Titan endpoint
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=gloo-agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/bedrock/titan" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## curl AWS Bedrock Haiku endpoint
```bash
curl -i "$GATEWAY_IP:8080/bedrock/haiku" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## curl AWS Bedrock llama3-8b endpoint
```bash
curl -i "$GATEWAY_IP:8080/bedrock/llama3-8b" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## Port-forward to Jaeger UI
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for agentgateway that include information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n gloo-system bedrock
kubectl delete backend -n gloo-system bedrock-titan
kubectl delete backend -n gloo-system bedrock-haiku3.5
kubectl delete backend -n gloo-system bedrock-llama3-8b
kubectl delete secret -n gloo-system bedrock-secret
```