# Dynamic MCP with Label Selectors

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Understand the difference between static and dynamic MCP backends
- Deploy the `mcp-server-everything` reference MCP server
- Configure a dynamic `AgentgatewayBackend` using label selectors
- Validate connectivity and tool execution with MCP Inspector
- Demonstrate zero-downtime backend updates without touching the Backend resource

## Overview

### Static vs. Dynamic Backends

The previous MCP labs used **static** backends, where you hard-code the target host and port directly in the `AgentgatewayBackend` resource:

```yaml
spec:
  mcp:
    targets:
    - name: my-target
      static:
        host: mcp-website-fetcher.agentgateway-system.svc.cluster.local
        port: 80
        protocol: SSE
```

This creates tight coupling between your gateway configuration and your MCP server deployment. Any time the service name changes, the port shifts, or you want to migrate to a new implementation, you must update the Backend resource — which means a change to gateway config just to update an application.

**Dynamic backends** break this coupling by using Kubernetes label selectors instead of hard-coded coordinates:

```yaml
spec:
  mcp:
    targets:
    - name: my-target
      selector:
        services:
          matchLabels:
            app: my-mcp-server
```

AgentGateway watches the cluster for Services matching those labels and automatically wires them up as targets. The gateway configuration becomes a stable contract; only the application layer changes when you deploy or update MCP servers.

### Why Dynamic Backends Matter

| Concern | Static Backend | Dynamic Backend |
|---|---|---|
| Update MCP server image | Must also update Backend if service name changes | Deploy new pods — gateway auto-discovers them |
| Scale to multiple replicas | Single target, no built-in replica awareness | AgentGateway load-balances across all matching pods |
| Ownership boundary | Platform and app teams both touch Backend resource | Platform team owns Backend, app team owns Service labels |
| GitOps stability | Gateway config drifts with every app deployment | Gateway config stays static; app manifests change independently |

This is particularly valuable in enterprise environments where gateway configuration is managed by a platform team and MCP server deployments are owned by individual product teams. Dynamic backends enforce that separation of concerns at the API level.

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

- `appProtocol: kgateway.dev/mcp` on the Service port — tells AgentGateway to speak the MCP protocol when connecting to this service
- `app: mcp-server-everything` label on both the Deployment and Service — this is the label the dynamic backend will select

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
      appProtocol: kgateway.dev/mcp
EOF
```

Verify the pod comes up:
```bash
kubectl rollout status deployment/mcp-server-everything -n mcp
```

---

## Step 2: Create a Dynamic Backend and HTTPRoute

Instead of providing a `static` host and port, we provide a `selector` that matches the Service labels. AgentGateway discovers the backing service at runtime.

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mcp-everything-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
      - name: mcp-server-everything
        selector:
          namespaces:
            matchLabels:
              kubernetes.io/metadata.name: mcp
          services:
            matchLabels:
              app: mcp-server-everything
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-everything
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
        - name: mcp-everything-backend
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "0s"
EOF
```

**Key configuration details:**

- `selector.services.matchLabels` — AgentGateway uses this to find matching Services in the cluster
- The `AgentgatewayBackend` itself does not reference any hostname or port — those are resolved dynamically from the discovered Service
- Path prefix `/mcp` isolates this backend from other MCP routes on the same gateway

---

## Step 3: Verify with MCP Inspector

### Get the gateway address
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo $GATEWAY_IP
```

### Run MCP Inspector
```bash
npx @modelcontextprotocol/inspector@0.21.1
```

Connect to your AgentGateway:
- **Transport Type**: Select `Streamable HTTP`
- **URL**: Enter `http://$GATEWAY_IP:8080/mcp` (replace with your actual IP/hostname)
- Click **Connect**

### List and run tools

1. Click the **Tools** tab in the menu bar
2. Click **List Tools** — you should see all tools provided by `mcp-server-everything`
3. Select the **echo** tool
4. In the **message** field, enter `Hello from AgentGateway!`
5. Click **Run Tool**
6. Verify the response echoes your message back

Try the **get-sum** tool as well — enter two numbers and confirm the result is returned.

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

---

## Observability

### View access logs
```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 5
```

Look for MCP-specific fields in the structured log output: `mcp.method`, `mcp.resource`, `mcp.target`, and `http.status`.

### View MCP metrics
```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep mcp && kill $!
```

You should see:
- `agentgateway_mcp_tool_calls_total`
- `agentgateway_mcp_server_requests_total`
- `agentgateway_mcp_request_duration_seconds`

### View in Grafana

1. Port-forward Grafana:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```
2. Open http://localhost:3000 (username: `admin`, password: `prom-operator`)
3. Navigate to **Dashboards > AgentGateway Dashboard**
4. The **MCP metrics** section shows tool call rates, server request counts, and request durations for the dynamically-discovered backend

---

## Cleanup

```bash
kubectl delete httproute -n agentgateway-system mcp-everything
kubectl delete agentgatewaybackend -n agentgateway-system mcp-everything-backend
kubectl delete deployment -n mcp mcp-server-everything
kubectl delete service -n mcp mcp-server-everything
```

