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
kubectl create namespace mcp
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
- From the menu bar, click the Tools tab. Then from the Tools pane, click List Tools and select the echo tool.
- In the message field, enter `Hello from AgentGateway!` and click Run Tool.
- Verify the response echoes your message back.

Try the **get-sum** tool as well — enter two numbers and confirm the result is returned.

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

Add a second replica. The Service automatically routes across both pods via kube-proxy — and since the dynamic Backend discovers the Service (not individual pods), no gateway configuration changes at all:

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

In MCP Inspector, connect and run **echo** or **get-env** several times. Watch the logs — all requests from your current session land on the same pod. AgentGateway encodes the backend endpoint into the session token at connection time, so a client stays pinned to one pod for the lifetime of that session.

### Observe load balancing on reconnect

Disconnect from MCP Inspector and reconnect. AgentGateway assigns a new session token, this time potentially routing to the other replica. Run **echo** again and check the logs — you may now see the second pod handling requests. Reconnect a few times to observe the distribution across both pods.

No Backend or HTTPRoute change was required at any point.

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

You should see MCP-specific metrics such as:
- `agentgateway_mcp_tool_calls_total`
- `agentgateway_mcp_server_requests_total`
- `agentgateway_mcp_request_duration_seconds`

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

### View Traces in Grafana

To view distributed traces with MCP-specific spans:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Filter traces by service, operation, or trace ID to find AgentGateway requests

Traces include MCP-specific spans with information like `mcp.method`, `mcp.resource`, `mcp.resource.name`, `mcp.target`, and more.

### View Access Logs

AgentGateway automatically logs detailed information about MCP requests to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Example output shows comprehensive request details including MCP-specific information like `mcp.method`, `mcp.resource`, `mcp.resource.name`, `mcp.target`, and trace IDs for correlation with distributed traces in Grafana.

### (Optional) View Traces in Jaeger

If you installed Jaeger in the [002 — Set Up Monitoring Tools (OCP)](../installation/openshift/002-set-up-monitoring-tools-ocp.md) lab instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with MCP-specific spans including `mcp.method`, `mcp.resource`, `mcp.resource.name`, `mcp.target`, and more

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

From the MCP Inspector, verify that the connection fails with an error message similar to the following, because no valid JWT was provided from the MCP inspector tool (MCP client) to the agentgateway proxy.
```
MCP error -32001: Error POSTing to endpoint (HTTP 403): authentication failure: no bearer token found
```

We should also be able to see this error in the access logs `authentication failure: no bearer token found` with an `http.status: 403`
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

### Provide a valid JWT
Go back to the MCP Inspector tool and expand the Authentication section. Enter the following details in the API Token Authentication card

- Header Name: Enter `Authorization`
- Bearer Token: Enter `Bearer ` followed by the JWT token below. The MCP Inspector sends this value as-is in the Authorization header, so the `Bearer ` prefix is required.
```
eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw
```

After entering the token, click **Reconnect** in the MCP Inspector to re-establish the connection with the new credentials.

Now, if you try to run the `echo` tool again it should result in `Tool Result: Success`

### Authorize based on JWT Claims
You can limit access to the MCP server based on specific JWT claims with CEL-based RBAC rules.

Update the EnterpriseAgentgatewayPolicy to add your RBAC rules. In the following example, you use a CEL expression to only allow access to the MCP server if the JWT has the org=ai-admins claim

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: jwt-rbac
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    authorization:
      policy:
        matchExpressions:
          - 'jwt.org == "admin"'
EOF
```

Now, if you try to run the `echo` tool again it should fail because our user is not allowed to access this endpoint anymore

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

## Limit tool access
We can also extend our CEL expression to limit tool access so that anyone who is a part of the `solo.io` org can use the echo tool
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: jwt-rbac
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    authorization:
      policy:
        matchExpressions:
          - 'jwt.org == "solo.io"'
EOF
```
Now, if you try to run the `echo` tool again it should result in `Tool Result: Success`


## Cleanup
```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system jwt
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system jwt-rbac
kubectl delete httproute -n agentgateway-system mcp
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mcp-backend
kubectl delete deployment -n mcp mcp-server-everything
kubectl delete service -n mcp mcp-server-everything
```
