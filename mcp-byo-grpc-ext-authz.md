# MCP BYO gRPC External Authorization (ext-authz)

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

You should also have a working MCP route configured. If not, complete the [In-Cluster MCP](in-cluster-mcp.md) or [Remote MCP](remote-mcp.md) lab first.

## Lab Objectives
- Deploy a custom gRPC ext-authz server to the cluster
- Apply an `EnterpriseAgentgatewayPolicy` to enforce external authorization on MCP routes
- Validate that MCP requests without the required header are denied with 403
- Validate that MCP requests with the required header are allowed through

## How ext-authz works with MCP

MCP traffic flows over HTTP (POST requests to your MCP endpoint), so all standard HTTP policies — including ext-authz — apply to MCP traffic automatically. Your ext-authz server sees the HTTP-layer details:

| Field | What the ext-authz server sees |
|---|---|
| Method | `POST` |
| Path | `/mcp` (or your MCP route path) |
| Headers | All HTTP headers (Authorization, custom headers, etc.) |

This means the same gRPC ext-authz server used for [LLM ext-authz](llm-byo-grpc-ext-authz.md) works for MCP without any code changes.

> **Note:** ext-authz operates at the HTTP transport layer. It does not inspect the MCP protocol payload (e.g., which tool is being called). For per-tool authorization, use the built-in `mcpAuthorization` CEL policy instead, or combine both for layered security.

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

## Set up the MCP route

If you already have an MCP route configured from the [In-Cluster MCP](in-cluster-mcp.md) or [Remote MCP](remote-mcp.md) labs, you can skip this section and go directly to [Create the ext-authz policy](#create-the-ext-authz-policy).

Deploy a simple MCP server and create the backend and route:
```bash
kubectl create namespace mcp --dry-run=client -o yaml | kubectl apply -f -
```

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-website-fetcher
  namespace: mcp
spec:
  selector:
    matchLabels:
      app: mcp-website-fetcher
  template:
    metadata:
      labels:
        app: mcp-website-fetcher
    spec:
      containers:
      - name: mcp-website-fetcher
        image: ghcr.io/peterj/mcp-website-fetcher:main
        imagePullPolicy: Always
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-website-fetcher
  namespace: mcp
  labels:
    app: mcp-website-fetcher
spec:
  selector:
    app: mcp-website-fetcher
  ports:
  - port: 80
    targetPort: 8000
    appProtocol: agentgateway.dev/mcp
EOF
```

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
    - name: mcp-target
      static:
        host: mcp-website-fetcher.mcp.svc.cluster.local
        port: 80
        protocol: SSE
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp
  namespace: agentgateway-system
spec:
  parentRefs:
  - name: agentgateway-proxy
  rules:
    - backendRefs:
      - name: mcp-backend
        group: agentgateway.dev
        kind: AgentgatewayBackend
EOF
```

Wait for the MCP server to be ready
```bash
kubectl rollout status deployment/mcp-website-fetcher -n mcp --timeout=60s
```

## Verify the MCP route works without ext-authz

Get the gateway IP
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

Send a test request to the MCP endpoint to verify it is reachable
```bash
curl -i "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {
        "name": "test-client",
        "version": "1.0.0"
      }
    }
  }'
```

You should get a response from the MCP server (or a valid SSE connection response).

## Create the ext-authz policy

Create an `EnterpriseAgentgatewayPolicy` that applies ext-authz to the MCP HTTPRoute. By targeting the HTTPRoute instead of the Gateway, only MCP traffic requires ext-authz — other routes (like LLM routes) remain unaffected.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  namespace: agentgateway-system
  name: mcp-ext-auth-policy
  labels:
    app: grpc-ext-authz
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: mcp
  traffic:
    extAuth:
      backendRef:
        name: grpc-ext-authz
        namespace: agentgateway-system
        port: 4444
      grpc: {}
EOF
```

> **Note:** You can also target the Gateway to apply ext-authz to all routes (LLM + MCP). See the [LLM BYO gRPC ext-authz](llm-byo-grpc-ext-authz.md) lab for a Gateway-level example.

## Test: MCP request denied without required header

Send an MCP request without the `x-ext-authz: allow` header. The request should be denied with a 403.
```bash
curl -i "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {
        "name": "test-client",
        "version": "1.0.0"
      }
    }
  }'
```

Expected output:
```
HTTP/1.1 403 Forbidden
x-ext-authz-check-result: denied

denied by ext_authz: header `x-ext-authz: allow` not found in request
```

Check the ext-authz server logs to see the decision:
```bash
kubectl logs -n agentgateway-system -l app=grpc-ext-authz --tail=10
```

## Test: MCP request allowed with required header

Send the request again with the `x-ext-authz: allow` header.
```bash
curl -i "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "x-ext-authz: allow" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {
        "name": "test-client",
        "version": "1.0.0"
      }
    }
  }'
```

You should see a successful response from the MCP server.

## Combining ext-authz with MCP authorization

For layered security, combine ext-authz (HTTP-layer) with MCP authorization (protocol-layer). ext-authz handles coarse-grained access control (e.g., validate tokens, check custom headers), while `mcpAuthorization` handles fine-grained per-tool authorization using CEL expressions:

```yaml
# Example: restrict tool access based on JWT claims
mcpAuthorization:
  rules:
  - 'mcp.tool.name == "fetch"'
  - 'jwt.sub == "admin" && mcp.tool.name == "delete"'
```

See the [In-Cluster MCP](in-cluster-mcp.md) lab for more on MCP authorization with JWT claims.

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-ext-auth-policy
kubectl delete deployment -n agentgateway-system grpc-ext-authz
kubectl delete service -n agentgateway-system grpc-ext-authz
kubectl delete httproute -n agentgateway-system mcp
kubectl delete agentgatewaybackend -n agentgateway-system mcp-backend
kubectl delete deployment -n mcp mcp-website-fetcher
kubectl delete service -n mcp mcp-website-fetcher
```
