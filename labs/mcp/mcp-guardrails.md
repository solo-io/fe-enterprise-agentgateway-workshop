# Guard MCP Tool Calls with an External Policy Server

In this lab, you put a policy server between agents and a procurement MCP server. Each caller sees only the tools it is entitled to, a purchase order above the caller's limit is denied with a message that approval is required, email can only go to approved domains, and bank account and tax ID values are masked before a tool result reaches the model.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

- `jq`, `openssl`, `base64`, and `bash` on your local machine (`openssl` and `base64` are used by the `lib/jwt/generate-jwt.sh` helper)
- The MCP server and both policy servers run in your cluster, and `lib/jwt/generate-jwt.sh` signs tokens with a key it generates locally.

> **Version:** MCP guardrails are available in Enterprise Agentgateway **v2026.8.2 and later**.

## Lab Objectives
- Deploy a procurement MCP server behind agentgateway with JWT authentication
- Attach an ExtMCP guardrails processor to an `EnterpriseAgentgatewayBackend` with an `EnterpriseAgentgatewayPolicy`
- Filter `tools/list` and gate `tools/call` per caller, using a JWT claim passed to the policy server
- Deny purchase orders above a per-persona limit and tell the agent that approval is required
- Block email to unapproved domains and redact bank account and tax ID values from tool results
- Compare a `FailClosed` and a `FailOpen` processor when their policy servers go down
- Change policy at runtime by editing a ConfigMap

## Overview

MCP guardrails (also called ExtMCP) call an external gRPC policy server at the MCP method layer. For each method you opt in, agentgateway sends the server the JSON-RPC method, the target backend, the request `params` or response `result`, and caller metadata computed with CEL. The server passes the message, returns a rewritten one, or denies it. A denied `tools/call` request reaches the client as a tool result with `isError: true` and the policy server's message as text, so the agent can read why the call failed. Any other denial reaches the client as a JSON-RPC error.

![MCP guardrails architecture: an MCP client sends a persona JWT to agentgateway-proxy, which authenticates the JWT, routes /procurement/mcp to the procurement-mcp server over Streamable HTTP, and calls two ExtMCP policy servers over gRPC in order: extmcp-authz (FailClosed) gates tools/call requests and filters tools/list responses using the persona and sub claims, and extmcp-redact (FailOpen) masks bank account and tax ID values in tools/call responses. Each policy server reads its policy from a ConfigMap mounted at /etc/extmcp](../../images/mcp/mcp-guardrails-architecture.png)

Each place you can put MCP authorization logic sees a different part of the call:

| Mechanism | What it sees | What it can do |
|---|---|---|
| [ext_authz](mcp-byo-grpc-ext-authz.md) | HTTP method, path, headers | Allow or deny the HTTP request |
| [`mcp.authorization` CEL rules](mcp-tool-federation.md#step-7-persona-based-tool-filtering) | Tool name and JWT claims | Allow or deny a tool |
| ExtMCP guardrails | Method, tool, argument values, results, caller metadata | Allow, deny, or rewrite the request or the result |

A limit on a purchase-order amount needs the argument value, so it belongs in ExtMCP.

> **Tip:** CEL rules evaluate inside the proxy. For per-persona tool scoping alone, a CEL rule on the backend is enough. This lab scopes tools in ExtMCP to keep all procurement policy in one ConfigMap. To use both, scope tools with CEL and check argument values and results with ExtMCP.

Each processor lists the methods it handles and the phase in which agentgateway calls it:

| Phase | When the policy server is called |
|---|---|
| `Request` | Before the call reaches the MCP server. Use it to gate or rewrite the call. |
| `Response` | After the MCP server returns. Use it to filter or rewrite the result. |
| `Full` | Both. |
| `Off` | Never. |

For a denial that reaches the client as a JSON-RPC error, the code the policy server returns sets the error code:

| Policy server code | JSON-RPC error code |
|---|---|
| `PERMISSION_DENIED` | `-32001` |
| `RESOURCE_EXHAUSTED` | `-32003` |
| `INVALID` | `-32600` (invalid request) |

### About the policy server

This lab uses [`extmcp-guardrails`](https://github.com/ably77/extmcp-guardrails), a small Go ExtMCP server that reads its policy from a mounted ConfigMap. You run the same image twice: once in `authz` mode for entitlements, limits, and the email allowlist, and once in `redact` mode for masking. The repo README documents every policy key; fork it to add your own checks.

The `authz` policy in this lab defines three personas. The persona comes from the `persona` claim in the caller's JWT.

| Persona | Tools | Purchase order limit |
|---|---|---|
| `requester` | `get_supplier`, `list_purchase_orders`, `create_purchase_order` | $1,000 |
| `buyer` | the requester's tools plus `send_supplier_email` | $10,000 |
| `finance-approver` | all tools, including `delete_supplier` | $250,000 |

---

## Step 1 — Deploy the Procurement MCP Server

Deploy the mock procurement MCP server, route `/procurement/mcp` to it, and require a JWT signed by the workshop's demo key under `lib/jwt/`. Run the commands in this lab from the root of the workshop repo.

`lib/jwt/generate-jwt.sh` creates the demo key pair and `lib/jwt/jwks.json` on its first run. Mint a throwaway token so the file exists before the policy below inlines it:

```bash
./lib/jwt/generate-jwt.sh lib/jwt/claims/buyer.json > /dev/null
```

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: procurement
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: procurement-mcp
  namespace: procurement
spec:
  replicas: 1
  selector:
    matchLabels:
      app: procurement-mcp
  template:
    metadata:
      labels:
        app: procurement-mcp
    spec:
      containers:
        - name: procurement-mcp
          image: docker.io/ably7/procurement-mcp:0.1.1
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8000
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: procurement-mcp
  namespace: procurement
spec:
  selector:
    app: procurement-mcp
  ports:
    - name: mcp-http
      port: 80
      targetPort: 8000
      appProtocol: agentgateway.dev/mcp
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: procurement-mcp
  namespace: procurement
spec:
  mcp:
    targets:
      - name: procurement
        static:
          host: procurement-mcp.procurement.svc.cluster.local
          port: 80
          protocol: StreamableHTTP
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: procurement-mcp
  namespace: procurement
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /procurement/mcp
      backendRefs:
        - name: procurement-mcp
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: procurement-jwt
  namespace: procurement
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: procurement-mcp
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: workshop.solo.io
          jwks:
            inline: |
$(sed 's/^/              /' lib/jwt/jwks.json)
EOF
kubectl rollout status -n procurement deploy/procurement-mcp --timeout=180s
```

> The `$(sed ...)` substitution inlines `lib/jwt/jwks.json` with the indentation YAML needs.

### Get the gateway address

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo $GATEWAY_IP
```

```bash
export MCP_URL="http://$GATEWAY_IP:8080/procurement/mcp"
```

### Mint persona tokens

Sign one token per persona with the workshop's demo key, then decode the buyer's claims:

```bash
export REQUESTER=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/requester.json)
export BUYER=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/buyer.json)
export APPROVER=$(./lib/jwt/generate-jwt.sh lib/jwt/claims/finance-approver.json)
echo "$BUYER" | jq -R 'split(".")[1] | gsub("-";"+") | gsub("_";"/") | @base64d | fromjson'
```

```json
{
  "iss": "workshop.solo.io",
  "sub": "bailey-buyer",
  "exp": 4070908800,
  "persona": "buyer",
  "org": "procurement",
  "team": "purchasing"
}
```

### Define the MCP helper

Every MCP call needs an `initialize` handshake and a session ID before the real request. `mcp_call` runs the handshake and one call, and prints the JSON-RPC response. `list_tools` prints only the tool names.

```bash
mcp_call() {
  local token="$1" method="$2" params="${3:-}" sid
  [ -z "$params" ] && params='{}'
  local h=(-H "Authorization: Bearer $token" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")
  sid=$(curl -s -D - -o /dev/null "$MCP_URL" "${h[@]}" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
    | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
  curl -s -o /dev/null "$MCP_URL" "${h[@]}" -H "mcp-session-id: $sid" \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  curl -s "$MCP_URL" "${h[@]}" -H "mcp-session-id: $sid" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"$method\",\"params\":$params}" \
    | sed -n 's/^data: //;/^{/p' | jq .
}

list_tools() {
  mcp_call "$1" tools/list | jq -r '.result.tools[].name'
}
```

### Test the baseline

A request without a token gets `401`. With a token, every persona sees every tool, and `get_supplier` returns the supplier's bank account and tax ID:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$MCP_URL" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'
list_tools "$REQUESTER"
mcp_call "$REQUESTER" tools/call '{"name":"get_supplier","arguments":{"id":"SUP-001"}}' | jq .result.structuredContent
```

```
401
create_purchase_order
delete_supplier
get_supplier
list_purchase_orders
send_supplier_email
{
  "bankAccount": "004815162342",
  "contactEmail": "acme@try-solo.io",
  "country": "US",
  "id": "SUP-001",
  "name": "Acme Industrial Supply",
  "taxId": "94-3175520"
}
```

The requester can see and call `delete_supplier`, and payment details flow back to whatever agent made the call.

---

## Step 2 — Scope Tools to Each Persona

### Deploy the authorization policy server

The `authz` policy lives in a ConfigMap that the policy server mounts at `/etc/extmcp`. The Service uses `appProtocol: kubernetes.io/h2c` so agentgateway speaks gRPC to it over cleartext HTTP/2.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: extmcp-authz-config
  namespace: procurement
data:
  config.yaml: |
    mode: authz
    personas:
      requester:
        tools:
          - get_supplier
          - list_purchase_orders
          - create_purchase_order
        poLimit: 1000
      buyer:
        tools:
          - get_supplier
          - list_purchase_orders
          - create_purchase_order
          - send_supplier_email
        poLimit: 10000
      finance-approver:
        tools:
          - "*"
        poLimit: 250000
    disabledTools: []
    email:
      allowedDomains:
        - try-solo.io
    approval:
      url: https://approvals.try-solo.io/requests
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: extmcp-authz
  namespace: procurement
spec:
  replicas: 1
  selector:
    matchLabels:
      app: extmcp-authz
  template:
    metadata:
      labels:
        app: extmcp-authz
    spec:
      containers:
        - name: extmcp
          image: docker.io/ably7/extmcp-guardrails:0.1.1
          ports:
            - containerPort: 9001
          readinessProbe:
            grpc:
              port: 9001
            periodSeconds: 5
          volumeMounts:
            - name: policy
              mountPath: /etc/extmcp
      volumes:
        - name: policy
          configMap:
            name: extmcp-authz-config
---
apiVersion: v1
kind: Service
metadata:
  name: extmcp-authz
  namespace: procurement
spec:
  selector:
    app: extmcp-authz
  ports:
    - name: grpc
      port: 4445
      targetPort: 9001
      appProtocol: kubernetes.io/h2c
EOF
kubectl rollout status -n procurement deploy/extmcp-authz --timeout=180s
```

### Attach the guardrails policy

Attach the policy server to the procurement backend:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: procurement-guardrails
  namespace: procurement
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: procurement-mcp
  backend:
    mcp:
      guardrails:
        processors:
          - remote:
              backendRef:
                name: extmcp-authz
                port: 4445
              failureMode: FailClosed
              metadata:
                persona: jwt.persona
                sub: jwt.sub
            methods:
              tools/call: Request
              tools/list: Response
EOF
```

| Setting | Description |
|---|---|
| `remote.backendRef` | The policy server's Service and port. |
| `remote.failureMode: FailClosed` | If the policy server is unreachable or errors, the call fails. |
| `remote.metadata` | CEL expressions evaluated per request and sent to the policy server as `metadata_context`. Here the policy server receives the caller's `persona` and `sub` claims. JWT authentication runs before any processor, so the claims are already verified. |
| `methods` | `tools/call: Request` lets the server allow or deny each call before it reaches the MCP server. `tools/list: Response` lets it filter the tool list after the MCP server returns it. |

### Test tool scoping

List the tools each persona sees, then have the requester call `delete_supplier` directly:

```bash
echo "--- requester"; list_tools "$REQUESTER"
echo "--- buyer"; list_tools "$BUYER"
echo "--- finance-approver"; list_tools "$APPROVER"
mcp_call "$REQUESTER" tools/call '{"name":"delete_supplier","arguments":{"id":"SUP-003"}}'
```

```
--- requester
create_purchase_order
get_supplier
list_purchase_orders
--- buyer
create_purchase_order
get_supplier
list_purchase_orders
send_supplier_email
--- finance-approver
create_purchase_order
delete_supplier
get_supplier
list_purchase_orders
send_supplier_email
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "persona requester may not call delete_supplier"
      }
    ],
    "isError": true
  }
}
```

A client can call a tool it was never shown, so hiding a tool from `tools/list` only changes what the client sees. The same policy server gates `tools/call`, and that gate denies the direct call.

---

## Step 3 — Gate Purchase Orders on Amount

The Step 2 policy already reads the `amount` argument of `create_purchase_order`. The buyer's limit is $10,000:

```bash
mcp_call "$BUYER" tools/call '{"name":"create_purchase_order","arguments":{"supplier":"SUP-002","amount":5000,"description":"Pallet racking"}}' | jq .result.structuredContent
mcp_call "$BUYER" tools/call '{"name":"create_purchase_order","arguments":{"supplier":"SUP-002","amount":25000,"description":"Forklift"}}'
```

```json
{
  "amount": 5000,
  "description": "Pallet racking",
  "id": "PO-1004",
  "status": "created",
  "supplier": "SUP-002"
}
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "amount $25000 exceeds the $10000 limit for buyer; approval required"
      }
    ],
    "isError": true
  }
}
```

The message names the limit and says approval is required, so the agent can tell the user what happened. The policy server also builds an approval payload with the `approval.url` from its policy, but agentgateway passes only the message to the client for a denied `tools/call`. The finance approver's limit covers the same order:

```bash
mcp_call "$APPROVER" tools/call '{"name":"create_purchase_order","arguments":{"supplier":"SUP-002","amount":25000,"description":"Forklift"}}' | jq .result.structuredContent
```

```json
{
  "amount": 25000,
  "description": "Forklift",
  "id": "PO-1005",
  "status": "created",
  "supplier": "SUP-002"
}
```

An amount sent as a string is rejected before any comparison:

```bash
mcp_call "$BUYER" tools/call '{"name":"create_purchase_order","arguments":{"supplier":"SUP-002","amount":"25000"}}'
```

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "create_purchase_order needs a numeric amount"
      }
    ],
    "isError": true
  }
}
```

> **Approval happens outside the gateway.** The gateway returns the denial and ends the call. Your approval system records the approval, and the agent re-submits the order under an identity whose limit covers it.

---

## Step 4 — Block Exfiltration and Redact Payment Data

### Deploy the redaction policy server

The second instance of the same image runs in `redact` mode. Each pattern's matches become `<NAME>` in tool results.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: extmcp-redact-config
  namespace: procurement
data:
  config.yaml: |
    mode: redact
    patterns:
      - name: BANK_ACCOUNT
        regex: '\b\d{8,17}\b'
      - name: TAX_ID
        regex: '\b\d{2}-\d{7}\b'
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: extmcp-redact
  namespace: procurement
spec:
  replicas: 1
  selector:
    matchLabels:
      app: extmcp-redact
  template:
    metadata:
      labels:
        app: extmcp-redact
    spec:
      containers:
        - name: extmcp
          image: docker.io/ably7/extmcp-guardrails:0.1.1
          ports:
            - containerPort: 9001
          readinessProbe:
            grpc:
              port: 9001
            periodSeconds: 5
          volumeMounts:
            - name: policy
              mountPath: /etc/extmcp
      volumes:
        - name: policy
          configMap:
            name: extmcp-redact-config
---
apiVersion: v1
kind: Service
metadata:
  name: extmcp-redact
  namespace: procurement
spec:
  selector:
    app: extmcp-redact
  ports:
    - name: grpc
      port: 4445
      targetPort: 9001
      appProtocol: kubernetes.io/h2c
EOF
kubectl rollout status -n procurement deploy/extmcp-redact --timeout=180s
```

### Chain the redaction processor

Add the redaction server as a second processor. Processors run in the order listed, and the first denial stops the chain. The authorization processor is `FailClosed` because an unreachable authorizer must not mean "allow". The redaction processor is `FailOpen` here so that Step 5 can show what that choice costs.

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: procurement-guardrails
  namespace: procurement
spec:
  targetRefs:
    - group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
      name: procurement-mcp
  backend:
    mcp:
      guardrails:
        processors:
          - remote:
              backendRef:
                name: extmcp-authz
                port: 4445
              failureMode: FailClosed
              metadata:
                persona: jwt.persona
                sub: jwt.sub
            methods:
              tools/call: Request
              tools/list: Response
          - remote:
              backendRef:
                name: extmcp-redact
                port: 4445
              failureMode: FailOpen
            methods:
              tools/call: Response
EOF
```

### Test the exfiltration controls

Try to email supplier details to an outside address, then to a look-alike domain, then to an approved supplier contact. Finally, look up a supplier again:

```bash
mcp_call "$BUYER" tools/call '{"name":"send_supplier_email","arguments":{"to":"x@gmail.com","subject":"Bank details","body":"See attached"}}' | jq -c .result
mcp_call "$BUYER" tools/call '{"name":"send_supplier_email","arguments":{"to":"ap@try-solo.io.attacker.example","subject":"Bank details","body":"See attached"}}' | jq -c .result
mcp_call "$BUYER" tools/call '{"name":"send_supplier_email","arguments":{"to":"globex@try-solo.io","subject":"PO-1002","body":"Please confirm delivery"}}' | jq -c .result.structuredContent
mcp_call "$BUYER" tools/call '{"name":"get_supplier","arguments":{"id":"SUP-001"}}' | jq .result.structuredContent
```

```
{"content":[{"type":"text","text":"recipient domain gmail.com not allowed"}],"isError":true}
{"content":[{"type":"text","text":"recipient domain try-solo.io.attacker.example not allowed"}],"isError":true}
{"status":"queued","to":"globex@try-solo.io"}
{
  "bankAccount": "<BANK_ACCOUNT>",
  "contactEmail": "acme@try-solo.io",
  "country": "US",
  "id": "SUP-001",
  "name": "Acme Industrial Supply",
  "taxId": "<TAX_ID>"
}
```

The allowlist matches whole domains, so `try-solo.io.attacker.example` does not pass as `try-solo.io`. The redaction server masks both the structured result and its text copy, so the model receives only the masked values:

```bash
mcp_call "$BUYER" tools/call '{"name":"get_supplier","arguments":{"id":"SUP-001"}}' | jq -r '.result.content[0].text'
```

```
{"bankAccount":"<BANK_ACCOUNT>","contactEmail":"acme@try-solo.io","country":"US","id":"SUP-001","name":"Acme Industrial Supply","taxId":"<TAX_ID>"}
```

---

## Step 5 — Compare Failure Modes

Scale each policy server to zero and see how its failure mode changes the result.

### Take down the redaction server

```bash
kubectl scale -n procurement deploy/extmcp-redact --replicas=0
kubectl wait -n procurement --for=delete pod -l app=extmcp-redact --timeout=60s || true
mcp_call "$BUYER" tools/call '{"name":"get_supplier","arguments":{"id":"SUP-001"}}' | jq .result.structuredContent
```

```json
{
  "bankAccount": "004815162342",
  "contactEmail": "acme@try-solo.io",
  "country": "US",
  "id": "SUP-001",
  "name": "Acme Industrial Supply",
  "taxId": "94-3175520"
}
```

The call succeeds with the raw values. `FailOpen` kept the agent working and let unredacted data through. Choose it only for processors whose absence you can accept.

### Take down the authorization server

```bash
kubectl scale -n procurement deploy/extmcp-authz --replicas=0
kubectl wait -n procurement --for=delete pod -l app=extmcp-authz --timeout=60s || true
mcp_call "$APPROVER" tools/call '{"name":"list_purchase_orders","arguments":{}}' | jq -c .
mcp_call "$APPROVER" tools/list | jq -c .
```

```
{"jsonrpc":"2.0","id":2,"error":{"code":-32603,"message":"mcpGuardrails checkRequest failed: no healthy backends"}}
{"jsonrpc":"2.0","id":2,"error":{"code":-32603,"message":"mcpGuardrails checkResponse failed: no healthy backends"}}
```

Every governed call fails, including the finance approver's.

| Processor | `failureMode` | With its server down | Consequence |
|---|---|---|---|
| `extmcp-authz` | `FailClosed` | Governed calls fail with a JSON-RPC error | No unauthorized call reaches the MCP server |
| `extmcp-redact` | `FailOpen` | Calls succeed unredacted | Agents keep working, payment data is exposed |

> **Note:** A slow policy server is bounded too. The gateway waits up to 10 seconds for a decision, then applies the processor's failure mode. To shorten the wait, set `backend.http.requestTimeout` in an `EnterpriseAgentgatewayPolicy` that targets the policy server's Service. That policy attaches only once a route references the Service.

### Restore both servers

```bash
kubectl scale -n procurement deploy/extmcp-authz deploy/extmcp-redact --replicas=1
kubectl rollout status -n procurement deploy/extmcp-authz --timeout=120s
kubectl rollout status -n procurement deploy/extmcp-redact --timeout=120s
mcp_call "$BUYER" tools/call '{"name":"get_supplier","arguments":{"id":"SUP-001"}}' | jq -r .result.structuredContent.bankAccount
```

```
<BANK_ACCOUNT>
```

---

## Step 6 — Change Policy Without Touching the Gateway

Lower the buyer's limit to $2,500 and switch off `delete_supplier` for every persona by editing the ConfigMap. The gateway configuration stays as it is.

```bash
kubectl patch configmap -n procurement extmcp-authz-config --type merge -p "$(cat <<'EOF'
data:
  config.yaml: |
    mode: authz
    personas:
      requester:
        tools:
          - get_supplier
          - list_purchase_orders
          - create_purchase_order
        poLimit: 1000
      buyer:
        tools:
          - get_supplier
          - list_purchase_orders
          - create_purchase_order
          - send_supplier_email
        poLimit: 2500
      finance-approver:
        tools:
          - "*"
        poLimit: 250000
    disabledTools:
      - delete_supplier
    email:
      allowedDomains:
        - try-solo.io
    approval:
      url: https://approvals.try-solo.io/requests
EOF
)"
```

The kubelet refreshes ConfigMap volumes on its sync period, which can take a minute or two, and the policy server checks the file every 5 seconds. Wait for the reload:

```bash
until kubectl logs -n procurement deploy/extmcp-authz --since=5m | grep -q '"msg":"policy reloaded"'; do sleep 5; done
kubectl logs -n procurement deploy/extmcp-authz --since=5m | grep '"msg":"policy reloaded"' | tail -1
```

Repeat the buyer's $5,000 order from Step 3, then check the finance approver's tools:

```bash
mcp_call "$BUYER" tools/call '{"name":"create_purchase_order","arguments":{"supplier":"SUP-002","amount":5000}}' | jq -c .result
list_tools "$APPROVER"
mcp_call "$APPROVER" tools/call '{"name":"delete_supplier","arguments":{"id":"SUP-003"}}' | jq -c .result
```

```
{"content":[{"type":"text","text":"amount $5000 exceeds the $2500 limit for buyer; approval required"}],"isError":true}
create_purchase_order
get_supplier
list_purchase_orders
send_supplier_email
{"content":[{"type":"text","text":"tool delete_supplier is disabled"}],"isError":true}
```

The order that passed in Step 3 now needs approval, and `delete_supplier` is gone for every persona, including the one whose policy allows all tools. If an edit does not parse, the policy server keeps enforcing the previous policy and logs `policy reload failed`.

---

## Observability

### View guardrail decisions

Each policy server logs one JSON line per decision, with the persona and subject that made the call:

```bash
kubectl logs -n procurement deploy/extmcp-authz --tail=50 \
  | jq -c 'select(.msg=="allow" or .msg=="deny" or .msg=="filter") | {msg, persona, sub, tool, reason, removed} | with_entries(select(.value != null))'
kubectl logs -n procurement deploy/extmcp-redact --tail=50 | jq -c 'select(.msg=="redact") | {msg, hits}'
```

```
{"msg":"allow","persona":"buyer","sub":"bailey-buyer","tool":"get_supplier"}
{"msg":"deny","persona":"buyer","sub":"bailey-buyer","tool":"create_purchase_order","reason":"amount $5000 exceeds the $2500 limit for buyer; approval required"}
{"msg":"filter","persona":"finance-approver","sub":"fran-finance","removed":["delete_supplier"]}
{"msg":"deny","persona":"finance-approver","sub":"fran-finance","tool":"delete_supplier","reason":"tool delete_supplier is disabled"}
{"msg":"redact","hits":{"BANK_ACCOUNT":2,"TAX_ID":2}}
```

The authorization server restarted in Step 5, so its log starts there. Each redaction counts twice because the result carries the values in both `structuredContent` and the text content.

### View access logs

Each MCP request's log line carries `mcp.method.name`, `mcp.resource.type`, `mcp.target`, and `mcp.session.id`, plus a `trace.id` you can search for in the Solo UI's **Tracing** view. When a processor denies a call, the gateway's log line for that request carries the policy server's reason in `error`, next to `jwt.sub` and `gen_ai.tool.name`. List the distinct rejections from this lab:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --tail=500 --prefix=false \
  | grep 'route=procurement/procurement-mcp' | grep 'mcp.method.name=tools/call' | grep 'mcpGuardrails rejected' \
  | grep -o 'jwt.sub=[^ ]*\|gen_ai.tool.name=[^ ]*\|error="[^"]*"' | paste - - - | sort -u
```

```
jwt.sub=bailey-buyer	gen_ai.tool.name=create_purchase_order	error="mcp: mcpGuardrails rejected: amount $25000 exceeds the $10000 limit for buyer; approval required"
jwt.sub=bailey-buyer	gen_ai.tool.name=create_purchase_order	error="mcp: mcpGuardrails rejected: amount $5000 exceeds the $2500 limit for buyer; approval required"
jwt.sub=bailey-buyer	gen_ai.tool.name=create_purchase_order	error="mcp: mcpGuardrails rejected: create_purchase_order needs a numeric amount"
jwt.sub=bailey-buyer	gen_ai.tool.name=send_supplier_email	error="mcp: mcpGuardrails rejected: recipient domain gmail.com not allowed"
jwt.sub=bailey-buyer	gen_ai.tool.name=send_supplier_email	error="mcp: mcpGuardrails rejected: recipient domain try-solo.io.attacker.example not allowed"
jwt.sub=fran-finance	gen_ai.tool.name=delete_supplier	error="mcp: mcpGuardrails rejected: tool delete_supplier is disabled"
jwt.sub=fran-finance	gen_ai.tool.name=list_purchase_orders	error="mcp: mcpGuardrails rejected: mcpGuardrails checkRequest failed: no healthy backends"
jwt.sub=riley-requester	gen_ai.tool.name=delete_supplier	error="mcp: mcpGuardrails rejected: persona requester may not call delete_supplier"
```

## Key Takeaways

- ExtMCP guardrails see tool argument values, so a policy can act on an amount, a recipient, or any other parameter.
- Filtering `tools/list` and gating `tools/call` in one policy server keeps what a caller sees and what a caller can do in agreement.
- Response-phase processors can mask sensitive values in tool results before they reach the model.
- Each processor picks its own failure mode: `FailClosed` for decisions that must happen, `FailOpen` only where losing the processor is acceptable.
- Policy lives in the policy server's configuration, so a ConfigMap edit changes limits and switches tools off with no gateway change.

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n procurement procurement-guardrails procurement-jwt --ignore-not-found
kubectl delete httproute -n procurement procurement-mcp --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n procurement procurement-mcp --ignore-not-found
kubectl delete namespace procurement --ignore-not-found
unset REQUESTER BUYER APPROVER MCP_URL
unset -f mcp_call list_tools
```
