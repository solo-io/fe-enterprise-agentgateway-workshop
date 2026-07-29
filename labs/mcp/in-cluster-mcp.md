# Configure Route to MCP Server

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy the `mcp-server-everything` reference MCP server
- Route to the MCP server using agentgateway with a static backend
- Validate MCP server connectivity using MCP Inspector
- Migrate the backend to dynamic label-based service discovery with zero route changes
- Observe session stickiness and load balancing as replicas scale
- Secure MCP server with JWT auth
- Authorize access based on JWT claims
- Limit access to tools

## Overview

### Static vs. Dynamic Backends

`EnterpriseAgentgatewayBackend` resources can wire up an MCP target two ways:

- **Static** — hard-code the target host and port directly in the Backend resource.
- **Dynamic** — use Kubernetes label selectors; AgentGateway watches the cluster for matching Services and wires them up automatically.

Static creates tight coupling between your gateway configuration and your MCP server deployment: any time the service name changes, the port shifts, or you migrate to a new implementation, you have to update the Backend resource — a gateway-config change just to update an application. Dynamic backends break that coupling: the gateway configuration becomes a stable contract, and only the application layer changes when you deploy or update MCP servers.

This lab walks through both. You'll stand up a static backend first, then migrate the *same* Backend resource to a label selector without touching the HTTPRoute at all.

| Concern | Static Backend | Dynamic Backend |
|---|---|---|
| Update MCP server image | Must also update Backend if service name changes | Deploy new pods — gateway auto-discovers them |
| Scale to multiple replicas | Single target, no built-in replica awareness | AgentGateway load-balances across all matching pods |
| Ownership boundary | Platform and app teams both touch Backend resource | Platform team owns Backend, app team owns Service labels |
| GitOps stability | Gateway config drifts with every app deployment | Gateway config stays static; app manifests change independently |

### The `mcp-server-everything` Reference Server

In this lab we'll use `@modelcontextprotocol/server-everything` — the official MCP reference implementation that provides a comprehensive set of tools for testing and exploration:

- **echo** — returns a message back to the caller
- **get-sum** — adds two numbers
- **get-env** — returns server environment variables
- **trigger-long-running-operation** — simulates a long-running task with progress notifications
- **get-tiny-image** — returns a small base64-encoded image

These tools make it easy to verify connectivity, test streaming behavior, and explore the full MCP protocol — making it an ideal server for learning and validation.

---

## Step 1: Deploy the MCP Server

The `mcp-server-everything` image runs via `npx`, so no custom container image is needed. Note the two required pieces of Kubernetes configuration:

- `appProtocol: agentgateway.dev/mcp` on the Service port — tells AgentGateway to speak the MCP protocol when connecting to this service
- `app: mcp-server-everything` label on both the Deployment and Service — this is the label the dynamic backend will select later

```bash
kubectl create namespace mcp --dry-run=client -o yaml | kubectl apply -f -
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
      appProtocol: agentgateway.dev/mcp
EOF
```

Verify the pod comes up:
```bash
kubectl rollout status deployment/mcp-server-everything -n mcp
```

---

## Step 2: Create a Static Backend and HTTPRoute

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
    - name: mcp-target
      static:
        host: mcp-server-everything.mcp.svc.cluster.local
        port: 8080
        protocol: StreamableHTTP
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
    - matches:
        - path:
            type: PathPrefix
            value: /mcp
      backendRefs:
      - name: mcp-backend
        group: enterpriseagentgateway.solo.io
        kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "0s"
EOF
```

### Get gateway IP
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo $GATEWAY_IP
```

### Run the MCP Inspector
```bash
npx @modelcontextprotocol/inspector@0.21.1
```

In the MCP Inspector menu, connect to your agentgateway
- Transport Type: Select Streamable HTTP.
- URL: Enter the agentgateway address, port, and the /mcp path. If your agentgateway proxy is exposed with a LoadBalancer server, use http://<lb-address>:8080/mcp. In local test setups where you port-forwarded the agentgateway proxy on your local machine, use http://localhost:8080/mcp.
- Click Connect.

### Run a tool
- From the menu bar, click the Tools tab. Then from the Tools pane, click List Tools and select the echo tool, listed as **Echo Tool**.
- In the message field, enter `Hello from AgentGateway!` and click Run Tool.
- Verify that the result reads `Tool Result: Success` and echoes your message back as `"Echo: Hello from AgentGateway!"`.

Try the **get-sum** tool as well, listed as **Get Sum Tool** — enter two numbers and confirm the result is returned.

The Inspector lists each tool by its display title rather than its protocol name, so `get-env` appears as **Print Environment Tool** and `trigger-long-running-operation` as **Trigger Long Running Operation Tool**.

---

## Step 3: Migrate to a Dynamic Backend

Instead of a hard-coded host and port, patch the same Backend to use a `selector` that matches the Service's labels. No HTTPRoute change is required — the route keeps pointing at `mcp-backend`; only the Backend's target resolution changes.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
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
EOF
```

**Key configuration details:**

- `selector.services.matchLabels` — AgentGateway uses this to find matching Services in the cluster
- The Backend no longer references any hostname or port — those are resolved dynamically from the discovered Service
- The `EnterpriseAgentgatewayBackend` name, the HTTPRoute, and the Inspector URL are all unchanged from Step 2

### Verify nothing else had to change

Reconnect in MCP Inspector using the same URL (`http://$GATEWAY_IP:8080/mcp`, Streamable HTTP) and run the **echo** tool again. It still works — the client, the route, and the backend name are identical; only the target resolution strategy changed.

---

## Step 4: Observe Dynamic Discovery in Action

This step demonstrates the core value of dynamic backends: updating the MCP server without modifying the Backend resource. It also shows how AgentGateway handles session stickiness across replicas.

### Scale the deployment

Add a second replica. AgentGateway discovers the Service, resolves its pod endpoints, and load-balances across them itself — so the new pod is picked up with no gateway configuration change at all:

```bash
kubectl scale deployment mcp-server-everything -n mcp --replicas=2
```

Verify both pods are running:
```bash
kubectl get pods -n mcp -l app=mcp-server-everything
```

### Tail both pod logs

Open a second terminal and stream logs from both pods simultaneously so you can see which pod handles each request:

```bash
kubectl logs -n mcp -l app=mcp-server-everything --prefix --follow
```

The `--prefix` flag prepends the pod name to each log line so you can tell them apart.

### Observe session stickiness

In MCP Inspector, connect and run **echo** or **get-env** several times. Watch the logs — all requests from your current session land on the same pod. AgentGateway assigns the session to a backend pod at connection time and returns a self-describing session token in the `mcp-session-id` header, so a client stays pinned to one pod for the lifetime of that session, and any agentgateway proxy replica can serve it.

This is the default behavior, `sessionRouting: Stateful` on the Backend, and it depends on the `selector`-based target you configured in Step 3 — a `static` target gives no such affinity guarantee. Set `sessionRouting: Stateless` instead and the gateway stops issuing a session ID entirely, which only works if the upstream MCP server holds no session state of its own. `mcp-server-everything` does hold session state, so it requires the stateful default.

### Observe load balancing on reconnect

Disconnect from MCP Inspector and reconnect. AgentGateway assigns a new session token, this time potentially routing to the other replica. Run **echo** again and check the logs — you may now see the second pod handling requests. Reconnect a few times to observe the distribution across both pods.

No Backend or HTTPRoute change was required at any point.

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
# `001` runs two proxy replicas and a request is only counted on the replica
# that served it, so scrape both.
for pod in $(kubectl get pods -n agentgateway-system \
    -l app.kubernetes.io/name=agentgateway-proxy -o name); do
  kubectl port-forward -n agentgateway-system "$pod" 15020:15020 >/dev/null 2>&1 &
  PF=$!
  sleep 3
  curl -s http://localhost:15020/metrics | grep -E 'agentgateway_mcp_requests_total|protocol="mcp"'
  kill "$PF" 2>/dev/null; wait "$PF" 2>/dev/null || true
done
```

You should see MCP-specific metrics such as:
- `agentgateway_mcp_requests_total` — per-call MCP counter, labeled by `method` (`initialize`, `tools/call`, …), `resource_type`, `server` (the MCP target), and `resource` (the tool name)
- `agentgateway_requests_total{protocol="mcp"}` — HTTP-level request counter for MCP traffic, labeled by `backend`, `route`, and `status`
- `agentgateway_request_duration_seconds{protocol="mcp"}` — request latency histogram for MCP traffic

For example, a `tools/call` for `echo` against the dynamic backend appears as:

```
agentgateway_mcp_requests_total{method="tools/call",resource_type="tool",server="mcp-server-everything-mcp-http",resource="echo",...} 2
```

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../../002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

The dashboard provides real-time visualization of:
- Core GenAI metrics (request rates, token usage by model)
- Streaming metrics (TTFT, TPOT)
- MCP metrics (tool calls, server requests)
- Connection and runtime metrics

### View Traces in the Solo UI

To view distributed traces with MCP-specific spans:

1. Port-forward to the Solo UI:
```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
```

2. Open http://localhost:4000 in your browser

3. Click **Tracing** in the left navigation

4. Use the **Search spans** box or the time-range buttons to find your requests, then click a row to open its span details

Each span carries the MCP fields `mcp.method.name`, `mcp.resource.type`, `mcp.target`, and `mcp.session.id`.

### View Access Logs

The gateway logs every MCP request to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

The log line carries the MCP fields `mcp.method.name`, `mcp.resource.type`, `mcp.target`, and `mcp.session.id`, plus a `trace.id` you can search for in the Solo UI's **Tracing** view.

### (Optional) View Traces in Jaeger

If you installed Jaeger in the [002 — Set Up Monitoring Tools (OCP)](../installation/openshift/002-set-up-monitoring-tools-ocp.md) lab instead of the Solo UI, you can view traces in the Jaeger UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see the traces. Each span carries the MCP fields `mcp.method.name`, `mcp.resource.type`, `mcp.target`, and `mcp.session.id`.

## Secure access to MCP Server

Create traffic policy to enforce JWT validation
```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: jwt
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: solo.io
          jwks:
            inline: |
                {
                  "keys": [
                    {
                      "kty": "RSA",
                      "kid": "solo-public-key-001",
                      "n": "vlmc5pb-jYaOq75Y4r91AC2iuS9B0sm6sxzRm3oOG7nIt2F1hHd4AKll2jd6BZg437qvsLdREnbnVrr8kU0drmJNPHL-xbsTz_cQa95GuKb6AI6osAaUAEL3dPjuoqkGNRe1sAJyOi48qtcbV0kPWcwFmCV0-OiqliCms12jrd1PSI_LYiNc3GcutpxY6BiHkbxxNeIuWDxE-i_Obq8EhhGkwha1KVUvLHV-EwD4M_AY8BegGsX-sjoChXOxyueu_ReqWV227I-FTKwMnjwWW0BQkeI6g1w1WqADmtKZ2sLamwGUJgWt4ZgIyhQ-iQfeN1WN2iupTWa5JAsw--CQJw",
                      "e": "AQAB",
                      "use": "sig",
                      "alg": "RS256"
                    }
                  ]
                }
EOF
```

From the MCP Inspector, click **Reconnect** and verify that the connection fails with an error message similar to the following, because no valid JWT was provided from the MCP inspector tool (MCP client) to the agentgateway proxy.
```
MCP error -32001: Streamable HTTP error: Error POSTing to endpoint: authentication failure: no bearer token found
```

This error also appears in the access logs as `authentication failure: no bearer token found` with an `http.status=401` and `reason=JwtAuth`
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

### Provide a valid JWT
Go back to the MCP Inspector tool and expand the **Authentication** section. In the **Custom Headers** card, add a header with the following details:

- **Header Name**: Enter `Authorization`
- **Header Value**: Enter `Bearer ` followed by the JWT token below. The MCP Inspector sends this value as-is in the Authorization header, so the `Bearer ` prefix is required.
- **Toggle the header on** using the switch to the left of the row. Headers are disabled by default and are only sent when enabled — if you skip this, the connection still fails with `no bearer token found`.
```
eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw
```

After entering the token, click **Reconnect** in the MCP Inspector to re-establish the connection with the new credentials.

Now, if you try to run the `echo` tool again it should result in `Tool Result: Success`

### Authorize based on JWT Claims
You can limit access to the MCP server based on specific JWT claims with CEL-based RBAC rules.

Create an EnterpriseAgentgatewayPolicy that attaches to the **Backend** and evaluates your rules under `backend.mcp.authorization`. Attaching at the Backend rather than the Gateway makes the rules MCP-aware, so the same CEL expression can reason about JWT claims now and individual tools later in this lab.

In the following example, you use a CEL expression to only allow access to the MCP server if the JWT has the `org=admin` claim:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-rbac
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: mcp-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            - 'jwt.org == "admin"'
EOF
```

Our token carries `org=solo.io`, not `org=admin`, so access is now denied. In the MCP Inspector, click **Reconnect**, then from the **Tools** tab click **Clear** and **List Tools** — the list comes back empty.

Unauthorized tools are filtered out of the catalog rather than merely blocked on call, so a denied caller sees an MCP server with no tools at all. Attempting a call anyway is rejected as an unknown tool:

```
{"jsonrpc":"2.0","id":3,"error":{"code":-32602,"message":"Unknown tool: echo"}}
```

### Inspect the JWT
If you navigate to jwt.io and input the tokens used we should see the claims that we can create CEL RBAC rules on

```
{
  "iss": "solo.io",
  "org": "solo.io",
  "sub": "user-id",
  "team": "team-id",
  "exp": 2079556104,
  "llms": {
    "openai": [
      "gpt-4o"
    ]
  }
}
```

### Restore access

Correct the expression to match the `org` claim our token carries, so that anyone in the `solo.io` org is allowed through:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-rbac
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: mcp-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            - 'jwt.org == "solo.io"'
EOF
```

Reconnect and list tools again. All twelve tools are back, and running `echo` returns `Tool Result: Success` — the expression authorizes the caller but says nothing about *which* tools they may use, so it grants the full catalog.

## Limit tool access

The expression above is still all-or-nothing: any caller in the `solo.io` org can reach *every* tool. Because the policy is attached to the Backend, it is MCP-aware, so you can extend the same CEL expression with `mcp.tool.name` to authorize individual tools.

In the following example, callers in the `solo.io` org may use only the `echo` tool:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-rbac
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: mcp-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            - 'jwt.org == "solo.io" && mcp.tool.name == "echo"'
EOF
```

**Key configuration details:**

- `targetRefs` points at the `EnterpriseAgentgatewayBackend`, not the Gateway — tool authorization is evaluated by the MCP backend, so a Gateway-scoped `traffic.authorization` policy can gate the server as a whole but cannot filter individual tools
- `matchExpressions` entries are OR'd together; use `&&` within a single expression to require both a claim and a tool name
- Omitting a `mcp.tool.name` condition grants access to all tools, which is why the previous step returned the full catalog

Verify the restriction in the MCP Inspector:

1. Click **Reconnect**, then from the **Tools** tab click **Clear** and **List Tools**. Only **Echo Tool** is listed — unauthorized tools are filtered out of `tools/list` rather than merely blocked on call.
2. Run **echo**. It still returns `Tool Result: Success`.
3. Because `get-sum` is no longer advertised, the Inspector can't offer it. Confirm it is rejected at the gateway by calling it directly:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

export JWT_TOKEN=eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw

MCP_SESSION_ID=$(curl -s -D - -o /dev/null -X POST "http://$GATEWAY_IP:8080/mcp" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  | grep -i '^mcp-session-id' | tr -d '\r' | awk '{print $2}')

curl -s -X POST "http://$GATEWAY_IP:8080/mcp" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "mcp-session-id: $MCP_SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get-sum","arguments":{"a":21,"b":21}}}'
```

Example output:
```
{"jsonrpc":"2.0","id":2,"error":{"code":-32602,"message":"Unknown tool: get-sum"}}
```

Because the tool was filtered out of the catalog, the gateway rejects the call as an unknown tool rather than as an authorization failure. Note this response arrives as plain JSON with a `400 Bad Request` status, whereas successful tool calls stream back as `text/event-stream` with a `data:` prefix.

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system jwt
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-rbac
kubectl delete httproute -n agentgateway-system mcp
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-backend
kubectl delete deployment -n mcp mcp-server-everything
kubectl delete service -n mcp mcp-server-everything
```
