# LLM BYO gRPC External Authorization (ext-authz)

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy a custom gRPC ext-authz server to the cluster
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Create an `EnterpriseAgentgatewayPolicy` to enforce external authorization on the Gateway
- Validate that requests without the required header are denied with 403
- Validate that requests with the required header are allowed through

## About BYO External Auth

Enterprise Agentgateway lets you integrate your own external authorization service with the Gateway. When configured, the proxy sends a gRPC authorization request to your ext-authz service for every incoming request. Your service inspects headers, tokens, or other credentials and returns an allow or deny decision.

```
Client → Agentgateway Proxy → gRPC ext-authz service → Allow/Deny
                              ↓ (if allowed)
                         Backend (LLM / MCP / Agent)
```

The ext-authz service must conform to the [Envoy External Authorization gRPC proto](https://github.com/envoyproxy/envoy/blob/main/api/envoy/service/auth/v3/external_auth.proto).

## Deploy the ext-authz server

Deploy the custom gRPC ext-authz server. This image is built from [grpc-ext-authz](https://github.com/ably77/grpc-ext-authz) and by default allows requests that include the `x-ext-authz: allow` header.

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  namespace: agentgateway-system
  name: grpc-ext-authz
  labels:
    app: grpc-ext-authz
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grpc-ext-authz
  template:
    metadata:
      labels:
        app: grpc-ext-authz
        app.kubernetes.io/name: grpc-ext-authz
    spec:
      containers:
      - image: ably7/grpc-ext-authz:latest
        name: grpc-ext-authz
        ports:
        - containerPort: 9000
        env:
        - name: PORT
          value: "9000"
EOF
```

Wait for the ext-authz pod to be ready
```bash
kubectl rollout status deployment/grpc-ext-authz -n agentgateway-system --timeout=60s
```

Create a Service for the ext-authz Deployment. The `appProtocol: kubernetes.io/h2c` annotation tells the gateway that this backend speaks gRPC (HTTP/2 cleartext).
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  namespace: agentgateway-system
  name: grpc-ext-authz
  labels:
    app: grpc-ext-authz
spec:
  ports:
  - port: 4444
    targetPort: 9000
    protocol: TCP
    appProtocol: kubernetes.io/h2c
  selector:
    app: grpc-ext-authz
EOF
```

## Set up the OpenAI route

Create the OpenAI api-key secret, backend, and HTTPRoute
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

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
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

## Verify the route works without ext-authz

Send a test request to confirm the OpenAI route is working before we lock it down with ext-authz
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

You should get a 200 response with a completion from OpenAI.

## Create the ext-authz policy

Create an `EnterpriseAgentgatewayPolicy` that applies ext-authz to the OpenAI HTTPRoute. This policy references the ext-authz Service we deployed and uses the gRPC protocol.

By targeting the HTTPRoute instead of the Gateway, only traffic to this specific route requires ext-authz — other routes remain unaffected.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  namespace: agentgateway-system
  name: openai-ext-auth-policy
  labels:
    app: grpc-ext-authz
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  traffic:
    extAuth:
      backendRef:
        name: grpc-ext-authz
        namespace: agentgateway-system
        port: 4444
      grpc: {}
EOF
```

## Test: request denied without required header

Send the same request as before, but now the ext-authz policy is in place. The request should be denied with a 403 because it is missing the `x-ext-authz: allow` header.
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

Expected output:
```
HTTP/1.1 403 Forbidden
content-type: text/plain
x-ext-authz-check-result: denied

denied by ext_authz: header `x-ext-authz: allow` not found in request
```

You can also check the ext-authz server logs to see the decision:
```bash
kubectl logs -n agentgateway-system -l app=grpc-ext-authz --tail=10
```

## Test: request allowed with required header

Send the request again with the `x-ext-authz: allow` header. The ext-authz server recognizes this header and allows the request through.
```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "x-ext-authz: allow" \
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

You should see a 200 response with a completion from OpenAI, along with the `x-ext-authz-check-result: allowed` header injected by the ext-authz server.

## View Access Logs

Check the agentgateway proxy logs to see the request flow:
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system openai-ext-auth-policy
kubectl delete deployment -n agentgateway-system grpc-ext-authz
kubectl delete service -n agentgateway-system grpc-ext-authz
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```
