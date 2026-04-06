# MCP Tool Rate Limiting

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy the `mcp-server-everything` reference MCP server
- Understand how MCP tool calls map to HTTP requests
- Create a `RateLimitConfig` that limits the `get-env` tool to 3 calls per minute
- Apply the rate limit via `EnterpriseAgentgatewayPolicy` targeting the MCP HTTPRoute
- Verify that `get-env` is rate limited while other tools like `echo` are unaffected

## Overview

Every MCP operation — whether it's `tools/list`, `tools/call`, `resources/read`, or any other JSON-RPC method — is a single HTTP POST to the MCP endpoint. From the gateway's perspective, there is no distinction between listing tools and actually calling one.

A typical MCP client session produces approximately 3–5 HTTP POSTs:

| Client action | HTTP requests to `/mcp` |
|---|---|
| Connect to server | `initialize` → 1 POST |
| List available tools | `tools/list` → 1 POST |
| Call a tool once | `tools/call` → 1 POST |
| **Total per tool call session** | **~3–5 POSTs** |

This means a limit of "5 requests per minute" translates to roughly 1 tool call session per minute — not 5 individual calls. Size your limits in sessions, not raw HTTP requests.

To target a specific tool, use a CEL descriptor to inspect the JSON-RPC request body and extract the tool name from `params.name`. Only requests matching the configured tool name are counted — all other tools and MCP operations like `initialize` and `tools/list` pass through without restriction.

## Deploy the MCP Server

Create the `mcp` namespace and deploy the `mcp-server-everything` reference MCP server:

```bash
kubectl create namespace mcp
```

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-server-everything
  namespace: mcp
  labels:
    app: mcp-server-everything
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mcp-server-everything
  template:
    metadata:
      labels:
        app: mcp-server-everything
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "3001"
    spec:
      containers:
        - name: mcp-everything
          image: node:20-alpine
          command:
            - sh
            - -c
            - |
              export NODE_OPTIONS="--max-old-space-size=10240 --max-semi-space-size=64"
              npx -y @modelcontextprotocol/server-everything streamableHttp
          ports:
            - name: mcp-http
              containerPort: 3001
          env:
            - name: PORT
              value: "3001"
          readinessProbe:
            tcpSocket:
              port: 3001
            initialDelaySeconds: 15
            periodSeconds: 10
            failureThreshold: 3
          livenessProbe:
            tcpSocket:
              port: 3001
            initialDelaySeconds: 30
            periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-server-everything
  namespace: mcp
  labels:
    app: mcp-server-everything
spec:
  selector:
    app: mcp-server-everything
  ports:
    - name: mcp-http
      port: 8080
      targetPort: 3001
      appProtocol: kgateway.dev/mcp
EOF
```

Wait for the deployment to be ready:

```bash
kubectl rollout status deployment/mcp-server-everything -n mcp
```

## Create Backend and HTTPRoute

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
      selector:
        namespaces:
          matchLabels:
            kubernetes.io/metadata.name: mcp
        services:
          matchLabels:
            app: mcp-server-everything
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp
  namespace: agentgateway-system
spec:
  parentRefs:
  - name: agentgateway-proxy
    namespace: agentgateway-system
  rules:
    - matches:
      - path:
          type: PathPrefix
          value: /mcp
      backendRefs:
      - name: mcp-backend
        group: agentgateway.dev
        kind: AgentgatewayBackend
      timeouts:
        request: "0s"
EOF
```

## Get Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo $GATEWAY_IP
```

## Verify Connectivity

Run the MCP Inspector and confirm the server is reachable before applying any rate limits:

```bash
npx @modelcontextprotocol/inspector@0.21.1
```

In the MCP Inspector menu, connect to your AgentGateway:
- **Transport Type**: Select `Streamable HTTP`
- **URL**: Enter `http://$GATEWAY_IP:8080/mcp` (use the value exported above)
- Click **Connect**

From the **Tools** tab, click **List Tools** and verify the `mcp-server-everything` tools are available:
- `echo` — returns a message back to the caller
- `get-sum` — adds two numbers
- `get-env` — returns server environment variables

## Configure Per-Tool Rate Limiting

Create a `RateLimitConfig` that limits `get-env` to 3 calls per minute. All other tools are unaffected:

```bash
kubectl apply -f- <<EOF
apiVersion: ratelimit.solo.io/v1alpha1
kind: RateLimitConfig
metadata:
  name: mcp-tool-rate-limit
  namespace: agentgateway-system
spec:
  raw:
    domain: "mcp-tools"
    descriptors:
    - key: tool_name
      value: "get-env"
      rateLimit:
        requestsPerUnit: 3
        unit: MINUTE
    rateLimits:
    - actions:
      - cel:
          expression: 'json(request.body).with(body, body.method == "tools/call" ? string(body.params.name) : "none")'
          key: "tool_name"
EOF
```

The CEL expression extracts the tool name from the JSON-RPC body for `tools/call` requests. For `initialize`, `tools/list`, and other MCP operations where `params.name` doesn't exist, it returns `"none"` — which matches no descriptor and is never rate limited. Only requests where `tool_name=get-env` are counted against the 3/min limit.

Apply the rate limit by referencing the `RateLimitConfig` in an `EnterpriseAgentgatewayPolicy` that targets the MCP HTTPRoute:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-tool-rate-limit
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: mcp
  traffic:
    entRateLimit:
      global:
        rateLimitConfigRefs:
        - name: mcp-tool-rate-limit
EOF
```

Verify that the policy is attached:

```bash
kubectl get EnterpriseAgentgatewayPolicy mcp-tool-rate-limit -n agentgateway-system -o yaml
```

Both `Accepted` and `Attached` conditions must be `True` before testing.

## Test Per-Tool Rate Limits

### Hit the limit on a rate-limited tool

> **Note:** `get-env` is not actually an expensive tool, but imagine it as one that returns sensitive environment data you want to tightly control — for example, a tool that reads secrets, calls a paid external API, or triggers a long-running backend job.

In the MCP Inspector, call the `get-env` tool 4 times:

1. From the **Tools** tab, click **List Tools** and select the **Print Environment Tool** (`get-env`)
2. Leave the parameters empty (no input required) and click **Run Tool**
3. Repeat 3 more times

The first 3 calls will succeed. On the 4th call you should see an error:

```
MCP error -32001: Streamable HTTP error: Error POSTing to endpoint: 
```

### Verify independent counters with a standard tool

Even though `get-env` has hit its 3/min limit, `echo` has its own independent counter (10/min) and should still succeed:

1. From the **Tools** tab, select the `echo` tool
2. Enter any message (e.g. `Hello World!`) and click **Run Tool**
3. Verify you get back `Echo: Hello World!`

Exhausting the budget for `get-env` has no effect on `echo` because they have separate rate limit counters.

## Cleanup

```bash
kubectl delete rlc -n agentgateway-system mcp-tool-rate-limit
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-tool-rate-limit
kubectl delete deployment -n mcp mcp-server-everything
kubectl delete service -n mcp mcp-server-everything
kubectl delete agentgatewaybackend -n agentgateway-system mcp-backend
kubectl delete httproute -n agentgateway-system mcp
```
