# Configure Authentication To Anthropic (Claude)

### Prerequisites

To follow along with this lab, you should have:
1. A Kubernetes cluster running (Kind/Minikube or another type of cluster is fine)
2. An Anthropic account
3. A Gateway configuration

## Objectives

The goal with this lab is to:
1. Use agentgateway to securely connect to an LLM (in this case, a Claude Model)

## Set Environment Variables

These environment variables will be for authenticating to Anthropic.


```
export CLAUDE_API_KEY=
```

## Set Up Anthropic Connection

The secret created below contains the Anthropic API key for interacting with Anthropic Models.

```
kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: anthropic-secret
  namespace: gloo-system
  labels:
    app: agentgateway
type: Opaque
stringData:
  Authorization: $CLAUDE_API_KEY
EOF
```

The `Backend` object/kind is used to reference the Anthropic API key and specify the Model to be used.

```
kubectl apply -f- <<EOF
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  labels:
    app: agentgateway
  name: anthropic
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
        anthropic:
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
          model: "claude-3-5-haiku-latest"
EOF
```

```
kubectl get backend -n gloo-system
```

Create the route used to interact with the LLM via the Gateway that you created previously.

Please note: This route is being used to interact with Anthropic, but you could have multiple routes hitting the same agentgateway Gateway for interacting with other LLMs/LLM Providers (Gemini, OpenAI, etc.)

```
kubectl apply -f- <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: claude
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  parentRefs:
    - name: gloo-agentgateway
      namespace: gloo-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /anthropic
    filters:
    - type: URLRewrite
      urlRewrite:
        path:
          type: ReplaceFullPath
          replaceFullPath: /v1/chat/completions
    backendRefs:
    - name: anthropic
      namespace: gloo-system
      group: gateway.kgateway.dev
      kind: Backend
EOF
```

Retrieve the Gateway address to use for testing purposes in the `curl` command to interact with Claude.

```
export INGRESS_GW_ADDRESS=$(kubectl get svc -n gloo-system agentgateway -o jsonpath="{.status.loadBalancer.ingress[0]['hostname','ip']}")
echo $INGRESS_GW_ADDRESS
```

```
curl "$INGRESS_GW_ADDRESS:8080/anthropic" -H content-type:application/json  -d '{
  "model": "claude-3-5-haiku-latest",
  "messages": [
    {
      "role": "system",
      "content": "You are a skilled cloud-native network engineer."
    },
    {
      "role": "user",
      "content": "Write me a paragraph containing the best way to think about Istio Ambient Mesh"
    }
  ]
}' | jq
```