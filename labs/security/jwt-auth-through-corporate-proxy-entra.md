# Configure JWT Auth Through a Corporate Proxy (Entra)

Many enterprises don't allow direct egress to the internet; all outbound traffic, including calls to an external identity provider, has to go through a corporate forward proxy. This lab deploys a Squid proxy to stand in for that corporate boundary, then configures agentgateway to fetch a remote JWKS from Microsoft Entra ID through that proxy using `BackendTunnel`, agentgateway's `HTTPS_PROXY`-style tunneling behavior.

This lab is focused purely on the tunnel mechanics: it configures JWT authentication only, with no authorization/RBAC rules. For an Entra-backed lab with token exchange, see [Microsoft Entra On-Behalf-Of Token Exchange](../identity-delegation/msft-entra-obo.md), whose Entra JWKS setup this lab reuses.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- An Entra ID tenant, and the `az` CLI installed and authenticated (`az login`) so you can mint a test token.
- Your Entra tenant ID:

```bash
export ENTRA_TENANT_ID="$ENTRA_TENANT_ID"                             # e.g. "11111111-2222-3333-4444-555555555555"
```

## Lab Objectives
- Deploy a Squid forward proxy to simulate a corporate network boundary
- Create an `EnterpriseAgentgatewayBackend` for the proxy, and a second `EnterpriseAgentgatewayBackend` for Entra's JWKS endpoint that tunnels through it via `policies.tunnel`
- Configure JWT authentication (no authorization) against the tunneled JWKS endpoint
- Validate that the JWKS fetch and JWT validation succeed even though Entra is only reachable through the proxy
- Confirm from the proxy's own access log that traffic actually transited the tunnel
- Reuse the same tunneled JWKS to protect an MCP backend instead of an LLM backend (see [MCP Backend Variant](#mcp-backend-variant))

## Overview

`BackendTunnel` lets an `EnterpriseAgentgatewayBackend` reach its destination by issuing an HTTP `CONNECT` to an intermediary proxy first, then tunneling the real (typically TLS) connection through it, the same behavior `HTTPS_PROXY` gives you in a standard HTTP client. The backend being tunneled just needs a `policies.tunnel.backendRef` pointing at another `EnterpriseAgentgatewayBackend` that represents the proxy:

```
 client              agentgateway            corporate-proxy (squid)              Entra
   |                      |                           |                              |
   |-- POST /openai ----->|                           |                              |
   |                      |-- CONNECT login.microsoftonline.com:443 --------------->|
   |                      |<================ TLS tunnel established ===============>|
   |                      |-- GET /<tenant>/discovery/v2.0/keys (JWKS, over tunnel) ->|
   |                      |<-- JWKS response (relayed by squid) --------------------|
   |<-- 200 OK ------------|                           |                              |
```

The gateway never talks to Entra directly: every byte of that JWKS fetch (and the eventual TLS handshake) passes through the Squid pod.

## Deploy the Corporate Proxy (Squid)

Deploy Squid in its own namespace to keep the "corporate network" boundary clear of the gateway's own namespace:

```bash
kubectl apply -f- <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: corporate-proxy
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: corporate-proxy-config
  namespace: corporate-proxy
data:
  squid.conf: |
    http_port 3128

    acl SSL_ports port 443
    acl SSL_ports port 8443

    http_access allow CONNECT SSL_ports
    http_access deny CONNECT !SSL_ports
    http_access allow all

    dns_v4_first on
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: corporate-proxy
  namespace: corporate-proxy
  labels:
    app: corporate-proxy
spec:
  replicas: 1
  selector:
    matchLabels:
      app: corporate-proxy
  template:
    metadata:
      labels:
        app: corporate-proxy
    spec:
      containers:
        - name: squid
          image: ubuntu/squid:6.6-24.04_beta
          ports:
            - containerPort: 3128
              name: proxy
              protocol: TCP
          volumeMounts:
            - name: squid-config
              mountPath: /etc/squid/squid.conf
              subPath: squid.conf
      volumes:
        - name: squid-config
          configMap:
            name: corporate-proxy-config
---
apiVersion: v1
kind: Service
metadata:
  name: corporate-proxy
  namespace: corporate-proxy
spec:
  type: ClusterIP
  ports:
    - port: 3128
      targetPort: 3128
      protocol: TCP
      name: proxy
  selector:
    app: corporate-proxy
EOF
```

> **Note: squid.conf, not conf.d.** Squid's `CONNECT` handling is mounted as a full `squid.conf` rather than a `conf.d/*.conf` snippet, so behavior doesn't depend on whatever default config ships in the base image. `SSL_ports` includes both `443` and `8443` so the same proxy can front any HTTPS-based IdP.

Confirm the pod is running before moving on:

```bash
kubectl get pods -n corporate-proxy
```

## Configure the Tunnel to Entra's JWKS Endpoint

First, create an `EnterpriseAgentgatewayBackend` that represents the Squid proxy itself:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: corporate-proxy
  namespace: agentgateway-system
spec:
  static:
    host: corporate-proxy.corporate-proxy.svc.cluster.local
    port: 3128
EOF
```

Now create the Entra JWKS backend, and set `policies.tunnel.backendRef` to route through it:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: entra-jwks
  namespace: agentgateway-system
spec:
  static:
    host: login.microsoftonline.com
    port: 443
  policies:
    tls: {}
    tunnel:
      backendRef:
        group: enterpriseagentgateway.solo.io
        kind: EnterpriseAgentgatewayBackend
        name: corporate-proxy
        port: 3128
EOF
```

> **Why `tls: {}` alongside `tunnel`?** `tunnel` only handles the `CONNECT` to the proxy. It's the `tls` field that tells agentgateway to originate a real TLS connection to Entra through that tunnel, verifying Entra's certificate against system trusted CAs the same way it would for a direct connection.

## Configure Basic Routing

Create the OpenAI secret, backend, and route that JWT auth will protect:

```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

```bash
kubectl apply -f- <<EOF
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
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

## Configure JWT Auth

Create a policy that validates JWTs against the Entra issuer using the tunneled JWKS backend. This lab intentionally skips the `authorization` block: any request with a valid token from this issuer is authenticated, no claims-based RBAC is enforced.

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: agentgateway-jwt-auth-tunnel
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: https://sts.windows.net/${ENTRA_TENANT_ID}/
          jwks:
            remote:
              backendRef:
                name: entra-jwks
                namespace: agentgateway-system
                kind: EnterpriseAgentgatewayBackend
                group: enterpriseagentgateway.solo.io
              jwksPath: /${ENTRA_TENANT_ID}/discovery/v2.0/keys
EOF
```

> **Why `sts.windows.net` and not `login.microsoftonline.com` in the issuer?** Entra's v1 access tokens carry `iss: https://sts.windows.net/<tenant>/`, not the `login.microsoftonline.com` host you fetch the JWKS from. The JWKS backend targets the host that actually serves the keys; the policy's `issuer` must match the `iss` claim in the token being validated.

> **Why target the `openai` `HTTPRoute` and not the `Gateway`?** `jwtAuthentication` validates and strips the JWT before it reaches any backend. If this policy targeted the `Gateway`, it would apply to *every* route on it — including an MCP backend that carries its own `mcp.authentication` (see [MCP Backend Variant](#mcp-backend-variant) below), whose auth plugin would then reject the request because the token was already stripped upstream. Scoping to the specific `HTTPRoute` keeps gateway-level and MCP-level JWT auth from colliding when both are in play on the same gateway.

## Test

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### curl with no token

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

Expected output: the request fails with `authentication failure: no bearer token found`. This happens even before any JWKS fetch: agentgateway only needs to resolve the tunneled JWKS the first time it has to validate a token's signature.

### Get a token from Entra

```bash
az login

export VALID_TOKEN=$(az account get-access-token --query accessToken -o tsv)

echo "Token (first 40 chars): ${VALID_TOKEN:0:40}..."
```

### curl with a valid Entra token

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $VALID_TOKEN" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

Expected output: `HTTP 200` with a completion from the OpenAI backend. This is the first request that forces agentgateway to resolve the Entra JWKS, and it does so entirely through the Squid tunnel.

## Verify the Tunnel Was Used

Confirm from Squid's own access log that it actually brokered the connection to Entra, rather than agentgateway reaching Entra directly:

```bash
kubectl exec -n corporate-proxy deploy/corporate-proxy -- tail -n 20 /var/log/squid/access.log
```

Expected output (truncated): a `CONNECT` entry for `login.microsoftonline.com:443`, e.g.

```
1770000000.123    45 10.244.0.12 TCP_TUNNEL/200 5678 CONNECT login.microsoftonline.com:443 - HIER_DIRECT/20.190.x.x -
```

`TCP_TUNNEL/200` confirms Squid successfully established the tunnel; if agentgateway had bypassed the proxy, this log would be empty.

### View Access Logs

Cross-reference with agentgateway's own logs to see the JWT validation succeed on the client-facing side:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

For metrics and traces on this traffic, see `002`.

## MCP Backend Variant

The tunnel mechanics above apply just as well when the thing behind JWT auth is an MCP server rather than an LLM backend. Instead of validating the JWT at the `HTTPRoute` level like `agentgateway-jwt-auth-tunnel` does above, an MCP backend can carry its own `policies.mcp.authentication` block directly on its `EnterpriseAgentgatewayBackend` — same tunneled `entra-jwks` backend, same Entra issuer, just wired into the MCP auth plugin instead of the gateway auth plugin.

This wraps [Solo.io's docs MCP server](https://search.solo.io) — the same remote server used in [Connect to a Remote MCP Server](../mcp/remote-mcp.md), here protected by JWT auth validated against the tunneled Entra JWKS:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: soloio-docs-mcp-backend
  namespace: agentgateway-system
spec:
  policies:
    mcp:
      authentication:
        mode: Strict
        issuer: https://sts.windows.net/${ENTRA_TENANT_ID}/
        jwks:
          backendRef:
            group: enterpriseagentgateway.solo.io
            kind: EnterpriseAgentgatewayBackend
            name: entra-jwks
            namespace: agentgateway-system
          cacheDuration: 5m
          jwksPath: ${ENTRA_TENANT_ID}/discovery/v2.0/keys
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
            value: /soloio-docs-mcp
      backendRefs:
        - name: soloio-docs-mcp-backend
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
EOF
```

> **No leading slash on `jwksPath` here either.** Same rule as the tunnel mechanics above: the controller joins the JWKS backend's URL and `jwksPath` with a `/`, so a leading slash produces a double slash and a 404 from Entra.

### Test

```bash
curl -i "$GATEWAY_IP:8080/soloio-docs-mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl-test","version":"1.0"}}}'
```

Expected output: `401 Unauthorized` with a `WWW-Authenticate: Bearer resource_metadata="..."` header — MCP auth failures return an OAuth protected-resource discovery pointer, unlike the plain-text `authentication failure: no bearer token found` from the gateway-level auth above.

```bash
export SESSION_ID=$(curl -s -D - "$GATEWAY_IP:8080/soloio-docs-mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $VALID_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl-test","version":"1.0"}}}' \
  -o /dev/null | grep -i mcp-session-id | awk -F': ' '{print $2}' | tr -d '\r')

curl -s "$GATEWAY_IP:8080/soloio-docs-mcp" \
  -H "content-type: application/json" \
  -H "accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $VALID_TOKEN" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Expected output: `200 OK` with the MCP server's tool list (`search`, `get_chunks`, `get_full_page`) — the JWT was validated against Entra's JWKS, fetched entirely through the Squid tunnel, exactly like the LLM backend above.

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system agentgateway-jwt-auth-tunnel --ignore-not-found
kubectl delete httproute -n agentgateway-system openai --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret --ignore-not-found
kubectl delete httproute -n agentgateway-system soloio-docs-mcp --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system soloio-docs-mcp-backend --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system entra-jwks --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system corporate-proxy --ignore-not-found
kubectl delete namespace corporate-proxy --ignore-not-found
```
