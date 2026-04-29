# OPA Authorization with Enterprise Agentgateway

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

You will also need:
- An OpenAI API key stored as an environment variable (`$OPENAI_API_KEY`)
- Access to the Solo.io docs MCP server (`https://search.solo.io/mcp`) for Part 2

## Lab Objectives
- Understand when to use OPA vs CEL-based RBAC for authorization
- Create an OPA Rego policy as a ConfigMap
- Create an `AuthConfig` referencing the OPA policy
- Attach the policy to an LLM HTTPRoute via `EnterpriseAgentgatewayPolicy`
- Validate that requests without the required header are denied with 403 and a custom response body
- Validate that requests with the required header are allowed with custom upstream header injection
- Reuse the same `AuthConfig` to protect an MCP route

## Overview

```
                                    AgentGateway
                              +----------------------+
                              |                      |
                              |   +--------------+   |
              +-------+       |   |  OPA Engine  |   |       +----------+
              |       | POST  |   |  (ext-auth)  |   |       |          |
              | Client+------>|   |              |   +------>| OpenAI   |
              |       |       |   |  Rego Policy |   |       | / MCP    |
              +-------+       |   |  (ConfigMap) |   |       | Backend  |
                              |   +------+-------+   |       +----------+
                              |          |           |
                              |    allow / deny      |
                              |    + custom headers  |
                              |    + custom body     |
                              +----------------------+

  +---------------------------------------------------------------------+
  |  ConfigMap          AuthConfig            EnterpriseAgentgateway-    |
  |  (policy.rego)  --> (opaAuth module)  --> Policy (targetRefs)        |
  |                                           v                         |
  |                                      HTTPRoute                      |
  |                                      (/openai, /mcp)                |
  +---------------------------------------------------------------------+
```

Solo Enterprise for AgentGateway includes an embedded OPA engine in the ext-auth service. You write a Rego policy as a ConfigMap, reference it from an `AuthConfig`, and attach it to any HTTPRoute via an `EnterpriseAgentgatewayPolicy`. No standalone OPA deployment required.

### When to use CEL vs OPA

For most authorization scenarios, **CEL-based RBAC rules** built directly into `EnterpriseAgentgatewayPolicy` are the recommended approach. CEL rules evaluate inside the proxy with no external call, and cover common cases like header matching, JWT claim checks, and MCP tool-level access control:

```yaml
# CEL example -- allow only requests with a specific header
spec:
  traffic:
    authorization:
      action: Allow
      policy:
        matchExpressions:
          - "request.headers['x-team'] == 'ml-platform'"
```

**Use OPA when you need:**
- Complex multi-condition logic that's hard to express in a single CEL expression
- Custom deny response bodies and headers (CEL RBAC returns a fixed 403)
- Dynamic metadata injection for downstream filters (e.g. rate limiting tiers)
- Header manipulation on allow (injecting upstream headers, stripping credentials)
- Policy-as-code workflows where Rego policies are managed in version control or OPA bundles

This lab demonstrates the OPA approach for these advanced use cases.

---

## Part 1: LLM Route with OPA

### Step 1: Create the OpenAI backend and route

Create the OpenAI api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create the OpenAI backend and HTTPRoute
```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: gpt-4o-mini
  policies:
    auth:
      secretRef:
        name: openai-secret
---
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
    - name: openai
      namespace: agentgateway-system
      group: agentgateway.dev
      kind: AgentgatewayBackend
EOF
```

Verify the route works before adding OPA
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Say hi"
      }
    ],
    "max_tokens": 5
  }'
```

You should get a 200 response with a completion from OpenAI.

### Step 2: Create the OPA policy

The Rego policy is stored in a ConfigMap. It checks for an `api-key` header and returns structured decisions that control the HTTP response:

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: llm-opa-policy
  namespace: agentgateway-system
data:
  policy.rego: |-
    package llm_authz
    import future.keywords.if

    # Default: deny with 403 + custom body + response header
    default rule := {
      "allow": false,
      "response_headers_to_add": {"x-opa-decision": "denied"},
      "request_headers_to_remove": ["api-key"],
      "body": "Access denied: missing or invalid API key",
      "http_status": 403,
    }

    # Allow when api-key matches; inject upstream header, strip api-key
    rule := result if {
      input.http_request.headers["api-key"] == "authorized-user-key"
      result := {
        "allow": true,
        "http_status": 200,
        "headers": {"x-validated-by": "opa-security-checkpoint"},
        "response_headers_to_add": {"x-opa-decision": "allowed"},
        "request_headers_to_remove": ["api-key"]
      }
    }
EOF
```

**Key fields in the OPA result object:**

| Field | Description |
|---|---|
| `allow` | `true` to forward the request, `false` to reject |
| `http_status` | Status code returned to the client on deny |
| `body` | Response body returned to the client on deny |
| `headers` | Headers added to the **upstream** request on allow |
| `response_headers_to_add` | Headers added to the **client** response |
| `request_headers_to_remove` | Headers stripped before forwarding upstream |

### Step 3: Create the AuthConfig

The `AuthConfig` references the ConfigMap module and specifies the Rego query path:

```bash
kubectl apply -f - <<EOF
apiVersion: extauth.solo.io/v1
kind: AuthConfig
metadata:
  name: llm-opa
  namespace: agentgateway-system
spec:
  configs:
    - opaAuth:
        modules:
        - name: llm-opa-policy
          namespace: agentgateway-system
        query: "data.llm_authz.rule"
EOF
```

Verify it is accepted:

```bash
kubectl get authconfig llm-opa -n agentgateway-system -o jsonpath='{.status.state}'
# Expected: Accepted
```

### Step 4: Attach the policy to the OpenAI route

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: llm-opa-policy
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  traffic:
    entExtAuth:
      authConfigRef:
        name: llm-opa
        namespace: agentgateway-system
EOF
```

No `backendRef` is needed -- it defaults to the provisioned ext-auth service.

### Step 5: Test LLM authorization

**Denied -- no api-key:**

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Say hi"
      }
    ],
    "max_tokens": 5
  }'
```

Expected:
```
HTTP/1.1 403 Forbidden
x-opa-decision: denied

Access denied: missing or invalid API key
```

**Allowed -- valid api-key:**

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "api-key: authorized-user-key" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Say hi"
      }
    ],
    "max_tokens": 5
  }'
```

Expected:
```
HTTP/1.1 200 OK
x-opa-decision: allowed

{"choices":[{"message":{"content":"Hello!",...}}],...}
```

The `api-key` header is stripped before it reaches OpenAI, and `x-validated-by: opa-security-checkpoint` is injected into the upstream request.

---

## Part 2: MCP Route with OPA

The same OPA policy can protect MCP tool servers. This demonstrates that one `AuthConfig` can be shared across multiple routes. We'll route to the external Solo.io docs MCP server (`https://search.solo.io/mcp`) -- no in-cluster MCP deployment required.

### Step 1: Create the MCP backend and route

Since the Solo.io docs MCP server is external, we configure a static backend with TLS and use the StreamableHTTP protocol:

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: soloio-docs-mcp
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
    namespace: agentgateway-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /mcp
    backendRefs:
    - name: soloio-docs-mcp
      namespace: agentgateway-system
      group: agentgateway.dev
      kind: AgentgatewayBackend
EOF
```

### Step 2: Attach the OPA policy to the MCP route

Reuse the same `AuthConfig` -- just create a new policy targeting the MCP HTTPRoute:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: mcp-opa-policy
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: soloio-docs-mcp
  traffic:
    entExtAuth:
      authConfigRef:
        name: llm-opa
        namespace: agentgateway-system
EOF
```

### Step 3: Test MCP authorization

**Denied -- no api-key:**

```bash
curl -i "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "test", "version": "1.0"}
    },
    "id": 1
  }'
```

Expected:
```
HTTP/1.1 403 Forbidden
x-opa-decision: denied

Access denied: missing or invalid API key
```

**Allowed -- valid api-key (initialize + list tools):**

```bash
# Initialize
curl -s -D /tmp/mcp-headers.txt "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "api-key: authorized-user-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "test", "version": "1.0"}
    },
    "id": 1
  }'

# Grab session ID
SESSION=$(grep -i "mcp-session-id" /tmp/mcp-headers.txt | awk '{print $2}' | tr -d '\r')

# Send initialized notification
curl -s "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -H "api-key: authorized-user-key" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

sleep 2

# List tools
curl -s "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -H "api-key: authorized-user-key" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":2}'
```

Expected:
```json
{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"search","description":"Search Solo.io product documentation",...},{"name":"get_chunks",...}]}}
```

**Call the search tool:**

```bash
curl -s "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -H "api-key: authorized-user-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {"query": "OPA authorization", "product": "solo-enterprise-for-agentgateway", "limit": 2}
    },
    "id": 3
  }'
```

---

## View Access Logs

Check the agentgateway proxy logs to see the request flow:
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

---

## How it works

```
Client                AgentGateway Proxy              Ext-Auth (OPA)            Backend
  |                         |                              |                      |
  |--- POST /openai ------->|                              |                      |
  |  (no api-key)           |--- gRPC Check() ------------>|                      |
  |                         |                              |-- evaluate Rego      |
  |                         |<-- deny(403, body, hdrs) ----|                      |
  |<-- 403 Forbidden -------|                              |                      |
  |    x-opa-decision:denied|                              |                      |
  |                         |                              |                      |
  |--- POST /openai ------->|                              |                      |
  |  api-key: auth-user-key |--- gRPC Check() ------------>|                      |
  |                         |                              |-- evaluate Rego      |
  |                         |<-- allow(hdrs to add/rm) ----|                      |
  |                         |--- POST (x-validated-by) --->|--- OpenAI API ------>|
  |                         |      (api-key stripped)      |                      |
  |<-- 200 OK --------------|<-----------------------------|<------ response -----|
  |    x-opa-decision:allowed                              |                      |
```

## Key resources

| Resource | Purpose |
|---|---|
| `ConfigMap` (policy.rego) | OPA Rego policy code |
| `AuthConfig` (extauth.solo.io/v1) | References ConfigMap modules, sets query path |
| `EnterpriseAgentgatewayPolicy` | Attaches AuthConfig to an HTTPRoute via `spec.traffic.entExtAuth` |

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system llm-opa-policy mcp-opa-policy
kubectl delete authconfig -n agentgateway-system llm-opa
kubectl delete configmap -n agentgateway-system llm-opa-policy
kubectl delete httproute -n agentgateway-system openai soloio-docs-mcp
kubectl delete agentgatewaybackend -n agentgateway-system openai soloio-docs-mcp
kubectl delete secret -n agentgateway-system openai-secret
```
