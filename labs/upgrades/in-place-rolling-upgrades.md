# Validating Zero Downtime During In-Place Rolling Upgrades

In this lab you'll configure Enterprise Agentgateway for zero-downtime in-place upgrades, then validate it by driving continuous traffic through a proxy rollout and measuring the result. You'll test three traffic patterns — short completions, long-lived streaming, and MCP/SSE sessions — to see what is zero-downtime, what is bounded by the drain window, and what is not.

This lab focuses on validating upgrade behavior. The configuration reference for graceful shutdown, Pod Disruption Budgets, and topology spread lives in the [Production Observability, Alerting & Scaling](../observability/production-observability-alerting-and-scaling.md) lab; here we apply a minimal subset and measure the outcome.

## Pre-requisites
- [001 — Install Enterprise Agentgateway](../../001-install-enterprise-agentgateway.md)
- [002 — Set Up UI and Monitoring Tools](../../002-set-up-ui-and-monitoring-tools.md)
- [Configure Mock OpenAI Server](../routing/configure-mock-openai-server.md) — provides the `mock-gpt-4o` backend on the `/openai` route
- A cluster with **at least 2 proxy replicas** (this lab configures that in the first section)

## Lab Objectives
- Explain Agentgateway's default connection-draining behavior
- Apply the minimum zero-downtime posture: ≥2 replicas + PDB + graceful shutdown
- Measure zero downtime under live traffic for three connection types
- Map the validated rollout to the real production `helm upgrade`

## How Draining Works

When a proxy pod is replaced during a rollout, Kubernetes sends it `SIGTERM`. Agentgateway then drains gracefully:

1. Minimum drain period (`spec.shutdown.min`, default `10s`) — the proxy keeps accepting connections but signals clients to migrate: `Connection: close` for HTTP/1 and `GOAWAY` for HTTP/2. Load balancers and clients shift new traffic to the other healthy pod.
2. Drain in-flight requests — after the minimum period, the proxy stops accepting new connections and waits for active request handlers to finish.
3. Maximum drain period (`spec.shutdown.max`, default `60s`) — a hard deadline. Connections still active after this are forcibly closed.
4. SIGKILL — sent by Kubernetes at `terminationGracePeriodSeconds`, which must be greater than or equal to `spec.shutdown.max`. The operator derives this value automatically from `shutdown.max`, so you do not set it by hand.

What this means for AI traffic:

| Traffic | What draining means |
|---|---|
| Short requests | Complete well within `shutdown.min`; new requests route to the healthy pod. No impact. |
| Long-lived streams | In-flight streams continue until they finish or hit `shutdown.max`, whichever comes first. Bounded impact. |
| Stateful SSE sessions (single replica) | The session's only replica is going away, so the session breaks. Requires a stateless transport to avoid. |

We test all three below.

## Step 1 — Configure Replicas, a PDB, and Graceful Shutdown

Zero downtime requires three things working together: two or more replicas (so traffic always has a healthy pod), a Pod Disruption Budget (so the rollout never takes the last pod), and graceful shutdown (so in-flight work drains). Apply them to the `agentgateway-config` parameters object:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-config
  namespace: agentgateway-system
spec:
  deployment:
    spec:
      replicas: 2
  podDisruptionBudget:
    spec:
      minAvailable: 1
  shutdown:
    min: 15
    max: 110
  # ── Topology spread (commented out) ───────────────────────────────
  # Requires a multi-node cluster — not exercised on single-node vind.
  # See the Production Observability, Alerting & Scaling lab for the full
  # topology-spread / anti-affinity reference.
  #   deployment:
  #     spec:
  #       template:
  #         spec:
  #           topologySpreadConstraints:
  #             - maxSkew: 1
  #               topologyKey: kubernetes.io/hostname
  #               whenUnsatisfiable: ScheduleAnyway
  #               labelSelector:
  #                 matchLabels:
  #                   app.kubernetes.io/name: agentgateway-proxy
EOF
```

A few notes on this config:

- We raise `shutdown.min`/`shutdown.max` above their defaults (10s/60s) so long-lived streams have room to finish draining before the deadline.
- `kubectl apply` merges into the existing object. Fields not listed here (logging, rawConfig, service, resource requests) are preserved; only `deployment.spec.replicas`, `podDisruptionBudget.spec.minAvailable`, and `shutdown` are added or changed.
- On v2026.6.1, the operator sets `terminationGracePeriodSeconds` equal to `shutdown.max` (both 110 s), satisfying the `>= shutdown.max` requirement from the draining section above. SIGKILL arrives at the hard-deadline boundary, so active drains have up to `shutdown.max` seconds to complete before the backstop fires.

Confirm two healthy proxy pods and the PDB:

```bash
kubectl get deploy agentgateway-proxy -n agentgateway-system -o jsonpath='{.spec.replicas}{"\n"}'
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy
kubectl get pdb -n agentgateway-system
kubectl get deploy agentgateway-proxy -n agentgateway-system -o jsonpath='{.spec.template.spec.terminationGracePeriodSeconds}{"\n"}'
```

Expected: replicas `2`, two `Running` pods, a PDB with `MIN AVAILABLE` = `1`, and `terminationGracePeriodSeconds` = `110` (equal to `shutdown.max`, set automatically by the operator).

## Step 2 — Short Completions

We drive a steady stream of short, non-streaming completions and restart the proxy mid-run. With 2 replicas and a PDB, no request should fail.

Create the load namespace and the k6 script:

```bash
kubectl create namespace loadgenerator --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: k6-zdt-short
  namespace: loadgenerator
data:
  short.js: |-
    import http from 'k6/http';
    import { check } from 'k6';
    const BASE = __ENV.GATEWAY_URL;
    export const options = {
      scenarios: { steady: { executor: 'constant-arrival-rate', rate: 50, timeUnit: '1s',
        duration: __ENV.DURATION || '4m', preAllocatedVUs: 20, maxVUs: 100 } },
      thresholds: { http_req_failed: ['rate==0'], checks: ['rate==1.0'] },
    };
    export default function () {
      const res = http.post(`${BASE}/openai`,
        JSON.stringify({ model: 'mock-gpt-4o', messages: [{ role: 'user', content: 'hi' }] }),
        { headers: { 'Content-Type': 'application/json' } });
      check(res, { 'status is 200': r => r.status === 200 });
    }
EOF
```

Start the load test:

```bash
kubectl apply -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-zdt-short
  namespace: loadgenerator
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: k6
        image: grafana/k6:latest
        command: ["k6","run","/scripts/short.js"]
        env:
        - name: GATEWAY_URL
          value: "http://agentgateway-proxy.agentgateway-system.svc.cluster.local:8080"
        - name: DURATION
          value: "4m"
        volumeMounts:
        - name: script
          mountPath: /scripts
      volumes:
      - name: script
        configMap:
          name: k6-zdt-short
EOF
```

About 30 seconds in, trigger the rollout in a second terminal:

```bash
kubectl rollout restart deploy/agentgateway-proxy -n agentgateway-system
kubectl rollout status deploy/agentgateway-proxy -n agentgateway-system --timeout=300s
```

Watch the pods cycle one at a time — the PDB keeps one pod serving throughout:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy -w
```

When the Job finishes, read the k6 summary:

```bash
kubectl logs job/k6-zdt-short -n loadgenerator | grep -E 'http_req_failed|checks|http_reqs'
```

Success criterion: `http_req_failed` is `0.00%` and `checks` is `100.00%`. The rollout happened under load with zero dropped requests.

## Step 3 — Long-Lived Streaming

Short requests finish before draining matters. Streaming responses that last tens of seconds are different: when a pod drains, in-flight streams either finish within `spec.shutdown.max` or are cut at the deadline. We make the mock stream slowly to show this.

Deploy a slow streaming variant of the mock (≈18–58s per stream, up to ~1 minute, depending on response length) and route it on `/openai-slow`:

```bash
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o-slow
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels: { app: mock-gpt-4o-slow }
  template:
    metadata:
      labels: { app: mock-gpt-4o-slow }
    spec:
      containers:
      - name: vllm-sim
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        args: ["--model","mock-gpt-4o","--port","8000","--time-to-first-token","2s","--inter-token-latency","600ms","--max-num-seqs","100"]
        ports: [{ containerPort: 8000, name: http }]
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpt-4o-slow-svc
  namespace: agentgateway-system
spec:
  selector: { app: mock-gpt-4o-slow }
  ports: [{ protocol: TCP, port: 8000, targetPort: 8000, name: http }]
  type: ClusterIP
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mock-openai-slow
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: { model: "mock-gpt-4o" }
      host: mock-gpt-4o-slow-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth: { passthrough: {} }
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai-slow
  namespace: agentgateway-system
spec:
  parentRefs: [{ name: agentgateway-proxy, namespace: agentgateway-system }]
  rules:
  - matches: [{ path: { type: PathPrefix, value: /openai-slow } }]
    backendRefs: [{ name: mock-openai-slow, group: enterpriseagentgateway.solo.io, kind: EnterpriseAgentgatewayBackend }]
    timeouts: { request: "180s" }
EOF
kubectl rollout status deploy/mock-gpt-4o-slow -n agentgateway-system --timeout=120s
```

The streaming k6 script counts streams that **completed** (body contains `finish_reason":"stop`) vs. were **cut**:

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: k6-zdt-stream
  namespace: loadgenerator
data:
  stream.js: |-
    import http from 'k6/http';
    import { check } from 'k6';
    import { Counter } from 'k6/metrics';
    const BASE = __ENV.GATEWAY_URL;
    const completed = new Counter('streams_completed');
    const cut = new Counter('streams_cut');
    export const options = {
      scenarios: { streams: { executor: 'constant-arrival-rate', rate: 1, timeUnit: '1s',
        duration: __ENV.DURATION || '3m', preAllocatedVUs: 10, maxVUs: 60 } },
      thresholds: { 'http_req_failed': ['rate<0.05'] },
    };
    export default function () {
      const res = http.post(`${BASE}/openai-slow`,
        JSON.stringify({ model: 'mock-gpt-4o', stream: true, messages: [{ role: 'user', content: 'tell me a long story' }] }),
        { headers: { 'Content-Type': 'application/json' }, timeout: '120s' });
      const ok = res.status === 200 && String(res.body).includes('finish_reason":"stop');
      if (res.status === 200 && ok) completed.add(1);
      else cut.add(1);
      check(res, { 'connected (200)': r => r.status === 200 });
    }
EOF

kubectl apply -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-zdt-stream
  namespace: loadgenerator
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: k6
        image: grafana/k6:latest
        command: ["k6","run","/scripts/stream.js"]
        env:
        - { name: GATEWAY_URL, value: "http://agentgateway-proxy.agentgateway-system.svc.cluster.local:8080" }
        - { name: DURATION, value: "3m" }
        volumeMounts: [{ name: script, mountPath: /scripts }]
      volumes: [{ name: script, configMap: { name: k6-zdt-stream } }]
EOF
```

~30s in, restart the proxy (`kubectl rollout restart deploy/agentgateway-proxy -n agentgateway-system`). When the Job finishes:

```bash
kubectl logs job/k6-zdt-stream -n loadgenerator | grep -E 'streams_completed|streams_cut|http_req_failed|http_reqs'
```

Observed result (v2026.6.1, `shutdown.max: 110`):
- `streams_completed`: 157, `streams_cut`: 3, `http_req_failed`: 1.87% (3 of 160 requests). A few iterations at the tail of the run did not launch due to VU exhaustion under the arrival-rate executor; 160 is the count of streams that actually ran.
- Single-stream duration: avg ~26s, min 3.2s, max 58.5s (random response length at 600ms/token).
- The 3 cut streams are connection resets (`unexpected EOF`) from the draining pod during the rollout window. The 110s drain window covers most streams: 157 of 160 streams (98%) completed cleanly, and the `rate<0.05` threshold passed.

How to read this:
- The impact is bounded and in-flight-only. All 3 failures were streams already mid-flight on the draining pod when it hit `shutdown.max`; they received a status-0 connection reset, not a failed new-connection attempt. No new-request failures occurred. The `rate<0.05` threshold is a tolerance for these drain-deadline resets, not for new-request failures.
- With stream lifetimes of ≈18–58s and a 110s drain window, most in-flight streams finish before the deadline.
- Raising `spec.shutdown.max` (and `terminationGracePeriodSeconds` with it) lets more in-flight streams finish before the deadline, at the cost of slower pod turnover. Lowering it increases cuts.

## Step 4 — MCP/SSE Sessions

Not everything can be made zero-downtime by adding replicas. A stateful SSE MCP session is pinned to one replica; when that replica is rolled, the session breaks. This section shows both what survives a rollout (discrete stateless MCP POSTs) and what does not (a persistent long-lived SSE MCP stream pinned to one replica).

Deploy the mock MCP server and route (same server used in the MCP load-testing lab):

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
            method = body.get('method', ''); req_id = body.get('id')
            session_id = self.headers.get('mcp-session-id', str(uuid.uuid4()))
            if method == 'initialize':
                self._json(req_id, {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fast-mcp","version":"1.0"}}, session_id)
            elif method == 'notifications/initialized':
                self.send_response(202); self.send_header('Content-Length','0'); self.end_headers()
            elif method == 'tools/list':
                self._json(req_id, {"tools":[{"name":"echo","description":"Echo","inputSchema":{"type":"object","properties":{"message":{"type":"string"}}}}]})
            elif method == 'tools/call':
                msg = body.get('params',{}).get('arguments',{}).get('message','')
                self._json(req_id, {"content":[{"type":"text","text":msg}]})
            else:
                self.send_response(200); self.send_header('Content-Length','0'); self.end_headers()
        def _json(self, req_id, result, session_id=None):
            payload = json.dumps({"jsonrpc":"2.0","id":req_id,"result":result}).encode()
            self.send_response(200); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length', str(len(payload)))
            if session_id: self.send_header('mcp-session-id', session_id)
            self.end_headers(); self.wfile.write(payload)
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
        command: ["python3","/scripts/server.py"]
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
    appProtocol: kgateway.dev/mcp
  type: ClusterIP
---
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
kubectl rollout status deploy/fast-mcp -n agentgateway-system --timeout=120s
```

The `EnterpriseAgentgatewayBackend` for MCP uses `spec.mcp.targets` with namespace and service label selectors, not `static: {host, port}` (the working shape confirmed in the [MCP load-testing lab](../load-testing/mcp-load-testing-k6.md)). The gateway also enforces a proper MCP initialize handshake — a bare `params:{}` returns 400, so send `protocolVersion`, `capabilities`, and `clientInfo` in the initialize request.

Smoke test that `/mcp` initialize returns 200 and an `mcp-session-id` header:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -si -X POST "http://${GATEWAY_IP}:8080/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0"}}}' \
  | head -10
```

Expected: HTTP 200 with `mcp-session-id` header and SSE-format body.

Run k6 with VUs that each hold an MCP session across iterations, then restart the proxy mid-run:

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: k6-zdt-mcp
  namespace: loadgenerator
data:
  mcp.js: |-
    import http from 'k6/http';
    import { check } from 'k6';
    import { Counter } from 'k6/metrics';
    const BASE = __ENV.GATEWAY_URL; const MCP = `${BASE}/mcp`;
    const sessionErrors = new Counter('mcp_session_errors');
    const H = { 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream' };
    export const options = {
      scenarios: { sessions: { executor: 'constant-vus', vus: 20, duration: __ENV.DURATION || '3m' } },
    };
    let sid = null;
    export default function () {
      if (!sid) {
        const init = http.post(MCP, JSON.stringify({ jsonrpc:'2.0', id:1, method:'initialize', params:{ protocolVersion:'2024-11-05', capabilities:{}, clientInfo:{ name:'k6-zdt', version:'1.0' } } }), { headers: H });
        sid = init.headers['Mcp-Session-Id'] || init.headers['mcp-session-id'];
      }
      if (!sid) { sessionErrors.add(1); return; }
      const h = Object.assign({}, H, { 'mcp-session-id': sid });
      const res = http.post(MCP, JSON.stringify({ jsonrpc:'2.0', id:2, method:'tools/call', params:{ name:'echo', arguments:{ message:'hi' } } }), { headers: h });
      const ok = check(res, { 'call 200': r => r.status === 200 });
      if (!ok) { sessionErrors.add(1); sid = null; }  // session lost — re-initialize next iteration
    }
EOF

kubectl apply -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-zdt-mcp
  namespace: loadgenerator
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: k6
        image: grafana/k6:latest
        command: ["k6","run","/scripts/mcp.js"]
        env:
        - name: GATEWAY_URL
          value: "http://agentgateway-proxy.agentgateway-system.svc.cluster.local:8080"
        - name: DURATION
          value: "3m"
        volumeMounts:
        - name: script
          mountPath: /scripts
      volumes:
      - name: script
        configMap:
          name: k6-zdt-mcp
EOF
```

~30s in: `kubectl rollout restart deploy/agentgateway-proxy -n agentgateway-system`. After the Job:

```bash
kubectl logs job/k6-zdt-mcp -n loadgenerator | grep -E 'mcp_session_errors|http_req_failed|checks'
```

Observed result (v2026.6.1):

The discrete-POST MCP session pattern survived the rollout with near-zero errors: 1 `mcp_session_error` out of 378,185 iterations (0.00% failure rate, `http_req_failed: 0.00%`). The gateway's `mcp-session-id` is a stateless token that encodes backend routing information, so the proxy forwards it across replicas and a rolling restart does not break in-flight sessions for this request pattern.

This pattern uses discrete HTTP POSTs: each call is an independent request that carries the session token in a header, so the proxy routes it statelessly and does not pin the VU to a specific replica. A persistent SSE MCP session — a long-lived `GET` stream that holds a connection open while the server pushes events — is pinned to one proxy replica, and rolling that replica closes the stream. Demonstrating that failure mode requires a persistent-SSE MCP client, not the discrete-POST mock used here, which exercises the stateless request layer rather than the persistent connection layer.

The fix is the transport, not more replicas:

- Discrete POST MCP (StreamableHTTP-style): stateless session tokens carry routing information, replicas share no per-connection state, and it survives a rollout as shown above.
- Persistent SSE MCP: a long-lived `GET` stream pinned to one proxy replica cannot survive rolling that replica. Treat it as a maintenance event or migrate to a stateless transport.
- Adding more replicas does not help SSE MCP. It distributes new sessions, but a session already established on a draining replica breaks when that replica exits, regardless of how many healthy replicas exist.

If you are running SSE-backed MCP agents, plan upgrades as a maintenance window, or migrate to a StreamableHTTP transport to gain the zero-downtime behavior this test confirmed.

## Step 5 — Run the Helm Upgrade

`kubectl rollout restart` exercised the exact drain-and-replace path you just measured. A production version upgrade is the same data-plane path, driven by Helm and including the controller:

```bash
helm upgrade enterprise-agentgateway solo/enterprise-agentgateway \
  --namespace agentgateway-system \
  --version <new-version> \
  --reuse-values

kubectl rollout status deployment/enterprise-agentgateway -n agentgateway-system --timeout=300s
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system --timeout=300s
```

Run any of the k6 Jobs above during the upgrade to confirm the same zero-downtime behavior end-to-end.

### What to Monitor

Watch these in the Grafana stack from [lab 002](../../002-set-up-ui-and-monitoring-tools.md) (see the [scaling lab](../observability/production-observability-alerting-and-scaling.md#rolling-upgrades) for the full list):

- `agentgateway_build_info` — shows old and new versions during the rollout, then only the new one.
- `agentgateway_requests_total{status=~"5.."}` — error rate must not spike.
- `agentgateway_xds_connection_terminations` — expect `Reconnect` reasons as proxies restart, **not** `ConnectionError`.

## Interpreting the Results

| Pattern | Result | Why |
|---|---|---|
| Short completions | Zero downtime | 12,000 requests, 0.00% failed, 100% checks through the rollout. Requests finish inside the drain window; new traffic routes to the healthy pod. |
| Long-lived streaming | Zero downtime for new traffic; in-flight bounded by `shutdown.max` | 157 of 160 streams completed; 3 in-flight streams were reset at the drain deadline (status-0). All failures were in-flight-only, with no new-request failures. |
| Stateless/StreamableHTTP MCP (discrete POSTs) | Effectively zero downtime | 1 error in 378,185 iterations (0.00% failed). Session tokens are stateless; the proxy routes them across replicas without pinning. |
| Long-lived persistent SSE MCP | Not zero-downtime | A persistent `GET` stream is pinned to one proxy replica; rolling that replica closes the stream, and adding replicas does not help. Treat upgrades as a maintenance event, or migrate to a stateless transport. (Not demonstrated in this lab — the mock uses discrete POSTs.) |

The minimum recipe for zero-downtime in-place upgrades: two or more replicas, a PDB, and graceful shutdown sized to your longest acceptable in-flight request.

## Cleanup

```bash
kubectl delete job -n loadgenerator --all
kubectl delete configmap -n loadgenerator k6-zdt-short k6-zdt-stream k6-zdt-mcp --ignore-not-found
kubectl delete httproute -n agentgateway-system mock-openai-slow fast-mcp --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mock-openai-slow fast-mcp-backend --ignore-not-found
kubectl delete deploy -n agentgateway-system mock-gpt-4o-slow fast-mcp --ignore-not-found
kubectl delete svc -n agentgateway-system mock-gpt-4o-slow-svc fast-mcp-svc --ignore-not-found
kubectl delete configmap -n agentgateway-system fast-mcp-script --ignore-not-found
```

The 2-replica + PDB + shutdown config from Step 1 is a production best practice; leave it in place.
