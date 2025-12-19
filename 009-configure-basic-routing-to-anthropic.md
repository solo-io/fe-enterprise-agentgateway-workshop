# Configure Basic Routing to Anthropic (Claude)

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Create a Kubernetes secret that contains your Anthropic API key credentials
- Create a route to Anthropic as your backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Test the integration with a sample request to Claude

## Set Environment Variables

Set your Anthropic API key for authenticating to Claude. You can get this from your [Anthropic Console](https://console.anthropic.com/).

```bash
export CLAUDE_API_KEY=<your-anthropic-api-key>
```

## Set Up Anthropic Connection

The secret created below contains the Anthropic API key for interacting with Anthropic Models.

```bash
kubectl create secret generic anthropic-secret -n enterprise-agentgateway \
--from-literal="Authorization=$CLAUDE_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

The `AgentgatewayBackend` object/kind is used to reference the Anthropic API key and specify the Model to be used.

```bash
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: anthropic
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      anthropic:
        model: "claude-3-5-haiku-latest"
  policies:
    auth:
      secretRef:
        name: anthropic-secret
EOF
```

```bash
kubectl get agentgatewaybackend -n enterprise-agentgateway
```

Create the route used to interact with the LLM via the Gateway that you created previously.

Please note: This route is being used to interact with Anthropic, but you could have multiple routes hitting the same agentgateway Gateway for interacting with other LLMs/LLM Providers (Gemini, OpenAI, etc.)

```bash
kubectl apply -f- <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: claude
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /anthropic
      backendRefs:
        - name: anthropic
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

Retrieve the Gateway address to use for testing purposes in the `curl` command to interact with Claude.

```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/anthropic" \
  -H "content-type: application/json" \
  -d '{
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
  }'
```

Expected output should be a successful response from Claude with a paragraph about Istio Ambient Mesh.

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway claude
kubectl delete agentgatewaybackend -n enterprise-agentgateway anthropic
kubectl delete secret -n enterprise-agentgateway anthropic-secret
```