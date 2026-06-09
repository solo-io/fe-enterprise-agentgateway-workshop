# BYO OPA gRPC External Authorization (ext-authz)

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

You will also need:
- An OpenAI API key stored as an environment variable (`$OPENAI_API_KEY`) for Part 1
- Reachability to the Solo.io docs MCP server (`https://search.solo.io/mcp`) for Part 2

## Lab Objectives
- Deploy the open-source OPA server as a standalone gRPC ext-authz service
- Author a Rego policy that conforms to the Envoy ext-authz input schema
- Create an `EnterpriseAgentgatewayPolicy` that points the gateway at OPA via gRPC
- Protect an LLM (OpenAI) route with the OPA policy
- Reuse the same OPA service to protect an MCP route
- (Optional) Swap the in-cluster ConfigMap-backed policy for an OPA bundle

## About BYO OPA ext-authz

OPA ships an `envoy_ext_authz_grpc` plugin that lets the `openpolicyagent/opa` server speak the standard [Envoy External Authorization gRPC proto](https://github.com/envoyproxy/envoy/blob/main/api/envoy/service/auth/v3/external_auth.proto) directly — no custom code required. Enterprise Agentgateway calls OPA on every request, OPA evaluates Rego, and returns allow/deny.

```
Client → Agentgateway → gRPC (Envoy ext-authz) → OPA pod → allow/deny
                                                   ↓
                                       Rego at envoy/authz/allow
                                       (ConfigMap or remote bundle)
```

### Embedded OPA vs BYO OPA

Enterprise Agentgateway offers two ways to enforce OPA policy. Pick based on how you want to manage and distribute policy:

| | Embedded OPA (`AuthConfig.opaAuth`) | BYO OPA (this lab) |
|---|---|---|
| **Where OPA runs** | Inside the provisioned ext-auth service | Standalone `openpolicyagent/opa` pod |
| **Policy source** | `ConfigMap` referenced by `AuthConfig` | ConfigMap mount, OPA bundle (HTTP/OCI), or git-sync |
| **Decision logs** | Gateway access logs | OPA `decision_logs` (console, HTTP sink, S3, etc.) |
| **Operational model** | Managed by gateway controller | Standard OPA deployment patterns |
| **Use when** | Simplest path, single-team ownership | You already run OPA, need bundles, or want OPA's full observability stack |

See [opa-authorization.md](opa-authorization.md) for the embedded-OPA approach. The rest of this lab covers the BYO path.

## Deploy OPA as a gRPC ext-authz service

OPA is configured with three pieces:
1. A **Rego policy** that the gateway will query
2. An **OPA config** that turns on the `envoy_ext_authz_grpc` plugin
3. A **Deployment + Service** wired for gRPC (HTTP/2 cleartext)

### Step 1: Create the policy and OPA config

Put both files in one ConfigMap. The Rego policy lives at `package envoy.authz` and is queried at the Rego path `envoy/authz/allow`. With the Envoy plugin, OPA receives the full Envoy `CheckRequest` as `input` — `input.attributes.request.http.headers` holds the request headers.

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: opa-ext-authz-config
  namespace: agentgateway-system
data:
  config.yaml: |
    plugins:
      envoy_ext_authz_grpc:
        addr: :9191
        path: envoy/authz/allow
        dry-run: false
        enable-reflection: false
    decision_logs:
      console: true
  policy.rego: |
    package envoy.authz
    import rego.v1

    # Default: deny
    default allow := false

    # Allow when the x-ext-authz: allow header is present
    allow if {
      input.attributes.request.http.headers["x-ext-authz"] == "allow"
    }
EOF
```

**Key fields in the OPA config:**

| Field | Purpose |
|---|---|
| `plugins.envoy_ext_authz_grpc.addr` | TCP port OPA binds the gRPC ext-authz server on |
| `plugins.envoy_ext_authz_grpc.path` | Rego path the gateway's `Check()` call is evaluated against |
| `decision_logs.console` | Streams every authz decision to OPA's stdout — visible via `kubectl logs` |

### Step 2: Deploy OPA

Use the `openpolicyagent/opa:latest-envoy-static` image — it bundles the Envoy ext-authz plugin and ships multi-arch (amd64 + arm64). The plain `:latest-envoy` tag is amd64-only and will fail to pull on arm64 nodes. The pod runs `opa run --server` pointed at the mounted config and policy.

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  namespace: agentgateway-system
  name: opa-ext-authz
  labels:
    app: opa-ext-authz
spec:
  replicas: 1
  selector:
    matchLabels:
      app: opa-ext-authz
  template:
    metadata:
      labels:
        app: opa-ext-authz
        app.kubernetes.io/name: opa-ext-authz
    spec:
      containers:
      - name: opa
        image: openpolicyagent/opa:latest-envoy-static
        args:
        - "run"
        - "--server"
        - "--addr=:8181"
        - "--config-file=/config/config.yaml"
        - "/policy/policy.rego"
        ports:
        - name: grpc-authz
          containerPort: 9191
        - name: http-api
          containerPort: 8181
        volumeMounts:
        - name: opa-config
          mountPath: /config
        - name: opa-policy
          mountPath: /policy
        readinessProbe:
          httpGet:
            path: /health?plugins
            port: 8181
          initialDelaySeconds: 2
          periodSeconds: 5
      volumes:
      - name: opa-config
        configMap:
          name: opa-ext-authz-config
          items:
          - key: config.yaml
            path: config.yaml
      - name: opa-policy
        configMap:
          name: opa-ext-authz-config
          items:
          - key: policy.rego
            path: policy.rego
EOF
```

Wait for the OPA pod to be ready
```bash
kubectl rollout status deployment/opa-ext-authz -n agentgateway-system --timeout=60s
```

### Step 3: Expose OPA over gRPC

The `appProtocol: kubernetes.io/h2c` annotation tells the gateway that this backend speaks gRPC (HTTP/2 cleartext).

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  namespace: agentgateway-system
  name: opa-ext-authz
  labels:
    app: opa-ext-authz
spec:
  ports:
  - name: grpc-authz
    port: 9191
    targetPort: 9191
    protocol: TCP
    appProtocol: kubernetes.io/h2c
  selector:
    app: opa-ext-authz
EOF
```

---

## Part 1: Protect an LLM route with OPA

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
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai
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

### Step 2: Verify the route works before applying OPA

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Whats your favorite poem?"}
    ]
  }'
```

You should get a 200 response with a completion from OpenAI.

### Step 3: Attach OPA as the ext-authz backend

By targeting the HTTPRoute instead of the Gateway, only the OpenAI route requires ext-authz — other routes remain unaffected.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  namespace: agentgateway-system
  name: openai-opa-ext-auth-policy
  labels:
    app: opa-ext-authz
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  traffic:
    extAuth:
      backendRef:
        name: opa-ext-authz
        namespace: agentgateway-system
        port: 9191
      grpc: {}
EOF
```

### Step 4: Test — request denied without required header

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Whats your favorite poem?"}
    ]
  }'
```

Expected output:
```
HTTP/1.1 403 Forbidden
```

Check OPA's decision log to see the rejection:
```bash
kubectl logs -n agentgateway-system -l app=opa-ext-authz --tail=20
```

You should see a decision-log entry with `"result": false` and the request attributes OPA evaluated.

### Step 5: Test — request allowed with required header

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "x-ext-authz: allow" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Whats your favorite poem?"}
    ]
  }'
```

You should get a 200 response with a completion from OpenAI. The OPA decision log will now show `"result": true`.

---

## Part 2: Reuse the same OPA service to protect an MCP route

The same OPA service can protect any number of routes. We'll attach it to an MCP route that proxies to the external Solo.io docs MCP server — no in-cluster MCP deployment required.

### Step 1: Create the MCP backend and route

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
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
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
EOF
```

### Step 2: Attach the same OPA backend to the MCP route

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  namespace: agentgateway-system
  name: mcp-opa-ext-auth-policy
  labels:
    app: opa-ext-authz
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: soloio-docs-mcp
  traffic:
    extAuth:
      backendRef:
        name: opa-ext-authz
        namespace: agentgateway-system
        port: 9191
      grpc: {}
EOF
```

> **Note:** ext-authz operates at the HTTP transport layer — OPA sees the request method, path, and headers but does not inspect the MCP protocol payload (e.g., which tool is being called). For per-tool authorization, layer the built-in `mcpAuthorization` CEL policy on top of this ext-authz check.

### Step 3: Test — MCP request denied without required header

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
```

### Step 4: Test — MCP request allowed with required header

```bash
# Initialize
curl -s -D /tmp/mcp-headers.txt "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-ext-authz: allow" \
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
  -H "x-ext-authz: allow" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

sleep 2

# List tools
curl -s "$GATEWAY_IP:8080/mcp" \
  -H "content-type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -H "x-ext-authz: allow" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":2}'
```

Expected:
```json
{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"search",...},{"name":"get_chunks",...}]}}
```

---

## View Access Logs

OPA decision logs (every allow/deny with full request attributes):
```bash
kubectl logs -n agentgateway-system -l app=opa-ext-authz --tail=20
```

Agentgateway proxy logs (request flow):
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

---

## Alternative: Serve the policy as an OPA bundle

For production, most teams version-control their Rego and ship it to OPA as a **bundle** — a signed tarball OPA fetches over HTTP or OCI. The gateway-facing setup is identical; only OPA's config and volumes change.

### Step 1: Build and host the bundle

Bundle layout (a tarball of one or more `.rego` files plus an optional `.manifest`):
```
bundle.tar.gz
└── envoy/
    └── authz.rego
```

Build it locally and put it on an in-cluster HTTP server:

```bash
mkdir -p /tmp/opa-bundle/envoy
cat > /tmp/opa-bundle/envoy/authz.rego <<'EOF'
package envoy.authz
import rego.v1
default allow := false
allow if {
  input.attributes.request.http.headers["x-ext-authz"] == "allow"
}
EOF
tar -czf /tmp/bundle.tar.gz -C /tmp/opa-bundle .

# Stash the tarball in a ConfigMap so an nginx pod can serve it
kubectl create configmap opa-bundle-files -n agentgateway-system \
  --from-file=bundle.tar.gz=/tmp/bundle.tar.gz \
  --dry-run=client -oyaml | kubectl apply -f -
```

Deploy a tiny nginx to serve the bundle:

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: opa-bundle-server
  namespace: agentgateway-system
spec:
  replicas: 1
  selector: { matchLabels: { app: opa-bundle-server } }
  template:
    metadata: { labels: { app: opa-bundle-server } }
    spec:
      containers:
      - name: nginx
        image: nginx:alpine
        ports: [{ containerPort: 80 }]
        volumeMounts:
        - name: bundle
          mountPath: /usr/share/nginx/html
      volumes:
      - name: bundle
        configMap:
          name: opa-bundle-files
---
apiVersion: v1
kind: Service
metadata:
  name: opa-bundle-server
  namespace: agentgateway-system
spec:
  selector: { app: opa-bundle-server }
  ports: [{ port: 80, targetPort: 80 }]
EOF
```

### Step 2: Reconfigure OPA to pull the bundle

Replace `opa-ext-authz-config` with a bundle-aware config and drop the inline `policy.rego`. OPA polls the bundle URL every 30 s by default and hot-reloads on change.

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: opa-ext-authz-config
  namespace: agentgateway-system
data:
  config.yaml: |
    services:
      bundle-server:
        url: http://opa-bundle-server.agentgateway-system.svc.cluster.local
    bundles:
      authz:
        service: bundle-server
        resource: /bundle.tar.gz
        polling:
          min_delay_seconds: 30
          max_delay_seconds: 60
    plugins:
      envoy_ext_authz_grpc:
        addr: :9191
        path: envoy/authz/allow
        dry-run: false
        enable-reflection: false
    decision_logs:
      console: true
EOF
```

Re-apply the Deployment without the `policy.rego` volume and arg — OPA now gets its policy from the bundle plugin instead of a mounted file. The readiness probe is updated to `/health?plugins&bundles` so it waits for both the Envoy plugin and a successful bundle activation before going Ready.

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  namespace: agentgateway-system
  name: opa-ext-authz
  labels:
    app: opa-ext-authz
spec:
  replicas: 1
  selector:
    matchLabels:
      app: opa-ext-authz
  template:
    metadata:
      labels:
        app: opa-ext-authz
        app.kubernetes.io/name: opa-ext-authz
    spec:
      containers:
      - name: opa
        image: openpolicyagent/opa:latest-envoy-static
        args:
        - "run"
        - "--server"
        - "--addr=:8181"
        - "--config-file=/config/config.yaml"
        ports:
        - name: grpc-authz
          containerPort: 9191
        - name: http-api
          containerPort: 8181
        volumeMounts:
        - name: opa-config
          mountPath: /config
        readinessProbe:
          httpGet:
            path: /health?plugins&bundles
            port: 8181
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
      - name: opa-config
        configMap:
          name: opa-ext-authz-config
          items:
          - key: config.yaml
            path: config.yaml
EOF
kubectl rollout status deployment/opa-ext-authz -n agentgateway-system --timeout=90s
```

> **Tip:** In production, replace the in-cluster nginx with an S3 / GCS bucket, an OCI registry, or a hosted policy server (Styra DAS, etc.). The OPA config shape is the same; only the `services` entry changes.

### Step 3: Verify

Re-run the deny/allow tests from Part 1 — they should behave identically, because the policy is the same; only its delivery channel changed.

```bash
# Should be 403
curl -sS -o /dev/null -w "no header: %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'

# Should be 200
curl -sS -o /dev/null -w "with header: %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "x-ext-authz: allow" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
```

To prove the policy came from the bundle, check the decision log — each entry now includes a `bundles` field naming the active bundle:

```bash
kubectl logs -n agentgateway-system -l app=opa-ext-authz --tail=5 | grep -o '"bundles":{[^}]*}' | head -3
# Expected: "bundles":{"authz":{}}
```

---

## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system openai-opa-ext-auth-policy mcp-opa-ext-auth-policy
kubectl delete httproute -n agentgateway-system openai soloio-docs-mcp
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai soloio-docs-mcp
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete deployment -n agentgateway-system opa-ext-authz
kubectl delete service -n agentgateway-system opa-ext-authz
kubectl delete configmap -n agentgateway-system opa-ext-authz-config
# If you completed the bundle alternative
kubectl delete deployment -n agentgateway-system opa-bundle-server 2>/dev/null
kubectl delete service -n agentgateway-system opa-bundle-server 2>/dev/null
kubectl delete configmap -n agentgateway-system opa-bundle-files 2>/dev/null
```
