# Configure Authentication To Anthropic (Claude)

### Prerequisites

To follow along with this lab, you should have:
1. A Kubernetes cluster running (Kind/Minikube or another type of cluster is fine)
2. An Anthropic account

## Objectives

The goal with this lab is to:
1. Use agentgateway to securely connect to an LLM (in this case, a Claude Model)
2. Configure Gloo Gateway + agentgateway

## Set Environment Variables

These environment variables will be for within your environment (cluster name, license keys)

```
export GLOO_GATEWAY_LICENSE_KEY=

export AGENTGATEWAY_LICENSE_KEY=
```

```
export CLUSTER1=

export CLUSTER1_NAME=
```

## Deploy Kubernetes Gateway API CRDs

The Gateway objects rely on Kubernetes Gateway API as the standard.

```
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.3.0/standard-install.yaml --context=$CLUSTER1
```

## Deploy Gloo Gateway
```
helm upgrade -i gloo-gateway-crds oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/gloo-gateway-crds --kube-context=$CLUSTER1 \
--create-namespace \
--namespace gloo-system \
--version 2.0.0-rc.1
```

```
helm upgrade -i gloo-gateway oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/gloo-gateway --kube-context=$CLUSTER1 \
-n gloo-system \
--version 2.0.0-rc.1 \
--set agentgateway.enabled=true \
--set licensing.glooGatewayLicenseKey=$GLOO_GATEWAY_LICENSE_KEY \
--set licensing.agentgatewayLicenseKey=$AGENTGATEWAY_LICENSE_KEY
```

```
kubectl get pods -n gloo-system --context=$CLUSTER1
```

```
kubectl get gatewayclass -n gloo-system --context=$CLUSTER1
```

## Set Up Anthropic Connection

```
export CLAUDE_API_KEY=
```

Set up a Gateway for the HTTP Route that will be used (you'll see this in the next few sections) to interact with the LLM of your choosing (in this case, Claude)

```
kubectl apply -f- <<EOF
kind: Gateway
apiVersion: gateway.networking.k8s.io/v1
metadata:
  name: agentgateway
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  gatewayClassName: agentgateway-enterprise
  listeners:
  - protocol: HTTP
    port: 8080
    name: http
    allowedRoutes:
      namespaces:
        from: All
EOF
```

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
    - name: agentgateway
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