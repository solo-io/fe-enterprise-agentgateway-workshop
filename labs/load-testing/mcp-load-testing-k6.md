# MCP Load Testing with k6

In this lab, you will load-test MCP (Model Context Protocol) traffic through Enterprise AgentGateway using k6. You will deploy a lightweight mock MCP server, configure AgentGateway to proxy it, then run two complementary load tests: one that ramps virtual users (concurrent sessions) and one that targets a specific requests-per-second rate. Observe latency and throughput in the existing Grafana/Prometheus stack.

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives

- Deploy a mock MCP server for load testing
- Configure AgentGateway MCP routing
- Run a VU-based (concurrent-session) k6 load test
- Run an RPS-based (sustained-throughput) k6 load test
- Interpret p95 latency, requests/sec, and error rate in Grafana

## Deploy Mock MCP Server

Deploy a lightweight Python MCP echo server. The server script is stored in a ConfigMap and responds to `initialize`, `tools/list`, and `tools/call` requests:

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: fast-mcp-script
  namespace: agentgateway-system
data:
  server.py: |
    #!/usr/bin/env python3
    import http.server, json, uuid, os
    from socketserver import ThreadingMixIn

    PORT = int(os.environ.get('PORT', 3000))

    class ThreadingHTTPServer(ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    class MCPHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/mcp':
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            method = body.get('method', '')
            req_id = body.get('id')
            session_id = self.headers.get('mcp-session-id', str(uuid.uuid4()))
            if method == 'initialize':
                self._json(req_id, {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fast-mcp","version":"1.0"}}, session_id)
            elif method == 'notifications/initialized':
                self.send_response(202); self.send_header('Content-Length','0'); self.end_headers()
            elif method == 'tools/list':
                self._json(req_id, {"tools":[{"name":"echo","description":"Echo a message","inputSchema":{"type":"object","properties":{"message":{"type":"string"}}}}]})
            elif method == 'tools/call':
                msg = body.get('params',{}).get('arguments',{}).get('message','')
                self._json(req_id, {"content":[{"type":"text","text":msg}]})
            else:
                self.send_response(200); self.send_header('Content-Length','0'); self.end_headers()
        def _json(self, req_id, result, session_id=None):
            payload = json.dumps({"jsonrpc":"2.0","id":req_id,"result":result}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length', str(len(payload)))
            if session_id: self.send_header('mcp-session-id', session_id)
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args): pass

    ThreadingHTTPServer(('', PORT), MCPHandler).serve_forever()
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fast-mcp
  namespace: agentgateway-system
  labels:
    app: fast-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: fast-mcp
  template:
    metadata:
      labels:
        app: fast-mcp
    spec:
      containers:
      - name: fast-mcp
        image: python:3.12-alpine
        command: ["python3", "/scripts/server.py"]
        ports:
        - name: mcp-http
          containerPort: 3000
        env:
        - name: PORT
          value: "3000"
        readinessProbe:
          tcpSocket:
            port: 3000
          initialDelaySeconds: 5
          periodSeconds: 10
          failureThreshold: 3
        livenessProbe:
          tcpSocket:
            port: 3000
          initialDelaySeconds: 10
          periodSeconds: 30
        resources:
          requests:
            cpu: "100m"
            memory: "64Mi"
          limits:
            cpu: "500m"
            memory: "128Mi"
        volumeMounts:
        - name: server-script
          mountPath: /scripts
          readOnly: true
      volumes:
      - name: server-script
        configMap:
          name: fast-mcp-script
---
apiVersion: v1
kind: Service
metadata:
  name: fast-mcp-svc
  namespace: agentgateway-system
  labels:
    app: fast-mcp
spec:
  selector:
    app: fast-mcp
  ports:
  - name: mcp-http
    port: 3000
    targetPort: 3000
    appProtocol: agentgateway.dev/mcp
  type: ClusterIP
EOF
```

Wait for the deployment to be ready:

```bash
kubectl rollout status deployment/fast-mcp -n agentgateway-system
```

## Configure AgentGateway MCP Routing

Create an `EnterpriseAgentgatewayBackend` resource that points AgentGateway to the mock MCP server, then create an `HTTPRoute` that forwards `/mcp` traffic to it:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: fast-mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
    - name: fast-mcp
      selector:
        namespaces:
          matchLabels:
            kubernetes.io/metadata.name: agentgateway-system
        services:
          matchLabels:
            app: fast-mcp
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: fast-mcp
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
    - name: fast-mcp-backend
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
    timeouts:
      request: "0s"
EOF
```

Verify the HTTPRoute was accepted:

```bash
kubectl get httproute fast-mcp -n agentgateway-system
```

Expected: `ACCEPTED` status in the `ACCEPTED` column.

## Reduce Log Verbosity (Optional)

For load testing, reduce AgentGateway log verbosity to prevent disk pressure:

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type=merge \
  -p '{"spec":{"logging":{"level":"warn"}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

## Smoke Test the MCP Route

Get the gateway IP and verify a full MCP initialize handshake succeeds before running load tests:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -si -X POST "http://${GATEWAY_IP}:8080/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "smoke-test", "version": "1.0"}
    }
  }' | head -20
```

Expected response: HTTP 200 with an `mcp-session-id` header. AgentGateway wraps MCP responses in SSE format, so the body will look like:

```
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",...}}
```

## VU-Based Load Test (Concurrent Sessions)

This test answers: **how many concurrent MCP sessions can AgentGateway handle?**

It uses k6's `ramping-vus` executor to ramp from 5 to 25 virtual users. Each VU establishes its own MCP session (initialize → notifications/initialized) on first use, then repeatedly calls `tools/call`. Requests are tagged with `mcp_method` so Grafana can break down latency per operation.

### Create Load Generator Namespace and Service Account

```bash
kubectl create namespace loadgenerator --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: k6s-loadgen
  namespace: loadgenerator
EOF
```

### Deploy VU Test Script

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: k6-mcp-vus-script
  namespace: loadgenerator
data:
  mcp-vus.js: |-
    import http from 'k6/http';
    import { check } from 'k6';
    import { Counter } from 'k6/metrics';

    const mcpErrors = new Counter('mcp_errors');
    const BASE_URL = __ENV.GATEWAY_URL;
    const MCP_PATH = `${BASE_URL}/mcp`;

    // VU-scoped session ID — persists across iterations for this VU
    let sessionId = null;

    export const options = {
      discardResponseBodies: true,
      stages: [
        { duration: '15s', target: 5  },
        { duration: '30s', target: 5  },
        { duration: '15s', target: 25 },
        { duration: '30s', target: 25 },
        { duration: '30s', target: 0  },
      ],
      thresholds: {
        'http_req_duration{mcp_method:tools_call}': ['p(95)<500'],
        'http_req_failed': ['rate<0.01'],
      },
    };

    function mcpPost(body, sid, methodTag) {
      const headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
      };
      if (sid) headers['mcp-session-id'] = sid;
      return http.post(MCP_PATH, JSON.stringify(body), {
        headers,
        tags: methodTag ? { mcp_method: methodTag } : {},
      });
    }

    export default function () {
      // Initialize session once per VU (on first iteration)
      if (!sessionId) {
        const res = mcpPost({
          jsonrpc: '2.0',
          id: 1,
          method: 'initialize',
          params: {
            protocolVersion: '2024-11-05',
            capabilities: {},
            clientInfo: { name: 'k6-mcp-vu-test', version: '1.0' },
          },
        }, null, 'initialize');

        if (!check(res, { 'initialize: status 200': (r) => r.status === 200 })) {
          mcpErrors.add(1);
          return;
        }
        sessionId = res.headers['Mcp-Session-Id'];
        if (!sessionId) {
          mcpErrors.add(1);
          return;
        }
        mcpPost({
          jsonrpc: '2.0',
          method: 'notifications/initialized',
        }, sessionId, 'initialized_notification');
        return; // skip tool call on initialization iteration
      }

      // tools/call x2 per iteration
      for (let i = 0; i < 2; i++) {
        const res = mcpPost({
          jsonrpc: '2.0',
          id: Date.now() + i,
          method: 'tools/call',
          params: { name: 'echo', arguments: { message: 'k6-vu-ping' } },
        }, sessionId, 'tools_call');

        check(res, { 'tools/call: status 200': (r) => r.status === 200 });
      }
    }
EOF
```

### Run VU Load Test Job

```bash
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-mcp-vus
  namespace: loadgenerator
spec:
  completions: 1
  parallelism: 1
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: k6s-loadgen
      containers:
      - name: k6
        image: grafana/k6:0.54.0
        command:
        - sh
        - -c
        - |
          k6 run \
            --summary-trend-stats 'min,avg,med,max,p(95),p(99)' \
            --discard-response-bodies \
            --tag test_run_id=mcp-vus \
            /scripts/mcp-vus.js
        env:
        - name: GATEWAY_URL
          value: "http://agentgateway-proxy.agentgateway-system.svc.cluster.local:8080"
        volumeMounts:
        - name: k6-script
          mountPath: /scripts
          readOnly: true
        securityContext:
          runAsUser: 1000
          runAsGroup: 1000
          runAsNonRoot: true
      volumes:
      - name: k6-script
        configMap:
          name: k6-mcp-vus-script
EOF
```

### Monitor the VU Load Test

Check job status and stream logs:

```bash
kubectl get job k6-mcp-vus -n loadgenerator
kubectl logs -f job/k6-mcp-vus -n loadgenerator
```

While the test runs, open Grafana to watch live metrics:

```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

Access at http://localhost:3000 (admin / prom-operator). Navigate to **Dashboards > AgentGateway Overview**.

Key panels to watch:
- **Request Rate** — total requests/sec through AgentGateway on the `/mcp` route
- **Request Duration** — p50/p95 latency (target: p95 < 500ms)
- **Error Rate** — should stay < 1%

The k6 terminal output shows a summary table after the test completes:

```
http_req_duration............: avg=XX  min=XX  med=XX  max=XX  p(95)=XX p(99)=XX
http_req_failed..............: X.XX%
mcp_errors...................: X
```

A run with p95 < 500ms at 25 VUs and error rate < 1% confirms the gateway handles concurrent MCP sessions under this load profile.

## RPS-Based Load Test (Sustained Throughput)

This test answers: **can AgentGateway sustain X MCP requests per second?**

It uses k6's `ramping-arrival-rate` executor to target a specific request rate (iterations/sec) rather than a VU count. k6 automatically allocates and recycles VUs to hit the target. VUs initialize their MCP session on first use, then each iteration fires one `tools/call`. This isolates the throughput question from session-creation overhead. The test ramps from 25 to 50 req/s.

### Deploy RPS Test Script

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: k6-mcp-rps-script
  namespace: loadgenerator
data:
  mcp-rps.js: |-
    import http from 'k6/http';
    import { check } from 'k6';
    import { Counter } from 'k6/metrics';

    const mcpErrors = new Counter('mcp_errors');
    const BASE_URL = __ENV.GATEWAY_URL;
    const MCP_PATH = `${BASE_URL}/mcp`;

    // VU-scoped session ID — persists across iterations for this VU
    let sessionId = null;

    export const options = {
      discardResponseBodies: true,
      scenarios: {
        mcp_rps: {
          executor: 'ramping-arrival-rate',
          startRate: 5,
          timeUnit: '1s',
          preAllocatedVUs: 10,
          maxVUs: 75,
          stages: [
            { duration: '15s', target: 25 },
            { duration: '30s', target: 25 },
            { duration: '15s', target: 50 },
            { duration: '30s', target: 50 },
            { duration: '30s', target: 0  },
          ],
        },
      },
      thresholds: {
        'http_req_duration{mcp_method:tools_call}': ['p(95)<500'],
        'dropped_iterations': ['count<10'],
      },
    };

    function mcpPost(body, sid, methodTag) {
      const headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
      };
      if (sid) headers['mcp-session-id'] = sid;
      return http.post(MCP_PATH, JSON.stringify(body), {
        headers,
        tags: methodTag ? { mcp_method: methodTag } : {},
      });
    }

    export default function () {
      // Initialize session once per VU (on first iteration)
      if (!sessionId) {
        const res = mcpPost({
          jsonrpc: '2.0',
          id: 1,
          method: 'initialize',
          params: {
            protocolVersion: '2024-11-05',
            capabilities: {},
            clientInfo: { name: 'k6-mcp-rps-test', version: '1.0' },
          },
        }, null, 'initialize');

        if (!check(res, { 'initialize: status 200': (r) => r.status === 200 })) {
          mcpErrors.add(1);
          return;
        }
        sessionId = res.headers['Mcp-Session-Id'];
        if (!sessionId) {
          mcpErrors.add(1);
          return;
        }
        mcpPost({
          jsonrpc: '2.0',
          method: 'notifications/initialized',
        }, sessionId, 'initialized_notification');
        return; // skip tool call on initialization iteration
      }

      // One tools/call per iteration — maps directly to target RPS
      const res = mcpPost({
        jsonrpc: '2.0',
        id: Date.now(),
        method: 'tools/call',
        params: { name: 'echo', arguments: { message: 'k6-rps-ping' } },
      }, sessionId, 'tools_call');

      check(res, { 'tools/call: status 200': (r) => r.status === 200 });
    }
EOF
```

### Run RPS Load Test Job

Wait for the VU test to finish before running the RPS test (avoids interference):

```bash
kubectl wait --for=condition=Complete job/k6-mcp-vus -n loadgenerator --timeout=10m
```

Then deploy the RPS job:

```bash
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-mcp-rps
  namespace: loadgenerator
spec:
  completions: 1
  parallelism: 1
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: k6s-loadgen
      containers:
      - name: k6
        image: grafana/k6:0.54.0
        command:
        - sh
        - -c
        - |
          k6 run \
            --summary-trend-stats 'min,avg,med,max,p(95),p(99)' \
            --discard-response-bodies \
            --tag test_run_id=mcp-rps \
            /scripts/mcp-rps.js
        env:
        - name: GATEWAY_URL
          value: "http://agentgateway-proxy.agentgateway-system.svc.cluster.local:8080"
        volumeMounts:
        - name: k6-script
          mountPath: /scripts
          readOnly: true
        securityContext:
          runAsUser: 1000
          runAsGroup: 1000
          runAsNonRoot: true
      volumes:
      - name: k6-script
        configMap:
          name: k6-mcp-rps-script
EOF
```

### Monitor the RPS Load Test

```bash
kubectl get job k6-mcp-rps -n loadgenerator
kubectl logs -f job/k6-mcp-rps -n loadgenerator
```

Watch Grafana (**Dashboards > AgentGateway Overview**) for:
- **Request Rate** — should step from ~25 req/s to ~50 req/s matching the stage transitions
- **Request Duration** — watch for p95 latency increase at 50 req/s
- **`dropped_iterations`** in the k6 summary — if this exceeds 10, AgentGateway is at capacity

The k6 terminal output after the run:

```
scenarios: (100.00%) 1 scenario, 75 max VUs, ...
  mcp_rps: ramping-arrival-rate ...

http_req_duration............: avg=XX   min=XX   med=XX  max=XX   p(95)=XX  p(99)=XX
  { mcp_method:tools_call }...: avg=XX   ...
dropped_iterations...........: X (X.XX/s)
```

A `dropped_iterations` count near zero at 50 req/s means the gateway is handling the load. A rising count indicates the gateway or mock server is the bottleneck.

## Interpreting Results

### VU Test vs RPS Test

| Dimension | VU Test (`ramping-vus`) | RPS Test (`ramping-arrival-rate`) |
|-----------|------------------------|----------------------------------|
| What you control | Concurrent sessions (VUs) | Target requests/sec |
| What k6 adjusts | Throughput (varies with VU latency) | VU count (scales to hit target rate) |
| Best for | "How many concurrent clients?" | "Can we sustain X req/s?" |
| Key metric | p95 latency as VUs ramp | `dropped_iterations` as RPS ramps |

### Reading k6 Output

- **`http_req_duration{mcp_method:tools_call}`** — latency for actual MCP tool calls only (excludes session initialization). This is the number to compare across runs.
- **`http_req_failed`** — any non-2xx response. Should stay < 1%.
- **`dropped_iterations`** (RPS test only) — iterations k6 could not start because it ran out of VUs. A value > 10 means you've found the gateway's saturation point.
- **`mcp_errors`** — custom counter for protocol-level failures (no session ID returned, etc.).

### Prometheus Metrics in Grafana

Query these in Prometheus (http://localhost:9090 after port-forwarding):

```promql
# MCP request rate
rate(agentgateway_requests_total{route="fast-mcp"}[1m])

# MCP request latency p95
histogram_quantile(0.95, rate(agentgateway_request_duration_seconds_bucket{route="fast-mcp"}[1m]))
```

## Cleanup

Delete the k6 jobs and load generator resources:

```bash
kubectl delete job k6-mcp-vus k6-mcp-rps -n loadgenerator --ignore-not-found
kubectl delete configmap k6-mcp-vus-script k6-mcp-rps-script -n loadgenerator --ignore-not-found
kubectl delete serviceaccount k6s-loadgen -n loadgenerator --ignore-not-found
kubectl delete namespace loadgenerator --ignore-not-found
```

Delete the AgentGateway routing configuration:

```bash
kubectl delete httproute fast-mcp -n agentgateway-system
kubectl delete enterpriseagentgatewaybackend fast-mcp-backend -n agentgateway-system
```

Delete the mock MCP server:

```bash
kubectl delete deployment fast-mcp -n agentgateway-system
kubectl delete service fast-mcp-svc -n agentgateway-system
kubectl delete configmap fast-mcp-script -n agentgateway-system
```

Restore AgentGateway logging level (if you reduced it):

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type=merge \
  -p '{"spec":{"logging":{"level":"info"}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
```
