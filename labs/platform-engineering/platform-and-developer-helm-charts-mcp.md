# Platform and Developer Helm Charts: Self-Service MCP Endpoints

The [Platform and Developer Helm Charts lab](platform-and-developer-helm-charts-llm.md) splits the gateway into two personas: a platform team that owns the `Gateway`, the security baseline, the URL space, and the cost tiers, and application teams that self-serve endpoints under an assigned path prefix with a chart that has **no vocabulary** for traffic policy. That lab proves the model with LLM endpoints.

This lab applies the same model to **MCP servers**. A team that runs an MCP server declares it as a `type: mcp` endpoint in the same developer chart, and the chart renders an `EnterpriseAgentgatewayBackend` (`entMcp`) plus the same prefix-enforced, team-labeled child route. The platform's controls attach to the parent route, so tier budgets, JWT, and access logging cover MCP tool calls exactly as they cover chat completions — the team never configures any of them. This lab is standalone and shorter; for the full separation-of-concerns treatment (escape attempts, re-tiering), see the LLM lab.

> This lab requires Enterprise Agentgateway **v2026.6.3** or later (the version installed in `001`).

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- **Helm 3** installed.
- Run every command **from the workshop root** (the directory that contains `charts/`). Chart paths below are repo-root-relative.
- The two charts referenced here live at `charts/agentgateway-platform` and `charts/agentgateway-developer`.

> **Note:** The platform chart installs its **own** `Gateway` named `agw-platform`, alongside the `agentgateway-proxy` gateway from `001`. The two coexist; this lab never modifies `001`. This lab uses the same release name as the [LLM lab](platform-and-developer-helm-charts-llm.md) — if you still have `agw-platform` installed from that lab, run its Cleanup first.

## Lab Objectives
- Install the platform chart and stand up a platform-owned `Gateway` with one onboarded team
- Let the team self-serve an MCP endpoint with the developer chart, configuring **only** what it owns
- Exercise the MCP server through the gateway with plain `curl` (initialize, list tools, call a tool)
- Watch the team's platform-assigned tier budget rate-limit its tool calls with a `429`
- Prove the team cannot smuggle a traffic policy past the developer chart's schema
- Turn on JWT authentication for every endpoint at once, without the team changing its release
- Observe MCP-aware access logs from the platform-owned logging baseline

---

## Overview

```
                 PLATFORM TEAM  (owns charts/agentgateway-platform)
                               │
        Gateway: agw-platform  +  proxy fleet  +  access logs
        +  security baseline (JWT / WAF)  +  cost tiers
                               │
      ┌────────────────────────┴────────────────────────┐
      │  parent HTTPRoute: team-team-tools              │
      │  matches  /teams/team-tools                     │
      │  delegates to child routes labeled              │
      │  team=team-tools in namespace team-tools        │
      └────────────────────────┬────────────────────────┘
                               │  delegation (Gateway API)
      ┌────────────────────────┴────────────────────────┐
      │  APP TEAM team-tools (owns charts/agentgateway-developer)
      │  namespace: team-tools                          │
      │  child HTTPRoute: team-tools-tools              │
      │  matches  /teams/team-tools/mcp                 │
      │  backend: team-tools-tools (entMcp) ──► mcp-server-everything
      └─────────────────────────────────────────────────┘
```

The contract is the same as in the LLM lab: the platform assigns the team its namespace, its path prefix (`/teams/team-tools`), and its tier at onboarding; the developer chart stamps the `team: team-tools` delegation label on every route it creates and prepends the platform-owned prefix to every path. The parent route delegates **only** to child routes that carry the label **and** live in the team's namespace, and everything the platform attaches to the parent — authentication, logging, tier policies — is inherited by every child the team adds.

---

## Step 1: The platform team installs the platform chart

The platform team owns a single values file: the gateway, the tier catalog, and the roster of onboarded teams. Create `platform-values.yaml`:

```yaml
gateway:
  name: agw-platform
tiers:
  gold:
    rateLimit:
      tokensPerMinute: 1000
      toolCallsPerMinute: 300
    retry:
      attempts: 3
      backoff: 500ms
      codes:
        - 429
        - 502
        - 503
        - 504
    timeouts:
      request: 120s
  silver:
    rateLimit:
      tokensPerMinute: 25
      toolCallsPerMinute: 5
    timeouts:
      request: 60s
teams:
  - name: team-tools
    namespace: team-tools
    tier: silver
```

> **Note on the tier:** the platform assigns `team-tools` to `silver` at onboarding, exactly as in the LLM lab. A tier carries two budgets: `tokensPerMinute` meters **LLM** traffic (MCP requests carry no token usage), and `toolCallsPerMinute` meters **MCP tool calls** — a global counter on the team's parent route that counts only `tools/call` requests. `silver`'s 5 tool calls/minute is set tiny so you can trip a `429` in [Step 3](#step-3-the-tier-budgets-the-teams-tool-calls); a production tier would be far higher (e.g. `gold`'s `300`). As always, what matters is who sets the number: the platform assigns it, and the team never chooses it.

Install the chart into `agentgateway-system` (run from the workshop root):

```bash
helm install agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml
```

Verify the platform resources were created and accepted:

```bash
kubectl get gateway,httproute,enterpriseagentgatewaypolicy,ratelimitconfig,enterpriseagentgatewayparameters \
  -n agentgateway-system -l app.kubernetes.io/part-of=agentgateway-platform
```

Expected output:

```
NAME                                             CLASS                     ADDRESS          PROGRAMMED   AGE
gateway.gateway.networking.k8s.io/agw-platform   enterprise-agentgateway   172.18.255.249   True         11s

NAME                                                  HOSTNAMES   AGE
httproute.gateway.networking.k8s.io/team-team-tools               11s

NAME                                                                                     ACCEPTED   ATTACHED   AGE
enterpriseagentgatewaypolicy.enterpriseagentgateway.solo.io/agw-platform-access-log      True       True       11s
enterpriseagentgatewaypolicy.enterpriseagentgateway.solo.io/team-team-tools-tier         True       True       11s
enterpriseagentgatewaypolicy.enterpriseagentgateway.solo.io/team-team-tools-tool-calls   True       True       11s

NAME                                                           AGE
ratelimitconfig.ratelimit.solo.io/team-team-tools-tool-calls   11s

NAME                                                                                  AGE
enterpriseagentgatewayparameters.enterpriseagentgateway.solo.io/agw-platform-config   11s
```

The team is onboarded: it has a parent route (`team-team-tools`), a tier policy, and — because `silver` sets `toolCallsPerMinute` — a tool-call budget (`team-team-tools-tool-calls`, a `RateLimitConfig` plus the policy that attaches it to the parent route). No endpoints yet. Wait for the proxy fleet to roll out:

```bash
kubectl rollout status -n agentgateway-system deploy/agw-platform --timeout=180s
```

Expected output:

```
deployment "agw-platform" successfully rolled out
```

Capture the gateway address for the rest of the lab:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agw-platform -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "GATEWAY_IP=${GATEWAY_IP}"
```

---

## Step 2: The team self-serves an MCP endpoint

Now the application team takes over. Nothing the team does here touches security, rate limits, or logging.

### Deploy the team's MCP server

The team runs the reference `mcp-server-everything` in its own namespace. It serves MCP over **Streamable HTTP**, where every request is independent — so it works unchanged behind the platform's 2-replica proxy fleet. (An SSE-transport server would need the fleet scaled to a single replica, and the fleet shape is platform-owned, not the team's to change.)

```bash
kubectl create namespace team-tools --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-server-everything
  namespace: team-tools
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mcp-server-everything
  template:
    metadata:
      labels:
        app: mcp-server-everything
    spec:
      containers:
        - name: mcp-everything
          image: node:20-alpine
          command:
            - sh
            - -c
            - |
              npx -y @modelcontextprotocol/server-everything streamableHttp
          env:
            - name: PORT
              value: "3001"
          ports:
            - name: mcp-http
              containerPort: 3001
          readinessProbe:
            tcpSocket:
              port: 3001
            initialDelaySeconds: 15
            periodSeconds: 10
            failureThreshold: 3
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-server-everything
  namespace: team-tools
  labels:
    app: mcp-server-everything
spec:
  selector:
    app: mcp-server-everything
  ports:
    - name: mcp-http
      port: 8080
      targetPort: 3001
EOF

kubectl rollout status -n team-tools deploy/mcp-server-everything --timeout=180s
```

Expected output (the `npx` download can take up to a minute on first start):

```
deployment "mcp-server-everything" successfully rolled out
```

### Declare the endpoint

The team creates `team-tools-values.yaml`. It names its team (the label + prefix contract), then declares one MCP endpoint pointing at its in-namespace server:

```yaml
team: team-tools
endpoints:
  - name: tools
    type: mcp
    path: /mcp
    targets:
      - name: everything
        host: mcp-server-everything.team-tools.svc.cluster.local
        port: 8080
        protocol: StreamableHTTP
```

`targets` is a list: a single endpoint can federate several MCP servers behind one URL, each declared the same way. This lab uses one.

Install the developer chart as the team's own release, in the team's namespace:

```bash
helm install team-tools charts/agentgateway-developer \
  -n team-tools \
  --values team-tools-values.yaml
```

Verify the child route and backend, and note the delegation label:

```bash
kubectl get httproute,enterpriseagentgatewaybackend -n team-tools
kubectl get httproute team-tools-tools -n team-tools -o jsonpath='{.metadata.labels}{"\n"}'
```

Expected output:

```
NAME                                                   HOSTNAMES   AGE
httproute.gateway.networking.k8s.io/team-tools-tools               4s

NAME                                                                            ACCEPTED   AGE
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/team-tools-tools   True       4s

{"app.kubernetes.io/instance":"team-tools","app.kubernetes.io/managed-by":"Helm","team":"team-tools"}
```

The chart stamped `team: team-tools` on the route, which is what makes the platform's parent route pick this child up, and prepended the platform-owned prefix: the route matches `/teams/team-tools/mcp` even though the team typed only `/mcp`.

### Call the tools

MCP over Streamable HTTP is plain JSON-RPC over POST, so you can exercise the endpoint with `curl`. Give the proxy a few seconds to program the route, then initialize a session and capture the session ID the gateway returns:

```bash
sleep 10
export MCP_SESSION=$(curl -si "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
echo "MCP_SESSION=${MCP_SESSION}"
```

Expected output (the session ID is a stateless token minted by the gateway, which is why the session works across all proxy replicas):

```
MCP_SESSION=eyJ0IjoibWNwIiwicyI6W3sidCI6ImV2ZXJ5dGhpbmciLCJzIjoiODJlOTMyNTctYzhiNy00MWQ1LWEyZWMtMDNiMDM1ZTU0YTlhIn1dfQ
```

Complete the MCP handshake, then list the server's tools:

```bash
curl -s -o /dev/null -w "initialized: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "mcp-session-id: ${MCP_SESSION}" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

curl -s "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "mcp-session-id: ${MCP_SESSION}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Expected output (trimmed; the server advertises a dozen tools):

```
initialized: 202
data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"echo","title":"Echo Tool","description":"Echoes back the input string",...
```

Call the `echo` tool through the gateway:

```bash
curl -s "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "mcp-session-id: ${MCP_SESSION}" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello from team-tools"}}}'
```

Expected output:

```
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"Echo: hello from team-tools"}]}}
```

The request flowed `Gateway agw-platform → parent route team-team-tools (/teams/team-tools) → delegates to child team-tools-tools (/teams/team-tools/mcp) → entMcp backend → mcp-server-everything`. Any MCP client works the same way — point the [MCP Inspector](../mcp/in-cluster-mcp.md) at `http://<GATEWAY_IP>:8080/teams/team-tools/mcp` with transport **Streamable HTTP**.

> **What the team did NOT configure.** A team name and one endpoint. No authentication, no access-log format, no rate limit, no proxy fleet settings — the platform attaches all of those to the parent route or the gateway, and this child inherits them.

---

## Step 3: The tier budgets the team's tool calls

`silver` allows 5 tool calls per minute. The budget lives in a `RateLimitConfig` the platform chart rendered at onboarding: a CEL expression inspects each JSON-RPC body and counts only `tools/call` requests, against a **global** counter — one budget for the team, no matter how many proxy replicas serve it or how many sessions the team opens.

The counter's window is a fixed clock minute, so start from a fresh window and send eight tool calls:

```bash
sleep 60
for i in $(seq 1 8); do
  curl -s -o /dev/null -w "tool-call-$i: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
    -H "content-type: application/json" \
    -H "accept: application/json, text/event-stream" \
    -H "mcp-session-id: ${MCP_SESSION}" \
    -d '{"jsonrpc":"2.0","id":99,"method":"tools/call","params":{"name":"echo","arguments":{"message":"budget probe"}}}'
done
```

Expected output:

```
tool-call-1: 200
tool-call-2: 200
tool-call-3: 200
tool-call-4: 200
tool-call-5: 200
tool-call-6: 429
tool-call-7: 429
tool-call-8: 429
```

Five calls admitted, then `429` — the platform-assigned budget, enforced with no involvement from the team. Only tool calls are counted: with the budget exhausted, the session itself still works fine:

```bash
curl -s -o /dev/null -w "tools/list: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "mcp-session-id: ${MCP_SESSION}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Expected output:

```
tools/list: 200
```

Clients can keep connecting, discovering, and listing tools; only *executing* them draws down the budget. The budget resets at the next minute. A tier change — say `team-tools` earns an upgrade to `gold`'s 300 calls/minute — is a one-line edit to the platform's `teams` list and a `helm upgrade`, exactly like the [re-tiering step in the LLM lab](platform-and-developer-helm-charts-llm.md#step-5-the-platform-re-tiers-a-team); the team's release never changes. For rate limits on *individual* tools (for example, a tighter budget for one expensive tool), see the [MCP Tool Rate Limiting lab](../mcp/mcp-tool-rate-limiting.md).

---

## Step 4: What the team cannot do

The developer chart's `values.schema.json` uses `"additionalProperties": false`, so a traffic knob smuggled onto an MCP endpoint fails at `helm` time, before anything reaches the cluster. Try it:

```bash
cat > team-tools-cheat.yaml <<'EOF'
team: team-tools
endpoints:
  - name: tools
    type: mcp
    path: /mcp
    targets:
      - name: everything
        host: mcp-server-everything.team-tools.svc.cluster.local
        port: 8080
        protocol: StreamableHTTP
    rateLimit:
      tokensPerMinute: 1000000
EOF

helm template team-tools charts/agentgateway-developer -n team-tools --values team-tools-cheat.yaml
```

Expected output:

```
Error: values don't meet the specifications of the schema(s) in the following chart(s):
agentgateway-developer:
- at '/endpoints/0': additional properties 'rateLimit' not allowed
```

The same structural enforcement covers path hijacking and tier redefinition; the [LLM lab](platform-and-developer-helm-charts-llm.md#step-4-what-teams-cannot-do) walks through the full set of escape attempts. Remove the file:

```bash
rm -f team-tools-cheat.yaml
```

---

## Step 5: The platform enables JWT for everyone

The platform turns on JWT authentication for the whole gateway with a single upgrade. The JWT policy targets the `Gateway`, so it protects every endpoint at once — including MCP endpoints — and no team release changes.

This step reuses the inline JWKS and the static `DEV_TOKEN_1` from the [JWT Auth with RBAC lab](../security/jwt-auth-with-rbac.md). Save that lab's `keys` block into a file named `jwks.json`:

```bash
cat > jwks.json <<'EOF'
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

> **Demo-only; do not use outside this workshop.** `solo-public-key-001` and the token below are a public demo keypair shared across this workshop's JWT labs.

Enable JWT on the platform release, passing the JWKS with `--set-file`:

```bash
helm upgrade agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml \
  --set security.jwt.enabled=true \
  --set security.jwt.issuer=solo.io \
  --set-file security.jwt.jwks.inline=jwks.json
```

Confirm the platform created a gateway-scoped JWT policy:

```bash
kubectl get enterpriseagentgatewaypolicy agw-platform-jwt -n agentgateway-system
```

Expected output:

```
NAME               ACCEPTED   ATTACHED   AGE
agw-platform-jwt   True       True       2s
```

Export the demo token and give the policy a few seconds to program:

```bash
export DEV_TOKEN_1="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw"
sleep 8
```

Try to initialize an MCP session **without** a token; the gateway now rejects it:

```bash
curl -s -o /dev/null -w "no-token: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'
```

Expected output:

```
no-token: 401
```

With the token, the full flow works again. Sessions do not outlive an auth change, so initialize a fresh one, then call the tool:

```bash
export MCP_SESSION=$(curl -si "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $DEV_TOKEN_1" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')

curl -s "http://${GATEWAY_IP}:8080/teams/team-tools/mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $DEV_TOKEN_1" \
  -H "mcp-session-id: ${MCP_SESSION}" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello with a token"}}}'
```

Expected output:

```
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"Echo: hello with a token"}]}}
```

One platform-side change put every endpoint on the gateway behind JWT — the team's MCP endpoint included — and the team's release was never touched. Turning JWT back off is symmetric: upgrade with the base values file alone:

```bash
helm upgrade agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml
```

Verify the JWT policy is gone:

```bash
kubectl get enterpriseagentgatewaypolicy agw-platform-jwt -n agentgateway-system
```

Expected output:

```
Error from server (NotFound): enterpriseagentgatewaypolicies.enterpriseagentgateway.solo.io "agw-platform-jwt" not found
```

---

## Observability

Access logging is on because the platform chart enabled it, so every MCP request the team serves is logged from the shared gateway. The proxy understands the MCP protocol, so the log records which method and which tool was called — not just an opaque POST. View the logs:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agw-platform --prefix --tail 20
```

Each MCP request shows its route, status, and MCP-specific attributes, for example:

```
...route=team-tools/team-tools-tools ... http.status=200 protocol=mcp mcp.method.name=initialize mcp.session.id=9309830d-... duration=8ms
...route=team-tools/team-tools-tools ... http.status=200 jwt.sub=user-id protocol=mcp mcp.method.name=tools/call mcp.target=everything mcp.resource.type=tool gen_ai.tool.name=echo ...
...route=team-tools/team-tools-tools ... http.status=429 protocol=http reason=DirectResponse duration=0ms
...route=team-tools/team-tools-tools ... http.status=401 protocol=http error="authentication failure: no bearer token found" reason=JwtAuth duration=0ms
```

Note the second line: with JWT enabled, the platform's logs attribute every tool call to a subject (`jwt.sub=user-id`) — per-user MCP auditing, with zero configuration by the team. To enable Prometheus scraping, the platform team sets `observability.metrics.enabled=true` (requires the prometheus-operator `PodMonitor` CRD). For dashboards and traces, use the Grafana stack from `002`; the AgentGateway dashboard has dedicated MCP panels (tool calls, server requests).

---

## Cleanup

Uninstall the developer release, then the platform release, then delete the team namespace (the `001` `agentgateway-proxy` gateway is untouched):

```bash
helm uninstall team-tools -n team-tools
helm uninstall agw-platform -n agentgateway-system
kubectl delete namespace team-tools --ignore-not-found
```

Remove the local files you created:

```bash
rm -f platform-values.yaml team-tools-values.yaml team-tools-cheat.yaml jwks.json
```

Confirm the platform gateway is gone and the `001` gateway survives:

```bash
kubectl get gateway agw-platform -n agentgateway-system
kubectl get gateway agentgateway-proxy -n agentgateway-system
```

Expected output:

```
Error from server (NotFound): gateways.gateway.networking.k8s.io "agw-platform" not found
NAME                 CLASS                     ADDRESS          PROGRAMMED   AGE
agentgateway-proxy   enterprise-agentgateway   172.18.255.254   True         31h
```
