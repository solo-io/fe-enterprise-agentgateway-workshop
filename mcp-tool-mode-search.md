# MCP Tool Mode — Search

## Pre-requisites

This lab assumes that you have completed the setup in `001` and that the gateway is running Enterprise AgentGateway **v2026.5.x or later** (the release that introduced `entMcp.toolMode`). Lab `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives

- Understand what Search mode does and when to reach for it
- Deploy `mcp-server-everything` and route it through an `EnterpriseAgentgatewayBackend` with `toolMode: Search`
- Verify with MCP Inspector that the upstream catalog is replaced by `get_tool` and `invoke_tool`
- Inspect the JSON-RPC shape of those meta-tools using raw `curl`
- Observe RBAC filtering: a restricted tool disappears from `get_tool` results

## Overview

A normal MCP backend forwards `tools/list` directly to the upstream server. The client receives the entire catalog on every connection, and that catalog stays in the model's context window for the rest of the session. **For a catalog of 50 tools at ~500 tokens of JSON schema each, that's 25,000 tokens spent before the first user prompt; at 180 tools it's roughly 90,000 tokens.** Most of those schemas are never used in the conversation.

**Search mode** replaces the upstream catalog with two meta-tools:

- `get_tool` — looks up an upstream tool by name or description fragment
- `invoke_tool` — calls an upstream tool by name with its arguments

The client only sees these two tools. The model searches the catalog at runtime instead of carrying it as context. Authorization is enforced inside `get_tool` — tools the caller can't use never appear in lookup results.

**Quantitatively:** the same 180-tool catalog through Search mode is two meta-tool schemas (~250 tokens) plus whichever single schema the model fetches at runtime (~140 tokens) — roughly **a 99% reduction in preloaded schema tokens.** Step 4 below measures this empirically on the lab's smaller upstream.

### When to use which mode

**Reach for Search mode when:**
- **The catalog is large and most tools go unused in a typical session.** Example: an aggregator MCP server fronting Jira, Linear, Salesforce, and an internal warehouse — easily 80–200 tools combined, but any one conversation touches a handful at most.
- **The catalog is dynamic.** Example: a tenant-scoped server where each customer gets a different tool set (per-integration connectors). The same client connection works across tenants because discovery happens at runtime — clients don't need to reconnect to refresh.
- **RBAC heavily filters per user.** Example: an internal "kitchen-sink" server where the average user is authorized to use ~10 of 200 tools. `get_tool` never surfaces the rest and the model never sees them. (Step 5 below demonstrates this with a single tool, but the pattern scales.)

**Stay with Standard mode when** the catalog is small and stable (e.g., a website-fetcher with one `fetch_url` tool, or a calculator with `add`/`multiply`) — there's not enough catalog to compress and the meta-tool indirection adds latency for no benefit. You also need Standard mode if you want per-upstream-tool counters on `agentgateway_mcp_tool_calls_total` without consulting traces; Search mode aggregates everything under the meta-tools.

**Reach for [Code mode](mcp-tool-mode-code.md) instead** when a workflow needs to chain tool calls or filter large intermediate results. Example: "find every open incident from `pagerduty.list_incidents`, fetch each one's logs via `loki.query_range`, return a 200-word summary." Search mode would round-trip each call and ferry every log blob through the model's context; Code mode keeps the intermediates inside the sandbox and returns only the summary. The two modes solve different problems — Search compresses the *catalog*, Code compresses the *results*.

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
      appProtocol: kgateway.dev/mcp
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
  name: mcp-search-backend
  namespace: agentgateway-system
spec:
  entMcp:
    toolMode: Search
    sessionRouting: Stateless
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
  name: mcp-search
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp/search
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /
      backendRefs:
        - name: mcp-search-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "0s"
EOF
```

**Key fields:**
- `entMcp.toolMode: Search` activates the meta-tool pattern; clients see only `get_tool` and `invoke_tool` instead of the upstream catalog.
- `sessionRouting: Stateless` tells the gateway not to pin MCP sessions to a specific upstream replica.
- Path prefix `/mcp/search` keeps this backend isolated from any other MCP route on the same gateway (including the [Code mode lab](mcp-tool-mode-code.md), which uses `/mcp/code`).
- `URLRewrite filter` strips the `/mcp/search` prefix so the upstream server sees the request as a root `/` request. Without this filter, the gateway forwards `/mcp/search` and the upstream returns 404.

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
- **URL**: `http://$GATEWAY_IP:8080/mcp/search`
- Click **Connect**

### List the tools

From the **Tools** tab, click **List Tools**. You should see exactly **two** tools — `get_tool` and `invoke_tool` — not the dozen tools that `mcp-server-everything` actually exposes.

### Look up a tool by name

Select **get_tool** and enter:

```json
{ "name": "get-sum" }
```

Click **Run Tool**. The response contains a `results` array with one entry — the `get-sum` tool's name, description, and input schema. `get_tool`'s `name` argument is an **exact-match lookup**, not a fuzzy search, so the upstream tool name (`get-sum`) is what you pass here.

### Invoke the tool

Select **invoke_tool** and enter:

```json
{ "name": "get-sum", "arguments": { "a": 2, "b": 3 } }
```

The response's `content[0].text` is `"The sum of 2 and 3 is 5."` — `invoke_tool` returns the upstream tool's response unchanged, so the shape (`content` vs `structuredContent`) depends on the upstream tool. The headline is the value, not the envelope.

### Discovery on a miss

Select **get_tool** again and enter:

```json
{ "name": "nonexistent" }
```

The response returns `status: "no_match"` and an `available_tools` array listing every tool the upstream exposes — that's how a model recovers when its initial exact-name guess misses. A search like `{ "name": "sum" }` also misses (no upstream tool is literally named `sum`); use the `available_tools` array to find the actual name (`get-sum`) and retry.

## Step 4: Under the Hood — Raw JSON-RPC

MCP Inspector is a nice UI but the protocol is plain HTTP. Walk through the same flow with `curl` to see the meta-tools' actual JSON-RPC shape.

### Initialize and capture the session ID

```bash
INIT=$(curl -s -i -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')

export SID=$(echo "$INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
echo "Session: $SID"
```

> **Note:** Because the backend uses `sessionRouting: Stateless`, the gateway does not emit an `mcp-session-id` header on initialize — `$SID` will be empty, and that's expected. Subsequent calls in this lab still pass `-H "Mcp-Session-Id: $SID"` (which becomes a harmless empty header) so the same snippet works unchanged if you later switch to a stateful routing mode that does return a session ID.

### List tools — confirm only the two meta-tools come back

```bash
curl -s -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -m json.tool
```

### Look up a tool

```bash
curl -s -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_tool","arguments":{"name":"get-sum"}}}' | python3 -m json.tool
```

The `structuredContent.results` array has one entry with the tool's `name`, `tool_description`, and JSON-Schema `args`. The `name` argument is an exact-name lookup; pass the literal upstream tool name (e.g., `get-sum`, not `sum`).

### Invoke it

```bash
curl -s -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"invoke_tool","arguments":{"name":"get-sum","arguments":{"a":2,"b":3}}}}' | python3 -m json.tool
```

The final response's `result.content[0].text` is `"The sum of 2 and 3 is 5."` — `invoke_tool` returns the upstream tool's reply verbatim, so the envelope shape (`content`, `structuredContent`, both, or neither) follows the upstream. Note the double-nested `arguments`: `invoke_tool` takes an `arguments` field whose value is itself the upstream tool's `arguments` payload.

### Measure the savings

JSON typically tokenizes at ~3-4 characters per token, so byte counts track tokens proportionally. The following commands capture the `tools/list` response size through Search mode (two meta-tools) versus directly against the upstream (the full catalog that Standard mode would have forwarded).

Capture the Search-mode `tools/list` response size. `$SID` is already exported from the initialize step above:

```bash
SEARCH_BYTES=$(curl -s -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":10,"method":"tools/list","params":{}}' | wc -c)
echo "Search-mode tools/list: $SEARCH_BYTES bytes"
```

Capture the upstream's raw `tools/list` response size by port-forwarding directly into the MCP server (bypassing the gateway). This is the closest equivalent to what Standard mode would surface — both forward the upstream catalog verbatim:

```bash
# Open a port-forward to the upstream MCP server in another terminal
# (or in the background of this terminal)
kubectl port-forward -n mcp svc/mcp-server-everything 3001:8080 &
PF_PID=$!
sleep 2

# Initialize a fresh session against the upstream directly
UPSTREAM_INIT=$(curl -s -i -X POST "http://localhost:3001/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')
UPSTREAM_SID=$(echo "$UPSTREAM_INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')

UPSTREAM_BYTES=$(curl -s -X POST "http://localhost:3001/mcp" \
  -H "Mcp-Session-Id: $UPSTREAM_SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | wc -c)
echo "Upstream (Standard equivalent) tools/list: $UPSTREAM_BYTES bytes"

kill $PF_PID 2>/dev/null
```

Calculate the reduction:

```bash
python3 -c "
upstream = $UPSTREAM_BYTES
search = $SEARCH_BYTES
saved = upstream - search
pct = (saved / upstream) * 100 if upstream else 0
print(f'Standard catalog: {upstream} bytes')
print(f'Search meta-tools: {search} bytes')
print(f'Reduction: {saved} bytes ({pct:.1f}%)')
"
```

On `mcp-server-everything`'s ~12-tool catalog you'll typically see a meaningful reduction even at this small scale. The proportional win is the same shape that scales to 99%+ for production catalogs with hundreds of tools. The next section deploys a synthetic 50-tool server so you can see the bigger numbers directly.

## Step 5: RBAC — Filter a Tool Out of get_tool Results

Authorization in Search mode is enforced inside `get_tool` — restricted tools never surface as lookup results. To demonstrate, apply a policy that requires a specific JWT claim to see `get-env`.

### Apply JWT validation

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-search-jwt
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mcp-search
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

The gateway-native pattern targets the **backend** (not the HTTPRoute) and uses the gateway-parsed CEL attribute `mcp.tool.name`. This attribute is extracted from the parsed MCP traffic by the proxy itself — it applies to both `tools/list` (filtering catalog visibility) and `tools/call` (denying invocation). In Search mode that means a `get_tool` lookup for `get-env` returns `no_match` for callers who fail the expression. Because authorization is enforced on the gateway's parsed view (not raw HTTP), it works uniformly across Standard, Search, and Code modes — no special-casing for meta-tools is required.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-search-tool-authz
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: mcp-search-backend
  backend:
    mcp:
      authorization:
        action: Allow
        policy:
          matchExpressions:
            # Allow all tools EXCEPT get-env for the demo JWT (which has org: solo.io).
            # The expression must evaluate true for the tool to be visible/callable.
            # Engineering JWTs would carry a different claim that satisfies this.
            - 'mcp.tool.name != "get-env" || (has(jwt.roles) && jwt.roles.exists(r, r == "engineering"))'
EOF
```

Note that `targetRefs.kind` is `EnterpriseAgentgatewayBackend` and `name` is `mcp-search-backend` — the policy attaches to the backend, not the HTTPRoute. The `mcp.tool.name` CEL attribute is a gateway-native value the proxy extracts from parsed MCP traffic.

> The exact enterprise field path (`spec.backend.mcp.authorization`) is verified against `v2026.5.2`. If your cluster rejects the resource, run `kubectl explain enterpriseagentgatewaypolicies.spec.backend.mcp` to confirm the field shape on your installed version.

### Test with the demo JWT

```bash
TOKEN="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw"

INIT=$(curl -s -i -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')
export SID=$(echo "$INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')

curl -s -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Mcp-Session-Id: $SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_tool","arguments":{"name":"env"}}}' | python3 -m json.tool
```

Expected: `get_tool` with `{"name": "env"}` returns `status: "no_match"` and the `available_tools` array does not contain `get-env`. The CEL expression for tool name `get-env` evaluates as `mcp.tool.name != "get-env" || (has(jwt.roles) && jwt.roles.exists(r, r == "engineering"))` → `false || (false && ...)` → `false` — the tool is filtered. The same call without the policy would return the tool's metadata.

## Step 6: Scale Up the Catalog (Optional)

`mcp-server-everything` ships ~12 tools, which keeps the Search-mode token reduction modest in absolute terms. To see the bigger numbers, deploy a synthetic MCP server that emits N stub tools and re-point the backend.

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
    // 7 categories x 9 targets = 63 unique base names. For TOOL_COUNT > 63 we
    // suffix with a round number so the catalog scales linearly without a ceiling.
    for (let i = 0; i < TOOL_COUNT; i++) {
      const c = categories[i % categories.length];
      const t = targets[Math.floor(i / categories.length) % targets.length];
      const round = Math.floor(i / (categories.length * targets.length));
      const name = round === 0 ? `${c}_${t}` : `${c}_${t}_${round}`;
      tools.push({
        name,
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
  ports: [{ port: 8080, targetPort: 8000, name: http, appProtocol: kgateway.dev/mcp }]
EOF

kubectl rollout status deployment/mcp-stub -n mcp --timeout=60s
```

### Re-point the backend at the stub server

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-search-backend
  namespace: agentgateway-system
spec:
  entMcp:
    toolMode: Search
    sessionRouting: Stateless
    targets:
      - name: mcp-target
        static:
          host: mcp-stub.mcp.svc.cluster.local
          port: 8080
          path: /
EOF
```

### Re-measure

Repeat the measurement from Step 4 against the larger catalog. Open a fresh session first because the backend changed:

```bash
INIT=$(curl -s -i -X POST "http://$GATEWAY_IP:8080/mcp/search" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0.0.1"}}}')
export SID=$(echo "$INIT" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')

SEARCH_BYTES=$(curl -s -X POST "http://$GATEWAY_IP:8080/mcp/search" \
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
UPSTREAM_RESPONSE=$(curl -s -X POST "http://localhost:3001/" \
  -H "Mcp-Session-Id: $UPSTREAM_SID" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')
UPSTREAM_BYTES=$(printf '%s' "$UPSTREAM_RESPONSE" | wc -c)
UPSTREAM_TOOLS=$(printf '%s' "$UPSTREAM_RESPONSE" | jq '.result.tools | length')
kill $PF_PID 2>/dev/null

python3 -c "
upstream = $UPSTREAM_BYTES
search = $SEARCH_BYTES
tools = $UPSTREAM_TOOLS
saved = upstream - search
pct = (saved / upstream) * 100 if upstream else 0
print(f'Stub catalog ({tools} tools) upstream: {upstream} bytes')
print(f'Search meta-tools:                  {search} bytes')
print(f'Reduction:                          {saved} bytes ({pct:.1f}%)')
"
```

Crank `TOOL_COUNT` in the Deployment to 100, 200, or 500 and re-run the measurement to watch the proportional reduction stay flat while the absolute savings grow linearly.

### Point the backend back to `mcp-server-everything` before continuing

The rest of the lab (Observability, Cleanup) assumes the backend points at `mcp-server-everything`. Either restore it now, or include `mcp-stub` in your cleanup at the end:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mcp-search-backend
  namespace: agentgateway-system
spec:
  entMcp:
    toolMode: Search
    sessionRouting: Stateless
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

Note that the structured log's `mcp.method` field shows the *meta-tool* name (`get_tool`, `invoke_tool`) — not the upstream tool — because that's what the client called. The gateway's call to the upstream is a separate trace span.

### View MCP metrics

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep mcp_tool_calls && kill $!
```

`agentgateway_mcp_tool_calls_total` increments for `get_tool` and `invoke_tool`, *not* `echo`/`get-sum`/etc. If you want per-upstream-tool metrics, you need Standard mode or to inspect traces.

### View in Grafana

If you have Grafana from lab `002`:

1. Port-forward: `kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000`
2. Open http://localhost:3000 (admin / prom-operator)
3. **Dashboards > AgentGateway Dashboard** — the MCP section shows tool-call rates for the meta-tools.

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-search-jwt --ignore-not-found
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system mcp-search-tool-authz --ignore-not-found
kubectl delete httproute -n agentgateway-system mcp-search --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-search-backend --ignore-not-found
kubectl delete deployment -n mcp mcp-stub --ignore-not-found
kubectl delete service -n mcp mcp-stub --ignore-not-found
kubectl delete configmap -n mcp mcp-stub-script --ignore-not-found
kubectl delete deployment -n mcp mcp-server-everything --ignore-not-found
kubectl delete service -n mcp mcp-server-everything --ignore-not-found
```
