# MCP Tool Mode — Code

## Pre-requisites

This lab assumes that you have completed the setup in `001` and that the gateway is running Enterprise AgentGateway **v2026.5.x or later** (the release that introduced `entMcp.toolMode`). Lab `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives

- Understand what Code mode does and when to reach for it
- Deploy `mcp-server-everything` and route it through an `EnterpriseAgentgatewayBackend` with `toolMode: Code`
- Verify with MCP Inspector that the upstream catalog is replaced by a single `run_code` tool
- Inspect the typed JavaScript API the gateway generates from the upstream catalog
- Compose multiple upstream tool calls in one script without round-trips
- Observe RBAC filtering: a restricted tool disappears from the typed API

## Overview

In Standard mode, every upstream tool call is its own round trip: the model issues a `tools/call`, the gateway forwards, the upstream responds, and the **full response is appended to the model's context** before the next reasoning step. A workflow that filters 500 records then enriches 30 of them costs the model ~30+ round trips and pulls all 500 records through its context window. **Tool results — especially large ones like file contents, base64 images, or paged query responses — dominate the context budget faster than schemas do.**

**Code mode** replaces the catalog with a single tool: `run_code`. Clients submit JavaScript that calls upstream tools through an auto-generated typed API. The gateway executes the script in a sandbox and returns only the final value. Intermediate results never reach the model.

**Quantitatively:** a script that calls three upstream tools and returns a 200-byte summary sends ~200 bytes back to the model regardless of how large the intermediate responses were. The same workflow in Standard mode would have sent the model every intermediate response in full. Step 4 measures both effects empirically.

### When to use which mode

**Reach for Code mode when:**
- **A workflow chains tool calls that depend on each other.** Example: "list every open incident from `pagerduty.list_incidents`, fetch each one's logs via `loki.query_range`, return a 200-word summary." In Code mode the 30 log blobs stay inside the sandbox; only the summary reaches the model.
- **Intermediate results are large.** Example: an image pipeline that fetches a base64-encoded image (KB–MB per payload), passes it through OCR, and returns extracted text. Standard mode would land every intermediate base64 payload in the model's context; Code mode only emits the final text. Step 4 Part B below demonstrates this with `get-tiny-image`.
- **The model needs to loop over tool calls rather than round-tripping each iteration.** Example: "for each of these 50 customer IDs, look up the open invoice total and sum them" — one `run_code` invocation vs. 50 sequential round trips. (Still bounded by the 20-tool-call ceiling — see Runtime limits above.)

**Stay with Standard mode when** workflows are one-shot and intermediate results are tiny (e.g., a calculator's `add(a,b)`, or a feature-flag service where each call is a self-contained lookup). Code mode buys you nothing if there's no chaining and no large intermediates to elide, and the sandbox indirection adds latency. You also need Standard mode if you want per-upstream-tool counters on `agentgateway_mcp_tool_calls_total`; Code mode aggregates everything under `run_code`.

**Reach for [Search mode](mcp-tool-mode-search.md) instead** when the catalog is large but calls don't depend on each other. Example: an aggregator MCP server with 200 tools where the model picks the right one per turn but rarely chains more than one — Search mode's `get_tool` is the lighter abstraction and you skip the sandbox altogether. The two modes solve different problems — Search compresses the *catalog*, Code compresses the *results*.

### Runtime limits

The sandbox enforces strict per-execution ceilings:

| Limit | Value | Notes |
|---|---|---|
| Memory | 4 MiB | Fixed |
| Tool calls | 20 max | Fixed |
| Wall-clock timeout | 5s default; this lab uses 60s via `codeMode.timeout` to absorb multi-step workflows | Adjustable via `entMcp.codeMode.timeout` |
| Stack | 256 KiB | Fixed |

Scripts that exceed any limit return an error result, which lets the model retry with different logic.

## Step 1: Deploy the MCP Server

Create the `mcp` namespace and deploy `mcp-server-everything`.

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

Wait for the deployment to be ready:

```bash
kubectl rollout status deployment/mcp-server-everything -n mcp
```

## Step 2: Create the EnterpriseAgentgatewayBackend and HTTPRoute

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-code-backend
  namespace: agentgateway-system
spec:
  entMcp:
    toolMode: Code
    sessionRouting: Stateless
    codeMode:
      timeout: 60s
    targets:
      - name: mcp-target
        static:
          host: mcp-server-everything.mcp.svc.cluster.local
          port: 8080
          path: /mcp
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-code
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp/code
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /
      backendRefs:
        - name: mcp-code-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "0s"
EOF
```

**Key fields:**
- `entMcp.toolMode: Code` activates the JS sandbox; the client sees only `run_code`.
- `entMcp.sessionRouting: Stateless` tells the gateway not to pin MCP sessions to a specific upstream replica.
- `entMcp.codeMode.timeout: 60s` raises the per-execution wall-clock limit above the 5-second default so multi-step scripts have room to complete. The 4 MiB memory, 20 tool-call, and 256 KiB stack ceilings still apply.
- Path prefix `/mcp/code` isolates this backend from any other MCP route on the same gateway (including the [Search mode lab](mcp-tool-mode-search.md)).
- The `URLRewrite` filter on the HTTPRoute strips `/mcp/code` to `/` so the upstream MCP server (which only knows `/`) receives a correctly-rooted request.

## Step 3: Verify with MCP Inspector

Get the gateway address:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo $GATEWAY_IP
```

Run MCP Inspector:

```bash
npx @modelcontextprotocol/inspector@0.21.1
```

Connect:
- **Transport Type**: Streamable HTTP
- **URL**: `http://$GATEWAY_IP:8080/mcp/code`
- Click **Connect**

### List the tools

From the **Tools** tab, click **List Tools**. You should see exactly **one** tool — `run_code` — not the five tools that `mcp-server-everything` actually exposes.

### Inspect the typed API

Click `run_code` and read its description. The description embeds a TypeScript-style API for every upstream tool the caller is authorized to use. For `mcp-server-everything`, you'll see entries like:

```javascript
// Echoes a message back to the caller
// type Input = { message: string }
async function echo(input);

// Returns the sum of two numbers
// type Input = { a: number, b: number }
async function get_sum(input);
```

This is the script-side API. The model writes JavaScript against these functions; the gateway executes the script in a sandbox and returns only the final value.

> **Note:** The typed API converts hyphenated tool names like `get-sum` to snake_case identifiers (`get_sum`). If your cluster emits camelCase (`getSum`) or bracket-access (`tools['get-sum']`) instead, substitute accordingly.

### Compose two tools in one script

In the `run_code` tool's argument form, paste:

```json
{
  "code": "const greeting = await echo({message: 'hello'}); const total = await get_sum({a: 2, b: 3}); ({greeting, total})"
}
```

Click **Run Tool**. The response's `structuredContent` is a single object containing both results — the model never sees the individual `echo` and `get_sum` responses. They stayed in the sandbox.

## Step 4: Under the Hood — Raw JSON-RPC

MCP Inspector wraps the script in a JSON-RPC envelope; `curl` makes the wrapping explicit.

### Initialize and capture the session ID

```bash
INIT=$(curl -s -i -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')

export SID=$(echo "$INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
echo "Session: $SID"
```

> **Note:** Because the backend uses `sessionRouting: Stateless`, the gateway does not emit an `mcp-session-id` header on initialize — `$SID` will be empty, and that's expected. Subsequent calls in this lab still pass `-H "Mcp-Session-Id: $SID"` (which becomes a harmless empty header) so the same snippet works unchanged if you later switch to a stateful routing mode that does return a session ID.

### List tools — confirm only run_code comes back

```bash
curl -s -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -m json.tool
```

Exactly one tool: `run_code`. Its `description` field contains the typed API.

### Run a composing script

```bash
curl -s -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"run_code","arguments":{"code":"const greeting = await echo({message: \"hello\"}); const total = await get_sum({a: 2, b: 3}); ({greeting, total})"}}}' | python3 -m json.tool
```

The `result.structuredContent.success` field has the combined object; intermediate results are not in the response.

### Hit the timeout

To see the limit in action, drive a script that waits longer than the configured timeout. The sandbox does not expose `Date`, `performance`, or `setTimeout`, so trigger the wait through the upstream `trigger-long-running-operation` tool instead:

```bash
curl -s -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"run_code","arguments":{"code":"await trigger_long_running_operation({duration: 70, steps: 1}); \"done\""}}}'
```

Expected: after exactly 60 seconds, the response is a structured JSON-RPC error with `isError: true` and `structuredContent.error.message` containing `Code mode execution timed out`. The 70-second upstream operation never completes; the gateway aborts the sandbox at the configured `codeMode.timeout` and returns a regular MCP error result, which lets a model retry with different logic rather than seeing a dropped connection.

### Measure the savings

JSON typically tokenizes at ~3-4 characters per token, so byte counts track tokens proportionally.

> **Note on catalog size:** Unlike Search mode, Code mode's `tools/list` response is not dramatically smaller than the upstream's — the typed API is embedded inside `run_code`'s description, so on this 12-tool catalog you'd see only a modest reduction (~5%). Code mode optimizes a different axis: **intermediate-result elimination at call time**, measured below. If you're curious, you can verify the catalog size with `curl … tools/list | wc -c` against `/mcp/code` versus a port-forward to the upstream.

#### Intermediate-result elimination

> **Note on identifier shape:** The run_code script below uses snake_case (`get_tiny_image`, `get_sum`) — that's what the gateway emits in v2026.5.x. If your cluster emits camelCase (`getTinyImage`) or hyphenated bracket-access (`tools['get-tiny-image']`) instead, substitute accordingly. The upstream tool names (used in the comparison block) are hyphenated regardless.

This is the marquee Code-mode demonstration. A multi-step script calls `get_tiny_image()` — which returns a base64-encoded image that is KB of payload — then returns only a tiny summary. Compare what the model receives in Code mode to what it would have received with sequential standard calls:

```bash
# Code mode: one round trip, large intermediate results stay in the sandbox.
# Script calls echo, get_sum, AND get_tiny_image (which returns a base64 image),
# then returns only a 200-ish-byte summary.
CODE_RUN_BYTES=$(curl -s -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":30,"method":"tools/call","params":{"name":"run_code","arguments":{"code":"const greeting = await echo({message: \"hello\"}); const total = await get_sum({a: 2, b: 3}); const img = await get_tiny_image({}); ({greeting, total, imageBytes: JSON.stringify(img).length})"}}}' | wc -c)
echo "Code mode (3 tools, 1 round trip, summary only): $CODE_RUN_BYTES bytes"

# Standard equivalent: three sequential tools/call invocations against the upstream.
# Each full response would have reached the model. Measure the sum of bytes.
kubectl port-forward -n mcp svc/mcp-server-everything 3001:8080 &
PF_PID=$!
sleep 2
UPSTREAM_INIT=$(curl -s -i -X POST "http://localhost:3001/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')
UPSTREAM_SID=$(echo "$UPSTREAM_INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')

ECHO_BYTES=$(curl -s -X POST "http://localhost:3001/mcp" \
  -H "Mcp-Session-Id: $UPSTREAM_SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello"}}}' | wc -c)
SUM_BYTES=$(curl -s -X POST "http://localhost:3001/mcp" \
  -H "Mcp-Session-Id: $UPSTREAM_SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get-sum","arguments":{"a":2,"b":3}}}' | wc -c)
IMG_BYTES=$(curl -s -X POST "http://localhost:3001/mcp" \
  -H "Mcp-Session-Id: $UPSTREAM_SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get-tiny-image","arguments":{}}}' | wc -c)
kill $PF_PID 2>/dev/null

python3 -c "
code = $CODE_RUN_BYTES
standard = $ECHO_BYTES + $SUM_BYTES + $IMG_BYTES
saved = standard - code
pct = (saved / standard) * 100 if standard else 0
print(f'Standard equivalent (3 sequential calls): {standard} bytes')
print(f'  echo:           $ECHO_BYTES bytes')
print(f'  get-sum:        $SUM_BYTES bytes')
print(f'  get-tiny-image: $IMG_BYTES bytes  <- dominates the standard cost')
print(f'Code mode (one round trip + summary):     {code} bytes')
print(f'Intermediate-result reduction:           {saved} bytes ({pct:.1f}%)')
"
```

The `get-tiny-image` response dominates the Standard total — that's the point. In Code mode the base64 image stayed inside the sandbox; only the script's final summary object (which contains `imageBytes: <length>` rather than the image itself) reached the model. In a production workflow that fetches dozens of records and computes a small summary, the absolute reduction scales with the size of the intermediate results being elided — easily 50-100× for image-heavy or large-document workflows.

## Step 5: RBAC — Filter a Tool Out of the Typed API

In Code mode, authorization is enforced in the typed API: restricted tools never appear as `async function` entries in `run_code`'s description. A script that tries to call a restricted tool fails compilation in the sandbox before it can run.

### Apply JWT validation

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-code-jwt
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mcp-code
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

### Apply per-tool authorization

The gateway-native pattern targets the **backend** (not the HTTPRoute) and uses the gateway-parsed CEL attribute `mcp.tool.name`. This attribute is extracted from the parsed MCP traffic by the proxy itself — it applies to both `tools/list` (filtering catalog visibility, i.e., which functions appear in the typed API) and `tools/call` (sandbox compilation/execution). In Code mode that means a restricted tool's `async function` entry is absent from `run_code`'s description, and a script that attempts to call it fails compilation before it runs. Because authorization is enforced on the gateway's parsed view (not raw HTTP), it works uniformly across Standard, Search, and Code modes — no special-casing for meta-tools is required.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-code-tool-authz
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: mcp-code-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            # Allow all tools EXCEPT get-env unless the caller carries the "engineering" role.
            # The expression must evaluate true for the tool to surface in the typed API.
            - 'mcp.tool.name != "get-env" || (has(jwt.roles) && jwt.roles.exists(r, r == "engineering"))'
EOF
```

Note that `targetRefs.kind` is `EnterpriseAgentgatewayBackend` and `name` is `mcp-code-backend` — the policy attaches to the backend, not the HTTPRoute. The `mcp.tool.name` CEL attribute is a gateway-native value the proxy extracts from parsed MCP traffic.

> The exact enterprise field path (`spec.backend.mcp.authorization`) is verified against `v2026.5.2`. If your cluster rejects the resource, run `kubectl explain enterpriseagentgatewaypolicies.spec.backend.mcp` to confirm the field shape on your installed version.

### Test with the demo JWT

```bash
TOKEN="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw"

INIT=$(curl -s -i -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')
export SID=$(echo "$INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')

curl -s -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -m json.tool | grep -E "function|get_env|getEnv|get-env"
```

Expected: in Code mode, the `run_code` tool's description (returned by `tools/list`) no longer lists `get_env` (or whichever identifier shape your cluster emits) in the typed API. The CEL expression for tool name `get-env` evaluates as `mcp.tool.name != "get-env" || (has(jwt.roles) && jwt.roles.exists(r, r == "engineering"))` → `false || (false && ...)` → `false` — the function is filtered from the typed API.

### Verify a script that calls the restricted tool fails

```bash
curl -s -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"run_code","arguments":{"code":"const env = await get_env({}); env"}}}' | python3 -m json.tool
```

Expected: an error indicating `get_env` is not defined in the sandbox — the typed API didn't include it for this caller, so the function is not defined and the script fails compilation before it runs.

## Step 6: Scale Up the Catalog (Optional)

`mcp-server-everything` only ships ~12 upstream tools (the gateway also exposes one synthesized helper, `simulate_research_query`, for Code-mode demos), which keeps Code mode's catalog savings modest in absolute terms. To see the typed-API compression on a larger catalog, deploy the synthetic stub server (the same one used in [the Search mode lab's Step 6](mcp-tool-mode-search.md#step-6-scale-up-the-catalog-optional)) and re-point the Code-mode backend at it. If you already deployed `mcp-stub` from the search lab, skip the next block and jump to "Re-point the backend".

### Deploy the synthetic server

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: mcp-stub-script
  namespace: mcp
data:
  server.js: |
    // Streamable-HTTP MCP server, JSON-RPC 2.0. Emits N stub tools across
    // a small set of categories so the catalog feels realistic to Search mode.
    const http = require('http');
    const PORT = 8000;
    const TOOL_COUNT = Number(process.env.TOOL_COUNT || 50);

    const categories = ['lookup', 'search', 'create', 'update', 'delete', 'list', 'count'];
    const targets    = ['user', 'order', 'product', 'incident', 'invoice', 'ticket', 'log', 'alert', 'report'];
    const tools = [];
    for (const c of categories) {
      for (const t of targets) {
        if (tools.length >= TOOL_COUNT) break;
        tools.push({
          name: `${c}_${t}`,
          description: `${c[0].toUpperCase()+c.slice(1)} ${t} records by id or filter. Synthetic stub for token-reduction demos.`,
          inputSchema: {
            type: 'object',
            properties: {
              id:    { type: 'string', description: 'Optional resource identifier' },
              limit: { type: 'number', description: 'Maximum results to return' },
              filter:{ type: 'string', description: 'Optional CEL/SQL-style filter expression' },
            },
          },
        });
      }
      if (tools.length >= TOOL_COUNT) break;
    }

    function rpc(id, result) { return { jsonrpc: '2.0', id, result }; }
    function err(id, code, message) { return { jsonrpc: '2.0', id, error: { code, message } }; }

    function handle(msg) {
      const id = msg.id ?? null;
      switch (msg.method) {
        case 'initialize':
          return rpc(id, {
            protocolVersion: '2025-03-26',
            capabilities: { tools: { listChanged: false } },
            serverInfo: { name: 'mcp-stub', version: '1.0.0' },
          });
        case 'notifications/initialized': return null;
        case 'tools/list': return rpc(id, { tools });
        case 'tools/call': {
          const name = msg.params?.name;
          const args = msg.params?.arguments ?? {};
          if (!tools.find(t => t.name === name)) return err(id, -32601, `Unknown tool: ${name}`);
          return rpc(id, { content: [{ type: 'text', text: JSON.stringify({ tool: name, args, served: 'stub' }) }], isError: false });
        }
        default: return err(id, -32601, `Method not found: ${msg.method}`);
      }
    }

    http.createServer((req, res) => {
      if (req.method === 'GET') {
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', 'connection': 'keep-alive' });
        return res.end();
      }
      if (req.method !== 'POST') { res.writeHead(405); return res.end(); }
      let body = '';
      req.on('data', c => body += c);
      req.on('end', () => {
        let parsed;
        try { parsed = JSON.parse(body); } catch { res.writeHead(400); return res.end('invalid JSON'); }
        const reply = handle(parsed);
        if (!reply) { res.writeHead(202); return res.end(); }
        res.writeHead(200, { 'content-type': 'application/json', 'mcp-session-id': req.headers['mcp-session-id'] || 'demo' });
        res.end(JSON.stringify(reply));
      });
    }).listen(PORT, () => console.log(`mcp-stub on :${PORT}, TOOL_COUNT=${TOOL_COUNT}`));
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-stub
  namespace: mcp
  labels: { app: mcp-stub }
spec:
  replicas: 1
  selector: { matchLabels: { app: mcp-stub } }
  template:
    metadata: { labels: { app: mcp-stub } }
    spec:
      containers:
      - name: stub
        image: node:20-alpine
        command: ["node", "/scripts/server.js"]
        env:
        - { name: TOOL_COUNT, value: "50" }   # crank this to 100, 200, 500 to see the effect
        ports: [{ containerPort: 8000, name: http }]
        volumeMounts:
        - { name: script, mountPath: /scripts }
        resources:
          requests: { cpu: 25m, memory: 32Mi }
          limits:   { cpu: 200m, memory: 128Mi }
      volumes:
      - name: script
        configMap: { name: mcp-stub-script }
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-stub
  namespace: mcp
  labels: { app: mcp-stub }
spec:
  selector: { app: mcp-stub }
  ports: [{ port: 8080, targetPort: 8000, name: http, appProtocol: agentgateway.dev/mcp }]
EOF

kubectl rollout status deployment/mcp-stub -n mcp --timeout=60s
```

### Re-point the backend at the stub server

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-code-backend
  namespace: agentgateway-system
spec:
  entMcp:
    toolMode: Code
    sessionRouting: Stateless
    codeMode:
      timeout: 60s
    targets:
      - name: mcp-target
        static:
          host: mcp-stub.mcp.svc.cluster.local
          port: 8080
          path: /
EOF
```

### Re-measure

Repeat the catalog measurement against the larger stub server. Open a fresh session first because the backend changed:

```bash
INIT=$(curl -s -i -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')
export SID=$(echo "$INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')

CODE_BYTES=$(curl -s -X POST "http://$GATEWAY_IP:8080/mcp/code" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":10,"method":"tools/list","params":{}}' | wc -c)

kubectl port-forward -n mcp svc/mcp-stub 3001:8080 &
PF_PID=$!
sleep 2
UPSTREAM_INIT=$(curl -s -i -X POST "http://localhost:3001/" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')
UPSTREAM_SID=$(echo "$UPSTREAM_INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
UPSTREAM_BYTES=$(curl -s -X POST "http://localhost:3001/" \
  -H "Mcp-Session-Id: $UPSTREAM_SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | wc -c)
kill $PF_PID 2>/dev/null

python3 -c "
upstream = $UPSTREAM_BYTES
code = $CODE_BYTES
saved = upstream - code
pct = (saved / upstream) * 100 if upstream else 0
print(f'Stub catalog ({50} tools) upstream: {upstream} bytes')
print(f'Code (run_code + typed API):       {code} bytes')
print(f'Catalog reduction:                 {saved} bytes ({pct:.1f}%)')
"
```

Crank `TOOL_COUNT` in the Deployment to 100, 200, or 500 and re-run the measurement to watch the absolute savings grow linearly while the typed-API description compresses the per-tool cost relative to JSON Schema.

### Point the backend back to `mcp-server-everything` before continuing

The rest of the lab (Observability, Cleanup) assumes the backend points at `mcp-server-everything`. Restore it now:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-code-backend
  namespace: agentgateway-system
spec:
  entMcp:
    toolMode: Code
    sessionRouting: Stateless
    codeMode:
      timeout: 60s
    targets:
      - name: mcp-target
        static:
          host: mcp-server-everything.mcp.svc.cluster.local
          port: 8080
          path: /mcp
EOF
```

## Observability

### View access logs

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

The `mcp.method` field shows `run_code` — each script execution is one MCP call to the gateway, regardless of how many upstream tool calls happen inside the sandbox.

### View MCP metrics

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep mcp_tool_calls && kill $!
```

`agentgateway_mcp_tool_calls_total` increments by one per `run_code` invocation. For per-upstream-tool visibility, inspect traces.

### View in Grafana

Same path as the [Search mode lab](mcp-tool-mode-search.md#observability).

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-code-jwt --ignore-not-found
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-code-tool-authz --ignore-not-found
kubectl delete httproute -n agentgateway-system mcp-code --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-code-backend --ignore-not-found
kubectl delete deployment -n mcp mcp-stub --ignore-not-found
kubectl delete service -n mcp mcp-stub --ignore-not-found
kubectl delete configmap -n mcp mcp-stub-script --ignore-not-found
kubectl delete deployment -n mcp mcp-server-everything --ignore-not-found
kubectl delete service -n mcp mcp-server-everything --ignore-not-found
```
