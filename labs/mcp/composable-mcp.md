# Composable MCP — Tool Aggregation & Orchestration

Most "give the agent one useful tool" stories actually require data from several backends. A support agent asking "what's this account's status?" needs a CRM record *and* an open-ticket count *and* maybe a billing balance — three services, one question. **Composable MCP** lets you declare a single MCP tool that fans a call out to distinct backends, runs the necessary steps, and merges the results into one response, so the calling agent never has to know there were multiple hops.

> Unlike [MCP Tool Federation](mcp-tool-federation.md), which routes each tool call to its owning backend (the client still makes N calls), Composable MCP fans one call out to distinct backends and merges the responses into a single result.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Declare a composite MCP tool that fans out to distinct backends
- Chain an MCP step and an HTTP step in the same composite tool
- Reference an earlier step's output from a later step (sequential orchestration)
- Compare a string-valued tool output against a structured (`structuredContent`) tool output
- (Bonus) Inject caller identity into a backend call using a CEL expression over the caller's JWT

## Overview

### Aggregation vs. federation

Both patterns let a client reach several backends through one MCP endpoint, but they solve different problems:

| | MCP Tool Federation | Composable MCP (this lab) |
|---|---|---|
| What the client calls | N tools, one per backend | 1 composite tool |
| Calls per "logical" request | N (client orchestrates) | 1 (gateway orchestrates) |
| Where the fan-out happens | Client / agent | Gateway (declarative `steps`) |
| Response shape | N separate tool results | 1 merged result (string or structured) |
| Adding a backend | Add another federated target; client learns a new tool | Add a `step`; existing tool callers see no interface change |

### `account-brief`: the composite tool built in this lab

Across this lab you build one composite tool, `account-brief`, step by step. It fans out to two distinct backends — an MCP backend (`accounts-mcp`, wrapping the `accounts-api` REST service via openapi-to-mcp) and a plain HTTP backend (`orders-api`) — and merges their responses:

```
              tools/call: account-brief({"account_id": "1"})
                              │
                              ▼
                 ┌─────────────────────────┐
                 │  EnterpriseAgentgateway  │
                 │  Backend: composable-mcp │
                 │  target: account-brief   │
                 └────────────┬─────────────┘
                               │
              ┌───────────────┴────────────────┐
              │  steps run in order:            │
              │                                  │
        step: account                     step: orders
        (mcp step)                        (http step)
              │                                  │
              ▼                                  ▼
      ┌───────────────┐                 ┌────────────────┐
      │ accounts-mcp  │                 │  orders-api    │
      │ (getAccount)  │                 │  (GET /orders) │
      └───────────────┘                 └────────────────┘
              │                                  │
              └───────────────┬──────────────────┘
                               ▼
                    output CEL merges both
                   step results into one result
                (string, then later structured)
```

The two backends Step 1 deploys — `accounts-api` and `orders-api` — are the REST mocks that `account-brief` will eventually fan out to (directly at first, then via an MCP wrapper in later steps).

---

## Step 1 — Deploy the backends

Deploy two independent `nginx:alpine` mocks into a dedicated `composable-mcp` namespace. Each serves fixed JSON via nginx's inline `return` directive — no file mounts, no app code, fully deterministic:

```bash
kubectl create namespace composable-mcp --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: accounts-nginx
  namespace: composable-mcp
data:
  default.conf: |
    server {
      listen 80;
      default_type application/json;
      location = /accounts/1 { return 200 '{"id":"1","name":"Globex","tier":"gold","region":"us-east","owner":"alice"}'; }
      location = /accounts/2 { return 200 '{"id":"2","name":"Initech","tier":"silver","region":"eu-west","owner":"bob"}'; }
      location = /healthz { return 200 '{"ok":true}'; }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: accounts-api
  namespace: composable-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: accounts-api
  template:
    metadata:
      labels:
        app: accounts-api
    spec:
      containers:
      - name: nginx
        image: nginx:alpine
        ports:
        - containerPort: 80
        volumeMounts:
        - name: conf
          mountPath: /etc/nginx/conf.d
      volumes:
      - name: conf
        configMap:
          name: accounts-nginx
---
apiVersion: v1
kind: Service
metadata:
  name: accounts-api
  namespace: composable-mcp
spec:
  selector:
    app: accounts-api
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: orders-nginx
  namespace: composable-mcp
data:
  default.conf: |
    server {
      listen 80;
      default_type application/json;
      location = /orders/1        { return 200 '{"region":"us-east","open":3,"orders":[{"id":"o-1","amt":120},{"id":"o-2","amt":75},{"id":"o-3","amt":200}]}'; }
      location = /orders/us-east  { return 200 '{"region":"us-east","open":3,"orders":[{"id":"o-1","amt":120},{"id":"o-2","amt":75},{"id":"o-3","amt":200}]}'; }
      location = /orders/2        { return 200 '{"region":"eu-west","open":1,"orders":[{"id":"o-9","amt":50}]}'; }
      location = /orders/eu-west  { return 200 '{"region":"eu-west","open":1,"orders":[{"id":"o-9","amt":50}]}'; }
      location = /healthz { return 200 '{"ok":true}'; }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orders-api
  namespace: composable-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: orders-api
  template:
    metadata:
      labels:
        app: orders-api
    spec:
      containers:
      - name: nginx
        image: nginx:alpine
        ports:
        - containerPort: 80
        volumeMounts:
        - name: conf
          mountPath: /etc/nginx/conf.d
      volumes:
      - name: conf
        configMap:
          name: orders-nginx
---
apiVersion: v1
kind: Service
metadata:
  name: orders-api
  namespace: composable-mcp
spec:
  selector:
    app: orders-api
  ports:
  - port: 80
    targetPort: 80
EOF
kubectl wait --for=condition=available deploy/accounts-api deploy/orders-api -n composable-mcp --timeout=90s
```

You should see output similar to the following:
```
namespace/composable-mcp created
configmap/accounts-nginx created
deployment.apps/accounts-api created
service/accounts-api created
configmap/orders-nginx created
deployment.apps/orders-api created
service/orders-api created
deployment.apps/accounts-api condition met
deployment.apps/orders-api condition met
```

### Assert the mock data

Run a throwaway `curl` pod inside the cluster and hit both Services directly by their in-cluster DNS names (`accounts-api`, `orders-api` — Kubernetes resolves these within the `composable-mcp` namespace without an FQDN):

```bash
kubectl run curltest --rm -it --restart=Never -n composable-mcp --image=curlimages/curl -- \
  sh -c 'curl -s -i http://accounts-api/accounts/1; echo; curl -s http://orders-api/orders/us-east'
```

You should see output similar to the following:
```
HTTP/1.1 200 OK
Server: nginx/1.31.1
Date: Wed, 08 Jul 2026 21:34:03 GMT
Content-Type: application/json
Content-Length: 75
Connection: keep-alive

{"id":"1","name":"Globex","tier":"gold","region":"us-east","owner":"alice"}
{"region":"us-east","open":3,"orders":[{"id":"o-1","amt":120},{"id":"o-2","amt":75},{"id":"o-3","amt":200}]}
pod "curltest" deleted from composable-mcp namespace
```

Both assertions hold: `accounts-api` returns `Content-Type: application/json` with the expected Globex profile, and `orders-api` returns the expected `us-east` order summary. Composable MCP's HTTP steps require a JSON 2xx response from every upstream call, so this confirms both mocks are ready to be used as step targets in the steps that follow.

---

## Step 2 — Expose the accounts service as MCP

`account-brief` fans out to `accounts-api` through an **MCP** step, not an HTTP step, so `accounts-api` first needs an MCP front door of its own. This reuses the [OpenAPI to MCP — In-Cluster Deployment](openapi-to-mcp-in-cluster.md) pattern verbatim: store a small OpenAPI 3.0 schema in a `ConfigMap` and reference it from an `EnterpriseAgentgatewayBackend` with `entMcp.targets[].static.protocol: OpenAPI`. Here the schema describes a single operation, `getAccount`, for `GET /accounts/{id}`.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: accounts-openapi
  namespace: composable-mcp
data:
  schema: |
    {
      "openapi": "3.0.0",
      "info": { "title": "Accounts", "version": "1.0" },
      "servers": [{ "url": "/" }],
      "paths": {
        "/accounts/{id}": {
          "get": {
            "operationId": "getAccount",
            "summary": "Get an account profile by ID",
            "parameters": [
              { "name": "id", "in": "path", "required": true, "schema": { "type": "string" } }
            ],
            "responses": { "200": { "description": "account profile" } }
          }
        }
      }
    }
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: accounts-mcp
  namespace: composable-mcp
spec:
  entMcp:
    targets:
    - name: accounts
      static:
        host: accounts-api.composable-mcp.svc.cluster.local
        port: 80
        protocol: OpenAPI
        openAPI:
          schemaRef:
            name: accounts-openapi
EOF
```

You should see output similar to the following:
```
configmap/accounts-openapi created
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/accounts-mcp created
```

Confirm the backend was accepted:
```bash
kubectl get enterpriseagentgatewaybackend accounts-mcp -n composable-mcp \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```
```
True
```

### Introspect the generated tool

Temporarily route to `accounts-mcp` so the generated tool can be inspected with `curl` (this route is deleted again immediately afterward — `account-brief`'s `mcp` step in a later part of this lab calls the backend directly by name and needs no route of its own):

```bash
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: accounts-mcp-probe
  namespace: composable-mcp
spec:
  parentRefs:
  - name: agentgateway-proxy
    namespace: agentgateway-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /accounts-mcp
    backendRefs:
    - name: accounts-mcp
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
EOF
kubectl port-forward -n agentgateway-system deploy/agentgateway-proxy 8080:8080 >/tmp/pf.log 2>&1 &
sleep 3
# initialize → capture session → tools/list
SID=$(curl -sD - "http://localhost:8080/accounts-mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
curl -s "http://localhost:8080/accounts-mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

You should see a similar `tools/list` response — one tool, `getAccount`, whose `inputSchema` nests the `id` path parameter under a **`path`** object (contrast with the Stripe lab's query parameters, which nest under a `query` object — the OpenAPI parameter's `in:` location determines the wrapper key):

```json
data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"getAccount","description":"Get an account profile by ID","inputSchema":{"required":["path"],"properties":{"path":{"required":["id"],"properties":{"id":{"type":"string"}},"type":"object"}},"type":"object"}}]}}
```

Calling it confirms the argument shape end to end — `arguments: {"path": {"id": "1"}}` returns the real `accounts-api` response, wrapped in an MCP tool result with both text and `structuredContent`:

```bash
curl -s "http://localhost:8080/accounts-mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"getAccount","arguments":{"path":{"id":"1"}}}}'
```
```json
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"id\":\"1\",\"name\":\"Globex\",\"tier\":\"gold\",\"region\":\"us-east\",\"owner\":\"alice\"}"}],"structuredContent":{"id":"1","name":"Globex","tier":"gold","region":"us-east","owner":"alice"},"isError":false}}
```

Delete the temporary probe route — the `accounts-mcp` backend and `accounts-openapi` ConfigMap stay in place for the composite tool built in the rest of this lab:

```bash
kubectl delete httproute accounts-mcp-probe -n composable-mcp
```

> **Takeaway:** `accounts-mcp` (target name `accounts`) now exposes `accounts-api` as the MCP tool `getAccount`, callable with `arguments: {"path": {"id": "<account-id>"}}`. The `account-brief` composite tool's `mcp` step will set `tool: getAccount` and an `arguments` CEL that produces this same `{"path":{"id":...}}` shape.

---

## Step 3 — Create the composite tool (baseline)

Everything so far has been plumbing: two REST mocks, then an MCP front door for one of them. This step introduces the feature the lab is actually about — `entMcp.targets[].custom`, a target whose "backend" is not one upstream but a small declarative pipeline of `steps` plus a CEL `output` expression that merges whatever those steps returned into a single tool result.

This baseline version deliberately keeps both steps as **HTTP** calls (`accounts-api` and `orders-api` directly, by Kubernetes `Service`), both driven off the same input (`input.account_id`), merged into a plain **string**. Later steps in this lab swap the `account` step for an `mcp` step calling `accounts-mcp`/`getAccount` (sequential orchestration), and swap the string `output` for `structuredContent`. Keeping those changes out of this step isolates what a `custom` target minimally needs to work at all.

### `custom` target fields

| Field | Purpose | In this tool |
|---|---|---|
| `description` | Tool description surfaced in `tools/list`, shown to the calling agent/LLM. | `"Consolidated brief for an account: profile plus open orders."` |
| `inputSchema` | JSON Schema for the arguments the *caller* passes to the composite tool — independent of whatever schemas the individual steps' backends expose. | One required string property, `account_id`. |
| `steps` | Ordered list of backend calls. Each step has a `name` (its result is addressable later as `output.<name>`) and exactly one of `http` or `mcp`. An `http` step's `backendRef` can point at a plain Kubernetes `Service` (as here), an `AgentgatewayBackend`, or an `EnterpriseAgentgatewayBackend`; `path`/`body`/`headers` are CEL expressions evaluated against a scope that includes `input` (the caller's arguments) and, for later steps, `output` (prior steps' results). | Two steps, `account` and `orders`, each a `GET` against a `Service`, with `path` built from `input.account_id`. |
| `output` | A CEL expression evaluated once all steps complete; its scope exposes `output.<step-name>` for every step. Its result type — string here — becomes the tool's `content[0].text`; a structured (object-typed) `output` instead populates `structuredContent` (a later step in this lab). | Concatenates `output.account.name`, `output.account.tier`, and `output.orders.open` into one sentence. |

A target's `name` (and each `steps[].name`) must be a DNS-1123 label: lowercase alphanumerics and hyphens, no underscores. That is why the composite tool is named `account-brief`. The caller-facing `inputSchema` property `account_id` is not subject to this rule — JSON Schema property names are arbitrary.

### Apply the baseline composite backend and route

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: composable-mcp
  namespace: composable-mcp
spec:
  entMcp:
    targets:
    - name: account-brief
      custom:
        description: "Consolidated brief for an account: profile plus open orders."
        inputSchema:
          type: object
          additionalProperties: false
          required:
          - account_id
          properties:
            account_id:
              type: string
              description: "Account ID, e.g. \"1\""
        steps:
        - name: account
          http:
            backendRef:
              group: ""
              kind: Service
              name: accounts-api
              port: 80
            method: GET
            path: '"/accounts/" + input.account_id'
        - name: orders
          http:
            backendRef:
              group: ""
              kind: Service
              name: orders-api
              port: 80
            method: GET
            path: '"/orders/" + input.account_id'
        output: |
          "Account " + output.account.name + " (tier " + output.account.tier + ") has "
          + string(output.orders.open) + " open orders."
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: composable-mcp
  namespace: composable-mcp
spec:
  parentRefs:
  - name: agentgateway-proxy
    namespace: agentgateway-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /composable
    backendRefs:
    - name: composable-mcp
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
EOF
```

Confirm both resources were accepted:
```bash
kubectl get enterpriseagentgatewaybackend composable-mcp -n composable-mcp \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
kubectl get httproute composable-mcp -n composable-mcp \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}{"\n"}'
```
```
True
True
```

### Call the composite tool end to end

```bash
kubectl port-forward -n agentgateway-system deploy/agentgateway-proxy 8080:8080 >/tmp/pf.log 2>&1 &
sleep 3
SID=$(curl -sD - "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"1"}}}'
```

You should see a similar `initialize` response:
```
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"rmcp","version":"1.5.0"}}}
```

You should see a similar `tools/list` response — a single composite tool, exposing only the caller-facing `inputSchema` (the two backend steps behind it are invisible to the caller):
```json
data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"account-brief","description":"Consolidated brief for an account: profile plus open orders.","inputSchema":{"additionalProperties":false,"properties":{"account_id":{"description":"Account ID, e.g. \"1\"","type":"string"}},"required":["account_id"],"type":"object"}}]}}
```

You should see a similar `tools/call` response for `{"account_id":"1"}` — one merged string, sourced from two HTTP calls the caller never sees:
```json
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"Account Globex (tier gold) has 3 open orders."}],"isError":false}}
```

`"Account Globex (tier gold) has 3 open orders."` matches expectations exactly: `output.account.name`/`output.account.tier` came from `accounts-api`'s `/accounts/1` response (`Globex`/`gold`), and `output.orders.open` came from `orders-api`'s `/orders/1` response (`3`) — one client call, two upstream calls, the gateway did the fan-out and the merge.

> **Takeaway:** a single declarative `custom` target with two `http` steps and a CEL `output` turns one client tool call into N upstream calls per request, merged into a single response the caller sees. The composite backend and route (`composable-mcp`/`composable-mcp` in ns `composable-mcp`) stay in place — later steps in this lab evolve this same tool (MCP step, structured output, JWT-derived headers) rather than replacing it.

---

## Step 4 — Fan out to a distinct MCP backend

The baseline `account-brief` fans out to two **HTTP** backends. That's aggregation, but the pattern this lab is really about is a composite tool that reaches an **MCP** backend and an **HTTP** backend in the same call. This step swaps the `account` step from `http` to `mcp`, pointed at the `accounts-mcp` front door built in Step 2, while leaving the `orders` step as plain HTTP against `orders-api`. Everything else — the target name, `inputSchema`, and the `output` CEL — is untouched.

### The diff

```diff
         steps:
         - name: account
-          http:
-            backendRef:
-              group: ""
-              kind: Service
-              name: accounts-api
-              port: 80
-            method: GET
-            path: '"/accounts/" + input.account_id'
+          mcp:
+            backendRef:
+              group: enterpriseagentgateway.solo.io
+              kind: EnterpriseAgentgatewayBackend
+              name: accounts-mcp
+              target: accounts
+            tool: getAccount
+            arguments: '{"path": {"id": input.account_id}}'
         - name: orders
           http:
             backendRef:
               group: ""
               kind: Service
               name: orders-api
               port: 80
             method: GET
             path: '"/orders/" + input.account_id'
         output: |
           "Account " + output.account.name + " (tier " + output.account.tier + ") has "
           + string(output.orders.open) + " open orders."
```

An `mcp` step's `backendRef` names the *backend* (`accounts-mcp`, the `EnterpriseAgentgatewayBackend` from Step 2) and, separately, the `target` within that backend (`accounts` — the `entMcp.targets[].name` Step 2 declared). That two-level addressing is because a single `EnterpriseAgentgatewayBackend` can expose several `entMcp.targets`; `target` picks the one this step calls, the same way an `http` step's `backendRef` picks a `Service`/port. `tool` is the MCP tool name to invoke on that target — `getAccount`, the `operationId` openapi-to-mcp generated from the OpenAPI schema in Step 2. `arguments` is a CEL expression, evaluated against the same `input` scope as an `http` step's `path`/`body`, whose *result* becomes the JSON `arguments` object sent in the `tools/call` request. Because Step 2's `tools/list` showed `getAccount`'s `inputSchema` nesting the path parameter under a `path` object (`{"path": {"id": ...}}` — the OpenAPI parameter's `in: path` location drives that wrapper key), the `arguments` CEL has to reproduce the same nesting: `'{"path": {"id": input.account_id}}'`, not the flatter `'{"id": input.account_id}'` an HTTP step's `path` might suggest. Getting this shape wrong is the single most likely failure mode when converting an `http` step to `mcp` — the call still succeeds, but the wrapped backend receives no usable `id` and errors or 404s internally.

### Apply and verify

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: composable-mcp
  namespace: composable-mcp
spec:
  entMcp:
    targets:
    - name: account-brief
      custom:
        description: "Consolidated brief for an account: profile plus open orders."
        inputSchema:
          type: object
          additionalProperties: false
          required:
          - account_id
          properties:
            account_id:
              type: string
              description: "Account ID, e.g. \"1\""
        steps:
        - name: account
          mcp:
            backendRef:
              group: enterpriseagentgateway.solo.io
              kind: EnterpriseAgentgatewayBackend
              name: accounts-mcp
              target: accounts
            tool: getAccount
            arguments: '{"path": {"id": input.account_id}}'
        - name: orders
          http:
            backendRef:
              group: ""
              kind: Service
              name: orders-api
              port: 80
            method: GET
            path: '"/orders/" + input.account_id'
        output: |
          "Account " + output.account.name + " (tier " + output.account.tier + ") has "
          + string(output.orders.open) + " open orders."
EOF
```

You should see output similar to the following:
```
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/composable-mcp configured
```

```bash
kubectl get enterpriseagentgatewaybackend composable-mcp -n composable-mcp \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```
```
True
```

The existing route from Step 3 (`composable-mcp`, `/composable`) is unchanged — it routes to the `composable-mcp` backend by name, not by step contents, so no route edit is needed. Re-run the same `tools/call` from Step 3, with the same port-forward already open on `8080`:

```bash
SID=$(curl -sD - "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"1"}}}'
```

You should see a similar response:
```json
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Account Globex (tier gold) has 3 open orders."}],"isError":false}}
```

`"Account Globex (tier gold) has 3 open orders."` — byte-for-byte the same string Step 3 produced, and **no change to the `output` CEL was needed**: `output.account.name`/`output.account.tier` resolve straight through even though `account`'s step type flipped from `http` to `mcp`. The composite engine flattens the `mcp` step's result to the same underlying JSON the `accounts-api` HTTP response carried (`{"id":"1","name":"Globex","tier":"gold","region":"us-east","owner":"alice"}`) before handing it to the `output` CEL as `output.account` — it does not leave the step result wrapped in the outer `tools/call` envelope (`content`/`structuredContent`) that Step 2's raw `curl` against `accounts-mcp` showed. A second call confirms this isn't a fluke of `account_id: "1"` specifically:

```bash
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"2"}}}'
```
```json
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"Account Initech (tier silver) has 1 open orders."}],"isError":false}}
```

`Initech`/`silver`/`1` matches `accounts-api`'s `/accounts/2` mock and `orders-api`'s `/orders/2` mock exactly, via the same unmodified `output` CEL.

> **Takeaway:** `account-brief` now aggregates **an MCP tool call and an HTTP call to two distinct backends** in a single client-facing tool — `account` resolves through `accounts-mcp`/`getAccount` (which itself wraps the `accounts-api` REST service via openapi-to-mcp), and `orders` still hits `orders-api` directly over HTTP. This is the key difference from [MCP Tool Federation](mcp-tool-federation.md): federation would expose `getAccount` and an `orders` tool as two *separate* tools for the client to call and stitch together itself; Composable MCP lets the `custom` target mix step *kinds* (`mcp`, `http`, and any future kind) transparently behind one tool name, so callers never need to know — or care — that one leg of the aggregation happens to be an MCP-wrapped backend and the other a raw REST call. The composite backend and route stay in place, unchanged in shape, for the sequential-orchestration and structured-output steps later in this lab.

---

## Step 5 — Orchestrate: chain step outputs

Both steps in `account-brief` so far take the same input — `input.account_id` — and run independently of each other; the gateway happens to fan them out together, but neither step depends on the other's result. That's fan-out, not orchestration.

A more realistic account brief shouldn't need the caller to know the account's region up front. `orders-api` exposes orders by region (`/orders/us-east`, `/orders/eu-west`) as well as by ID, and the account's region is exactly the kind of value the `account` step already fetched. Rather than have the caller pass a region alongside `account_id`, the `orders` step can look it up itself — by reading the `account` step's result.

### The diff

The `orders` step's `path` changes, and so does the final `output` CEL; the target's `description` also picks up a `(by region)` qualifier to match:

```diff
       custom:
-        description: "Consolidated brief for an account: profile plus open orders."
+        description: "Consolidated brief for an account: profile plus open orders (by region)."
         steps:
         - name: orders
           http:
             backendRef:
               group: ""
               kind: Service
               name: orders-api
               port: 80
             method: GET
-            path: '"/orders/" + input.account_id'
+            path: '"/orders/" + output.account.region'
         output: |
-          "Account " + output.account.name + " (tier " + output.account.tier + ") has "
-          + string(output.orders.open) + " open orders."
+          "Account " + output.account.name + " has " + string(output.orders.open)
+          + " open orders in region " + output.account.region + "."
```

`output.account.region` is available to the `orders` step's `path` CEL for the same reason `output.account.name`/`output.account.tier` are available to the final `output` CEL: **steps run in the order they're declared**, and every step after the first evaluates its CEL expressions against a scope that includes `output.<name>` for each step that already ran. `account` runs first and resolves through `accounts-mcp`/`getAccount`; by the time `orders` evaluates its `path`, `output.account` is the flattened JSON that call returned (`{"id":"1","name":"Globex","tier":"gold","region":"us-east","owner":"alice"}`), so `output.account.region` is simply `"us-east"`. The `orders` step no longer touches `input` at all — its only dependency is the prior step's output. This is the difference between the fan-out in Steps 3–4 (both steps read `input`, in parallel, and only the final `output` CEL combines them) and true sequential orchestration (a later step's *request* is built from an earlier step's *response*).

### Apply and verify

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: composable-mcp
  namespace: composable-mcp
spec:
  entMcp:
    targets:
    - name: account-brief
      custom:
        description: "Consolidated brief for an account: profile plus open orders (by region)."
        inputSchema:
          type: object
          additionalProperties: false
          required:
          - account_id
          properties:
            account_id:
              type: string
              description: "Account ID, e.g. \"1\""
        steps:
        - name: account
          mcp:
            backendRef:
              group: enterpriseagentgateway.solo.io
              kind: EnterpriseAgentgatewayBackend
              name: accounts-mcp
              target: accounts
            tool: getAccount
            arguments: '{"path": {"id": input.account_id}}'
        - name: orders
          http:
            backendRef:
              group: ""
              kind: Service
              name: orders-api
              port: 80
            method: GET
            path: '"/orders/" + output.account.region'
        output: |
          "Account " + output.account.name + " has " + string(output.orders.open)
          + " open orders in region " + output.account.region + "."
EOF
```

You should see output similar to the following:
```
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/composable-mcp configured
```

The route from Step 3 is unchanged — it routes to the `composable-mcp` backend by name, not by step contents. Re-run the same `tools/call` from Step 4, with the same port-forward already open on `8080`:

```bash
SID=$(curl -sD - "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"1"}}}'
```
```json
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Account Globex has 3 open orders in region us-east."}],"isError":false}}
```

```bash
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"2"}}}'
```
```json
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"Account Initech has 1 open orders in region eu-west."}],"isError":false}}
```

For `account_id: "1"`, `account` resolves to Globex in `us-east`, and the `orders` step's `path` becomes `/orders/us-east` — matching `orders-api`'s `us-east` mock (3 open orders) rather than its `/orders/1` mock. For `account_id: "2"`, the same chain resolves Initech's region, `eu-west`, and pulls that region's order count (1). The caller never passed a region at all; the composite tool derived it, mid-request, from the first step's result.

> **Takeaway:** a composite tool's `steps` aren't just parallel fan-out with a shared input — they run in declared order, and any step after the first can build its request from `output.<earlier-step-name>.<field>`, the same CEL scope the final `output` expression uses. That turns `account-brief` into a real pipeline: look up the account, then use what you learned to look up the right orders, and hand the caller back one answer. The composite backend and route stay in place, unchanged in shape, for the structured-output and JWT-derived-header steps later in this lab.

---

## Step 6 — Return structured data

A sentence like `"Account Globex has 3 open orders in region us-east."` is fine for a chat transcript, but it's a dead end for any caller that wants to *act* on the result — render a table, feed `open_orders` into a threshold check, list the individual order IDs. Those callers don't want prose to parse; they want fields. The only thing standing between `account-brief` and that use case is the type of the `output` CEL expression: swap the string for a JSON object, and the same steps produce a machine-readable result instead of a sentence.

### The diff

Only the target's `description` and its `output` CEL change — the `inputSchema` and both `steps` (the `mcp` step against `accounts-mcp`, the `http` step keyed off `output.account.region`) are exactly as Step 5 left them:

```diff
       custom:
-        description: "Consolidated brief for an account: profile plus open orders (by region)."
+        description: "Consolidated brief for an account, returned as structured data."
         inputSchema:
           ...
         steps:
           ...  # unchanged: account (mcp) and orders (http) steps from Step 5
         output: |
-          "Account " + output.account.name + " has " + string(output.orders.open)
-          + " open orders in region " + output.account.region + "."
+          {
+            "account": {"name": output.account.name, "tier": output.account.tier, "region": output.account.region},
+            "open_orders": output.orders.open,
+            "orders": output.orders.orders,
+            "summary": "Account " + output.account.name + " has " + string(output.orders.open) + " open orders in " + output.account.region + "."
+          }
```

The new `output` is a CEL map literal instead of a CEL string-concatenation expression. It still reads from the same `output.account` and `output.orders` step results Step 5 used — `region` and `tier` were already available, just never surfaced before — plus it forwards the raw `orders` array from the `orders` step, and keeps a human-readable `summary` field alongside the structured ones so nothing is lost for a caller that still just wants to display text.

### The output-shape contract

Agentgateway's Composable MCP evaluates the `output` CEL once all steps finish, and what happens next depends on the CEL's *result type*, not on anything declared elsewhere in the target:

- **String** result (Steps 3–5): becomes a single `content[0]` item of type `text`. That's it — one string, one text block, nothing else.
- **Object** (or list/number/bool) result (this step): serialized to JSON and placed in a `content[0]` `text` item *and* attached separately as `structuredContent` on the same `tools/call` result — the same field the raw `accounts-mcp`/`getAccount` call returned back in Step 2.

Both forms are valid MCP tool results; which one to use is a caller-experience decision. A model reading `content[0].text` gets a natural-language-shaped payload either way (a sentence, or now a JSON blob) — but a client written against `structuredContent` can skip parsing text entirely and read `open_orders`, `orders`, and `account.region` as typed fields.

### Apply and verify

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: composable-mcp
  namespace: composable-mcp
spec:
  entMcp:
    targets:
    - name: account-brief
      custom:
        description: "Consolidated brief for an account, returned as structured data."
        inputSchema:
          type: object
          additionalProperties: false
          required:
          - account_id
          properties:
            account_id:
              type: string
              description: "Account ID, e.g. \"1\""
        steps:
        - name: account
          mcp:
            backendRef:
              group: enterpriseagentgateway.solo.io
              kind: EnterpriseAgentgatewayBackend
              name: accounts-mcp
              target: accounts
            tool: getAccount
            arguments: '{"path": {"id": input.account_id}}'
        - name: orders
          http:
            backendRef:
              group: ""
              kind: Service
              name: orders-api
              port: 80
            method: GET
            path: '"/orders/" + output.account.region'
        output: |
          {
            "account": {"name": output.account.name, "tier": output.account.tier, "region": output.account.region},
            "open_orders": output.orders.open,
            "orders": output.orders.orders,
            "summary": "Account " + output.account.name + " has " + string(output.orders.open) + " open orders in " + output.account.region + "."
          }
EOF
```

You should see output similar to the following:
```
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/composable-mcp configured
```

```bash
kubectl get enterpriseagentgatewaybackend composable-mcp -n composable-mcp \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```
```
True
```

The route from Step 3 is unchanged — same backend name, same path prefix. Re-run the same `tools/call` from Step 5, with the same port-forward already open on `8080`:

```bash
SID=$(curl -sD - "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"1"}}}'
```

You should see a similar response:
```json
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\"open_orders\":3,\"summary\":\"Account Globex has 3 open orders in us-east.\",\"account\":{\"region\":\"us-east\",\"name\":\"Globex\",\"tier\":\"gold\"},\"orders\":[{\"id\":\"o-1\",\"amt\":120},{\"id\":\"o-2\",\"amt\":75},{\"id\":\"o-3\",\"amt\":200}]}"}],"structuredContent":{"open_orders":3,"summary":"Account Globex has 3 open orders in us-east.","account":{"region":"us-east","name":"Globex","tier":"gold"},"orders":[{"id":"o-1","amt":120},{"id":"o-2","amt":75},{"id":"o-3","amt":200}]},"isError":false}}
```

The same fields appear twice, exactly as the output-shape contract predicts: once as a JSON string inside `content[0].text`, and once — already parsed — as the `structuredContent` object. Both carry the identical `account` (name/tier/region), `open_orders`, `orders` array, and `summary`. Calling with `account_id: "2"` confirms the shape holds across accounts, not just this one:

```bash
curl -s "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"2"}}}'
```
```json
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"account\":{\"name\":\"Initech\",\"tier\":\"silver\",\"region\":\"eu-west\"},\"open_orders\":1,\"summary\":\"Account Initech has 1 open orders in eu-west.\",\"orders\":[{\"amt\":50,\"id\":\"o-9\"}]}"}],"structuredContent":{"account":{"name":"Initech","tier":"silver","region":"eu-west"},"open_orders":1,"summary":"Account Initech has 1 open orders in eu-west.","orders":[{"amt":50,"id":"o-9"}]},"isError":false}}
```

`tools/list` also confirms the caller-facing surface didn't change — the composite tool's `inputSchema` is identical to Step 5's, since this was purely an output-shape change on the backend side:

```json
data: {"jsonrpc":"2.0","id":3,"result":{"tools":[{"name":"account-brief","description":"Consolidated brief for an account, returned as structured data.","inputSchema":{"additionalProperties":false,"properties":{"account_id":{"description":"Account ID, e.g. \"1\"","type":"string"}},"required":["account_id"],"type":"object"}}]}}
```

> **Takeaway:** switching `output` from a string to an object is the entire change needed to move `account-brief` from a prose tool to a structured one — no changes to `steps`, `inputSchema`, or the route. A caller that only reads `content[0].text` still works (it now gets JSON text instead of a sentence), while a caller that reads `structuredContent` gets the same data as typed fields with no parsing step at all. That's the general shape of the tradeoff Composable MCP hands you: keep the output CEL a string while the caller is a human or a model reading a summary, switch it to an object the moment the caller is code that wants to act on individual fields — same `steps`, same backends, one expression.

---

## Step 7 (Optional) — Tie aggregation to caller identity

Everything so far treats `account-brief` as anonymous: any client that can reach `/composable` gets the same brief for the same `account_id`. A real deployment usually can't stop there — once a composite tool starts fanning out to backends on the caller's behalf, "who is actually asking" becomes something both the gateway and the downstream services need to know: to reject callers with no credential at all, and to give each backend call an auditable identity for logging, per-caller scoping, or rate limiting further downstream. This step adds both halves of that story to `account-brief`, using the same JWT mechanics as [In-Cluster MCP Deployment](in-cluster-mcp.md#secure-access-to-mcp-server): an `EnterpriseAgentgatewayPolicy` with `jwtAuthentication` enforces that every request to the route carries a valid token, and a CEL expression over the validated claims threads the caller's identity into the `orders` step as a header — no application code, no sidecar, just policy plus one line in the backend spec.

### Scope the policy to this route, not the whole gateway

`agentgateway-proxy` is the same shared `Gateway` every other lab in this workshop routes through. A `jwtAuthentication` policy targeting the `Gateway` would require a bearer token on *every* route behind it — including labs that haven't been touched since they were written. Targeting the `HTTPRoute` instead (`composable-mcp`, in the `composable-mcp` namespace) scopes enforcement to exactly the path this step cares about, `/composable`, and leaves everything else on the shared gateway untouched:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: composable-jwt
  namespace: composable-mcp
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: composable-mcp
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
      - issuer: solo.io
        jwks:
          inline: |
$(sed 's/^/            /' lib/jwt/jwks.json)
EOF
```

> The `$(sed ...)` substitution starts at column 0 in the heredoc, so `sed`'s 12-space prefix applies uniformly to every line of the block scalar. If your shell doesn't expand it, paste the contents of `lib/jwt/jwks.json` manually under `jwks.inline: |`, indented 12 spaces.

A policy's `targetRefs` resolve within the policy's own namespace, which is why this policy lives in `composable-mcp` (where the `HTTPRoute` lives) rather than `agentgateway-system` (where the `Gateway` lives). Confirm it attached:

```bash
kubectl get enterpriseagentgatewaypolicy composable-jwt -n composable-mcp \
  -o jsonpath='{.status.ancestors[0].conditions[?(@.type=="Attached")].status}{"\n"}'
```
```
True
```

### Thread the caller's identity into the `orders` step

With `mode: Strict` enforced, every request the gateway lets through has already been validated against the JWKS above — by the time a step's CEL runs, the caller's claims are available under a `jwt` variable, the same way `input` and `output` are. Adding a header to the `orders` step lets that identity ride along on the backend call itself, not just gate access to the route:

```diff
         - name: orders
           http:
             backendRef:
               group: ""
               kind: Service
               name: orders-api
               port: 80
             method: GET
             path: '"/orders/" + output.account.region'
+            headers:
+            - name: X-Caller
+              value: 'has(jwt.sub) ? jwt.sub : "anonymous"'
```

`jwt` exposes the validated token's claims as fields — `jwt.sub`, `jwt.iss`, or any custom claim a provider's tokens carry (`jwt.org`, `jwt.team`, and so on, as seen in the [In-Cluster MCP Deployment](in-cluster-mcp.md#authorize-based-on-jwt-claims) lab's RBAC examples). `has(jwt.sub)` guards against a token that validates but omits the claim, falling back to `"anonymous"` rather than sending an empty header. The same pattern works for API-key-authenticated routes via an `apiKey` variable exposing the matched key's metadata instead of JWT claims — whichever authentication mechanism a route uses, its identity becomes available to step CEL under a scope variable named after it.

Re-apply the composite backend with this one addition — `inputSchema`, the `account` step, and `output` are exactly as Step 6 left them:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: composable-mcp
  namespace: composable-mcp
spec:
  entMcp:
    targets:
    - name: account-brief
      custom:
        description: "Consolidated brief for an account, returned as structured data."
        inputSchema:
          type: object
          additionalProperties: false
          required:
          - account_id
          properties:
            account_id:
              type: string
              description: "Account ID, e.g. \"1\""
        steps:
        - name: account
          mcp:
            backendRef:
              group: enterpriseagentgateway.solo.io
              kind: EnterpriseAgentgatewayBackend
              name: accounts-mcp
              target: accounts
            tool: getAccount
            arguments: '{"path": {"id": input.account_id}}'
        - name: orders
          http:
            backendRef:
              group: ""
              kind: Service
              name: orders-api
              port: 80
            method: GET
            path: '"/orders/" + output.account.region'
            headers:
            - name: X-Caller
              value: 'has(jwt.sub) ? jwt.sub : "anonymous"'
        output: |
          {
            "account": {"name": output.account.name, "tier": output.account.tier, "region": output.account.region},
            "open_orders": output.orders.open,
            "orders": output.orders.orders,
            "summary": "Account " + output.account.name + " has " + string(output.orders.open) + " open orders in " + output.account.region + "."
          }
EOF
```

You should see output similar to the following:
```
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/composable-mcp configured
```

### Mint a token and assert both outcomes

`lib/jwt/generate-jwt.sh` signs arbitrary claims with the keypair behind `lib/jwt/jwks.json`, so the token below is minted on the fly rather than pulled from a `claims/` file — the only requirement is that `iss` matches the provider's `issuer: solo.io` above:

```bash
TOKEN=$(echo '{"iss":"solo.io","sub":"alice","exp":4070908800}' | ./lib/jwt/generate-jwt.sh -)
```

With the port-forward from earlier steps still open on `8080`, a request with no `Authorization` header is rejected before it ever reaches `account-brief`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "http://localhost:8080/composable" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
```

You should see output similar to the following:
```
401
```

The response body confirms why:
```
authentication failure: no bearer token found
```

The same request with the minted token attached succeeds, and the composite tool call behind it returns the same structured brief Step 6 produced:

```bash
SID=$(curl -sD - "http://localhost:8080/composable" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
  | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
curl -s "http://localhost:8080/composable" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"account-brief","arguments":{"account_id":"1"}}}'
```

You should see a similar response — `200 OK` with an `mcp-session-id` on the `initialize` response, then the same merged brief `account-brief` has returned since Step 6:
```json
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\"open_orders\":3,\"summary\":\"Account Globex has 3 open orders in us-east.\",\"account\":{\"name\":\"Globex\",\"region\":\"us-east\",\"tier\":\"gold\"},\"orders\":[{\"amt\":120,\"id\":\"o-1\"},{\"id\":\"o-2\",\"amt\":75},{\"id\":\"o-3\",\"amt\":200}]}"}],"structuredContent":{"open_orders":3,"summary":"Account Globex has 3 open orders in us-east.","account":{"name":"Globex","region":"us-east","tier":"gold"},"orders":[{"amt":120,"id":"o-1"},{"id":"o-2","amt":75},{"id":"o-3","amt":200}]},"isError":false}}
```

> **Takeaway:** a JWT policy and a CEL header together turn `account-brief` from "anyone can ask for anyone's account brief" into "only authenticated callers can ask, and every backend call carries who asked." Scoping the policy to the `HTTPRoute` rather than the shared `Gateway` keeps that enforcement local to `/composable`, and the `jwt` CEL variable makes the caller's validated claims available to *any* step's CEL — not just a gate at the edge — the same variable an `apiKey`-authenticated route would expose as `apiKey` instead. Composable MCP's aggregation story and a workshop's identity story turn out to be the same mechanism: CEL expressions with an ever-growing scope (`input`, `output.<step>`, and now `jwt`/`apiKey`) that steps and the final `output` can all read from.

---

## Observe it (optional, requires `002`)

From the caller's side, `account-brief` is one `tools/call`. Behind the gateway it's two upstream requests — the `account` step into `accounts-mcp`/`getAccount`, the `orders` step into `orders-api` — and both observability surfaces from `002` show that fan-out, just at different granularities.

The access log records one line per client-facing request, tagged with the composite target's name rather than the individual steps behind it:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

```
... protocol=mcp mcp.method.name=tools/call mcp.target=account-brief mcp.resource.type=tool gen_ai.tool.name=account-brief mcp.session.id=... duration=2ms
```

`protocol=mcp` and `mcp.target=account-brief` identify this as the composite tool call, but the log line doesn't break out the `account`/`orders` steps individually — same as the meta-tool logging in [MCP Tool Mode — Search](mcp-tool-mode-search.md), where the structured log shows the tool the client called, and "the gateway's call to the upstream is a separate trace span." Traces are where the per-step fan-out becomes visible: if you installed Tempo in `002`, the `trace.id` on the log line above has one parent span for the `tools/call` against `account-brief` and a child span per step — one for the MCP call into `accounts-mcp`, one for the HTTP call into `orders-api`.

1. Port-forward Grafana: `kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000`
2. Open http://localhost:3000 (username: `admin`, password: `prom-operator`)
3. **Home > Explore**, select **Tempo**, and search for a trace on route `composable-mcp/composable-mcp`
4. Expand the trace — the two child spans are the two upstream calls this one `tools/call` fanned out to

---

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy composable-jwt -n composable-mcp --ignore-not-found
kubectl delete httproute composable-mcp -n composable-mcp --ignore-not-found
kubectl delete enterpriseagentgatewaybackend composable-mcp accounts-mcp -n composable-mcp --ignore-not-found
kubectl delete configmap accounts-openapi accounts-nginx orders-nginx -n composable-mcp --ignore-not-found
kubectl delete deployment accounts-api orders-api -n composable-mcp --ignore-not-found
kubectl delete service accounts-api orders-api -n composable-mcp --ignore-not-found
kubectl delete namespace composable-mcp --ignore-not-found
```

Unlike [MCP Tool Federation](mcp-tool-federation.md), this lab's `composable-mcp` namespace is dedicated to it — the final `kubectl delete namespace` removes anything the steps above missed.

---

## Next steps

- [MCP Tool Federation](mcp-tool-federation.md) — the other half of the aggregation-vs-federation story from this lab's intro: federation routes each call to its owning backend (N tools, the client orchestrates); Composable MCP fans one call out and merges the responses (1 tool, the gateway orchestrates).
- [OpenAPI to MCP — In-Cluster Deployment](openapi-to-mcp-in-cluster.md) — the pattern behind the `accounts-mcp` front door built in Step 2 of this lab, on its own and without a composite target on top of it.
- The `output` and step CEL expressions in this lab cover string concatenation, object construction, and reading prior-step results — but CEL has no loops and no calls to other services. For orchestration logic that outgrows CEL, Enterprise Agentgateway also supports a full [ExtProc filter](https://docs.solo.io/agentgateway/latest/traffic-management/extproc/) for arbitrary request/response processing in an external gRPC service.
