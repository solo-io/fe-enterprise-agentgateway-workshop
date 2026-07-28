# Configure Route to Remote MCP Server

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Route to an external MCP server using AgentGateway
- Configure TLS for HTTPS upstream targets
- Validate MCP server connectivity using MCP Inspector
- Integrate with Claude Code
- Secure MCP server with JWT auth
- Authorize access based on JWT claims
- Limit access to tools

## Overview

In this lab, we'll route to the **external** Solo.io documentation MCP server (`https://search.solo.io/mcp`) through AgentGateway. This demonstrates how to proxy external MCP servers and apply enterprise policies like authentication, authorization, and observability.

Unlike the [in-cluster MCP lab](in-cluster-mcp.md) which deployed an MCP server in your cluster, this lab routes to an existing remote server.

### Why Route Through AgentGateway?

Routing external MCP servers through AgentGateway provides several benefits:

1. **Centralized Observability**: View metrics, logs, and traces for all MCP calls in one place
2. **Security Policies**: Add authentication, authorization, and rate limiting
3. **Unified Access**: Single gateway endpoint for multiple MCP servers
4. **Traffic Management**: Apply retry policies, timeouts, and circuit breaking
5. **Corporate Compliance**: Route external traffic through your approved gateway infrastructure

### Create Backend and HTTPRoute

Since the Solo.io docs MCP server is external (already running at `https://search.solo.io/mcp`), we only need to configure the routing - no deployment required.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: soloio-docs-mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
    - name: soloio-docs-mcp-target
      static:
        host: search.solo.io
        port: 443
        protocol: StreamableHTTP
        policies:
          tls: {}
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: soloio-docs-mcp
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
      - name: soloio-docs-mcp-backend
        group: enterpriseagentgateway.solo.io
        kind: EnterpriseAgentgatewayBackend
EOF
```

**Key Configuration Details:**

- `host: search.solo.io` - External MCP server hostname
- `port: 443` - HTTPS port
- `protocol: StreamableHTTP` - HTTP-based MCP protocol
- `policies.tls: {}` - Enables TLS to the upstream server
- `value: /mcp` - Custom path prefix for routing

When `policies.tls` is set, AgentGateway will:
- Initiate TLS to the backend
- Use system trusted CA certificates to validate the server
- Automatically set SNI based on the destination hostname

### Get Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo $GATEWAY_IP
```

### Run the MCP Inspector

```bash
npx @modelcontextprotocol/inspector@0.21.1
```

In the MCP Inspector menu, connect to your AgentGateway:
- **Transport Type**: Select `Streamable HTTP`
- **URL**: Enter `http://$GATEWAY_IP:8080/mcp` (replace with your actual IP)
- Click **Connect**

### Search Solo.io Documentation

The Solo.io docs MCP server provides three tools:
- **search**: Search Solo.io product documentation
- **get_chunks**: Retrieve sequential segments of a document
- **get_full_page**: Retrieve a full documentation page

Let's test the search tool:

1. From the menu bar, click the **Tools** tab
2. Click **List Tools** and select the **search** tool
3. Fill in the parameters:
   - **query**: Enter `MCP authentication`
   - **product**: Select `solo-enterprise-for-agentgateway`
   - **limit**: Enter `4`
4. Click **Run Tool**
5. Verify you get back Solo.io documentation search results with URLs and content

## Integrate with Claude Code

If you have Claude Code you can configure it to use this MCP server through AgentGateway:

```bash
# If you previously have added the soloio-docs mcp server you can remove it
claude mcp remove soloio-docs-mcp

# Add via AgentGateway
claude mcp add --transport http soloio-docs-mcp http://$GATEWAY_IP:8080/mcp
```

Replace `$GATEWAY_IP` with your actual gateway IP.

### Verify Claude Code Configuration

1. Run `claude` to start Claude Code
2. Type `/mcp` in Claude Code to see the list of available MCP servers
3. Verify `soloio-docs-mcp` is listed with the AgentGateway URL
4. Click on `soloio-docs-mcp` to view the available tools:
   - `search`
   - `get_chunks`
5. This confirms you're using the MCP server through AgentGateway

### Test in Claude Code

Send this test prompt in Claude Code:
```
Search the Solo.io AgentGateway documentation for how to configure JWT authentication for MCP servers
```

You should see the MCP tools being called in the Claude Code interface:

```
soloio-docs-mcp - search (MCP)(query: "JWT authentication MCP servers configure",
                                product: "solo-enterprise-for-agentgateway", limit: 4)
  ⎿  ## Result 1 (Score: 0.6055)
     Product: solo-enterprise-for-agentgateway
     ...

soloio-docs-mcp - get_chunks (MCP)(collection: "docs-solo-io_agentgateway_2-1-x",
                                    url: "https://docs.solo.io/agentgateway/...", ...)
  ⎿  ## Chunks 0-6 of 7 total
     ...
```

The fact that you see `soloio-docs-mcp` as the tool source confirms that Claude Code is successfully using the MCP server through AgentGateway!

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 3 && curl -s http://localhost:15020/metrics | grep -E 'agentgateway_mcp_requests_total|protocol="mcp"' && kill $!
```

You should see MCP-specific metrics like:
- `agentgateway_mcp_requests_total` — per-call MCP counter, labeled by `method` (`initialize`, `tools/call`, …), `resource_type`, `server` (the MCP target), and `resource` (the tool name)
- `agentgateway_requests_total{protocol="mcp"}` — HTTP-level request counter for MCP traffic, labeled by `backend`, `route`, and `status`
- `agentgateway_request_duration_seconds{protocol="mcp"}` — request latency histogram for MCP traffic

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
- **MCP metrics** (tool calls, server requests)
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

Traces include MCP-specific spans with information like `mcp.method.name`, `mcp.resource.type`, `mcp.target`, `mcp.session.id`, and more.

### View Access Logs

AgentGateway automatically logs detailed information about MCP requests to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Example output shows comprehensive request details including MCP-specific information like:
- `mcp.method.name: tools/call`
- `mcp.target: soloio-docs-mcp-target`
- `mcp.resource.type: tool`
- `gen_ai.tool.name: search`
- `mcp.session.id: <session id>`
- `http.status: 200`
- Trace IDs for correlation with distributed traces in the Solo UI

## Secure Access to MCP Server

Create traffic policy to enforce JWT validation:

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

### Verify Authentication Failure

From the MCP Inspector, verify that the connection fails with an error message similar to the following, because no valid JWT was provided from the MCP inspector tool (MCP client) to the AgentGateway proxy:

```
MCP error -32001: Streamable HTTP error: Error POSTing to endpoint: authentication failure: no bearer token found
```

We should also be able to see this error in the access logs `authentication failure: no bearer token found` with an `http.status=401` and `reason=JwtAuth`:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

### Provide a Valid JWT

Go back to the MCP Inspector tool and expand the **Authentication** section. In the **Custom Headers** card, add a header with the following details:

- **Header Name**: Enter `Authorization`
- **Header Value**: Enter `Bearer ` followed by the JWT token below. The MCP Inspector sends this value as-is in the Authorization header, so the `Bearer ` prefix is required.
- **Toggle the header on** using the switch to the left of the row. Headers are disabled by default and are only sent when enabled — if you skip this, the connection still fails with `no bearer token found`.

```
eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw
```

After entering the token, click **Reconnect** in the MCP Inspector to re-establish the connection with the new credentials.

Now, if you try to run the `search` tool again it should result in **Tool Result: Success**

## Authorize Based on JWT Claims

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
      name: soloio-docs-mcp-backend
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
{"jsonrpc":"2.0","id":3,"error":{"code":-32602,"message":"Unknown tool: search"}}
```

### Inspect the JWT

If you navigate to https://jwt.io and input the token we should see the claims that we can create CEL RBAC rules on:

```json
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

Notice the `org` field is `solo.io`, not `admin`.

## Allow Access for solo.io Organization

Correct the expression to match the `org` claim our token actually carries, so that anyone in the `solo.io` org is allowed through:

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
      name: soloio-docs-mcp-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            - 'jwt.org == "solo.io"'
EOF
```

Reconnect and list tools again. All three tools are back, and running `search` returns **Tool Result: Success** — the expression authorizes the caller but says nothing about *which* tools they may use, so it grants the full catalog.

You can create complex authorization rules based on any JWT claim:
- `jwt.org == "admin"` - Require specific organization
- `jwt.team == "ai-team"` - Require specific team
- `jwt.llms.openai.exists(m, m == "gpt-4o")` - Check for model access

## Limit Tool Access

The expression above is still all-or-nothing: any caller in the `solo.io` org can reach *every* tool on the remote server. Because the policy is attached to the Backend, it is MCP-aware, so you can extend the same CEL expression with `mcp.tool.name` to authorize individual tools.

This is especially useful for remote MCP servers, where you don't control which tools the upstream exposes. As the vendor adds tools, an allowlist keyed on `mcp.tool.name` keeps your callers scoped to the ones you have reviewed.

In the following example, callers in the `solo.io` org may use only the `search` tool:

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
      name: soloio-docs-mcp-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            - 'jwt.org == "solo.io" && mcp.tool.name == "search"'
EOF
```

**Key configuration details:**

- `targetRefs` points at the `EnterpriseAgentgatewayBackend`, not the Gateway — tool authorization is evaluated by the MCP backend, so a Gateway-scoped `traffic.authorization` policy can gate the server as a whole but cannot filter individual tools
- `matchExpressions` entries are OR'd together; use `&&` within a single expression to require both a claim and a tool name
- Omitting a `mcp.tool.name` condition grants access to all tools, which is why the previous step returned the full catalog
- An invalid CEL expression fails closed. If the catalog comes back empty unexpectedly, check the policy status: `kubectl get enterpriseagentgatewaypolicy -n agentgateway-system mcp-rbac -o jsonpath='{.status.ancestors[*].conditions[*].message}'`

Verify the restriction in the MCP Inspector:

1. Click **Reconnect**, then from the **Tools** tab click **Clear** and **List Tools**. Only **search** is listed — `get_chunks` and `get_full_page` are filtered out.
2. Run **search** again. It still returns **Tool Result: Success**.
3. Because the other tools are no longer advertised, a call to `get_chunks` is rejected as an unknown tool:

```
{"jsonrpc":"2.0","id":4,"error":{"code":-32602,"message":"Unknown tool: get_chunks"}}
```

## Cleanup

Remove Kubernetes resources:

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system jwt
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-rbac
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system soloio-docs-mcp-backend
kubectl delete httproute -n agentgateway-system soloio-docs-mcp
```

Remove the MCP server from Claude Code and restore the direct connection:

```bash
# Remove the AgentGateway-proxied version
claude mcp remove soloio-docs-mcp

# Re-add the direct connection to Solo.io (optional)
claude mcp add --transport http soloio-docs-mcp https://search.solo.io/mcp
```

Restart Claude Code to apply the changes.

## Key Takeaways

- You can route to external MCP servers through AgentGateway without deploying them in your cluster
- Use `policies.tls: {}` to enable TLS for HTTPS upstreams
- Path-based routing allows multiple MCP servers on one gateway
- AgentGateway provides centralized observability, security policies, and traffic management for all MCP traffic
- JWT authentication and CEL-based authorization can secure access to MCP servers and tools
- Claude Code can be configured to use MCP servers through AgentGateway using the `claude mcp add` command
