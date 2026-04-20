# LLM BYO HTTP External Authorization (ext-authz)

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy a custom HTTP ext-authz server to the cluster
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Create an `EnterpriseAgentgatewayPolicy` to enforce external authorization on the Gateway
- Validate that requests without the required header are denied with 403
- Validate that requests with the required header are allowed through

## About BYO External Auth

Enterprise Agentgateway lets you integrate your own external authorization service with the Gateway. When configured, the proxy sends an HTTP authorization request to your ext-authz service for every incoming request. Your service inspects headers, tokens, or other credentials and returns an allow (200 OK) or deny (403 Forbidden) decision.

```
Client → Agentgateway Proxy → HTTP ext-authz service → Allow (200) / Deny (403)
                              ↓ (if allowed)
                         Backend (LLM / MCP / Agent)
```

The HTTP ext-authz service is a plain HTTP server. The proxy forwards the original request headers and (optionally) the body. A 200 response means allow; any non-200 response means deny. Response headers from the ext-authz service can be injected into the upstream request.

## Deploy the ext-authz server

Deploy the custom HTTP ext-authz server. This image is built from [http-ext-authz](https://github.com/ably77/http-ext-authz) and by default allows requests that include the `x-ext-authz: allow` header.

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  namespace: agentgateway-system
  name: http-ext-authz
  labels:
    app: http-ext-authz
spec:
  replicas: 1
  selector:
    matchLabels:
      app: http-ext-authz
  template:
    metadata:
      labels:
        app: http-ext-authz
        app.kubernetes.io/name: http-ext-authz
    spec:
      containers:
      - image: ably7/http-ext-authz:latest
        name: http-ext-authz
        ports:
        - containerPort: 9000
        env:
        - name: PORT
          value: "9000"
EOF
```

Wait for the ext-authz pod to be ready
```bash
kubectl rollout status deployment/http-ext-authz -n agentgateway-system --timeout=60s
```

Create a Service for the ext-authz Deployment. Since this is a plain HTTP server, no special `appProtocol` annotation is needed.
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  namespace: agentgateway-system
  name: http-ext-authz
  labels:
    app: http-ext-authz
spec:
  ports:
  - port: 4444
    targetPort: 9000
    protocol: TCP
  selector:
    app: http-ext-authz
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

Create an `EnterpriseAgentgatewayPolicy` that applies ext-authz to the OpenAI HTTPRoute. This policy references the ext-authz Service we deployed and uses the HTTP protocol.

By targeting the HTTPRoute instead of the Gateway, only traffic to this specific route requires ext-authz — other routes remain unaffected.

> **Note:** Unlike gRPC ext-authz (which receives all request headers automatically via the CheckRequest proto), HTTP ext-authz requires you to explicitly list which client request headers should be forwarded to the auth service using `allowedRequestHeaders`. Similarly, `allowedResponseHeaders` controls which headers from the auth service response are injected into the upstream request.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  namespace: agentgateway-system
  name: openai-ext-auth-policy
  labels:
    app: http-ext-authz
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  traffic:
    extAuth:
      backendRef:
        name: http-ext-authz
        namespace: agentgateway-system
        port: 4444
      http:
        allowedRequestHeaders:
        - x-ext-authz
        - x-api-key
        - authorization
        - content-type
        allowedResponseHeaders:
        - x-ext-authz-check-result
        - x-ext-authz-check-reason
        - x-user-id
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
kubectl logs -n agentgateway-system -l app=http-ext-authz --tail=10
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
kubectl delete deployment -n agentgateway-system http-ext-authz
kubectl delete service -n agentgateway-system http-ext-authz
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
```
