# Blue/Green Upgrades Across Namespaces

In this lab you'll run two independent Agentgateway proxies side by side — a live **blue** proxy and a new **green** proxy, each in its own namespace — and shift traffic between them using [route delegation](https://docs.solo.io/agentgateway/latest/traffic-management/route-delegation/) with weighted backend references. You'll cut over from blue to green under continuous traffic with zero dropped requests, then roll back instantly by flipping a weight.

This is the side-by-side alternative to the [In-Place Rolling Upgrades](in-place-rolling-upgrades.md) lab. In-place drains and replaces the pods of one proxy; blue/green stands up a second, independent proxy and moves traffic at a routing tier in front of both — so rollback is a traffic flip, not a redeploy.

> **Simulated version delta:** both proxies run the same pinned version (`v2026.6.1`); we identify them with an `x-gateway-color` response header. In production, "green" is the proxy you install at the new `--version` (or with new config). This lab proves the **cutover path** is zero-downtime; the thing being cut over to is yours to choose.

## Pre-requisites
- [001 — Install Enterprise Agentgateway](../../001-install-enterprise-agentgateway.md)
- [002 — Set Up UI and Monitoring Tools](../../002-set-up-ui-and-monitoring-tools.md)
- [Configure Mock OpenAI Server](../routing/configure-mock-openai-server.md) — provides the shared `mock-gpt-4o-svc` LLM backend reused by both colors
- [In-Place Rolling Upgrades](in-place-rolling-upgrades.md) — the in-place counterpart

## Lab Objectives
- Stand up a second, independent Agentgateway proxy (`green`) alongside the live one (`blue`), each in its own namespace
- Front both with a thin edge proxy and shift traffic with weighted route delegation
- Cut over blue→green under live k6 traffic with zero dropped requests, then roll back instantly

## How blue/green works here

Three `Gateway`s, all programmed by the one shared controller you installed in `001`:

| Tier | What it is | Role |
|---|---|---|
| **Edge** (`bluegreen-edge`, `agentgateway-system`) | A thin proxy with one **parent** `HTTPRoute` | The stable client entrypoint. Its parent route *delegates* `/openai` to the blue and green namespaces with **weights** — this is the cutover control. |
| **Blue** (`agentgateway-blue` namespace) | A full proxy + its own route to the LLM | The currently-live data plane. Stamps `x-gateway-color: blue`. |
| **Green** (`agentgateway-green` namespace) | A full proxy + its own route to the LLM | The new data plane. Stamps `x-gateway-color: green`. |

The request path is **edge proxy → color proxy → shared LLM**. Route delegation lets the blue and green teams own their routing in their own namespaces; the edge merges their delegate routes and weights them.

```
                          client / k6
                               │  POST /openai
                               ▼
   ┌──────────────────────────────────────────────────────┐
   │  EDGE proxy — bluegreen-edge   (ns agentgateway-system)│
   │  parent HTTPRoute  /openai  ·  weighted delegation     │
   └─────────────────┬───────────────────────┬──────────────┘
          weight 100 │                       │ weight 0
                     │   ◀── flip weights: cut over (0/100) · roll back (100/0)
                     ▼                       ▼
   ┌─────────────────────────────┐  ┌──────────────────────────────┐
   │ blue-delegate  HTTPRoute    │  │ green-delegate  HTTPRoute    │
   │ ns agentgateway-blue        │  │ ns agentgateway-green        │
   │ → Service agentgateway-blue │  │ → Service agentgateway-green │
   └──────────────┬──────────────┘  └───────────────┬──────────────┘
                  ▼                                 ▼
   ┌─────────────────────────────┐  ┌──────────────────────────────┐
   │ BLUE proxy                  │  │ GREEN proxy                  │
   │ blue-proxy-route /openai→LLM│  │ green-proxy-route /openai→LLM│
   │ sets x-gateway-color: blue  │  │ sets x-gateway-color: green  │
   └──────────────┬──────────────┘  └───────────────┬──────────────┘
                  └─────────────────┬───────────────┘
                                    ▼
              ┌────────────────────────────────────────────┐
              │ shared mock LLM — mock-gpt-4o-svc : 8000   │
              │ (ns agentgateway-system)                   │
              └────────────────────────────────────────────┘
```


> **Why two routes per color namespace:** route delegation *merges* a child route's backends into the **edge** proxy. To make the edge forward to a separate color *proxy* (rather than straight to the LLM), each color has a small **delegate** route (`blue-delegate` / `green-delegate`) that forwards to its proxy `Service`, plus the color proxy's **own** route (`blue-proxy-route` / `green-proxy-route`) that forwards to the LLM and stamps the color header. The edge delegates to the delegate route **by name**, so it never merges the proxy's LLM route.

Only the **gateway** is blue/green — both colors call the same shared mock LLM (`mock-gpt-4o-svc`). You don't blue/green the model.

## Step 1 — Stand up the edge proxy

The edge is a thin, stable proxy that clients hit. It holds the parent route that decides how much traffic goes to blue vs. green. Create its parameters and `Gateway`:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: edge-config
  namespace: agentgateway-system
spec:
  deployment:
    spec:
      replicas: 1
  service:
    spec:
      type: LoadBalancer
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: bluegreen-edge
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  infrastructure:
    parametersRef:
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayParameters
      name: edge-config
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
EOF
```

Wait for it to program and capture its address:

```bash
kubectl wait --for=condition=Programmed gateway/bluegreen-edge -n agentgateway-system --timeout=120s
export EDGE_IP=$(kubectl get svc bluegreen-edge -n agentgateway-system \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}')
echo "Edge: ${EDGE_IP}:8080"
```

Expected: `condition met`, and `EDGE_IP` is a non-empty LoadBalancer address. The edge has no routes yet — you add them in Step 3.

## Step 2 — Stand up the blue and green proxies

Each color is a full, independent proxy in its own namespace, with its own route to the **shared** mock LLM. The route stamps an `x-gateway-color` header so we can see which proxy served a request.

```bash
kubectl create namespace agentgateway-blue --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace agentgateway-green --dry-run=client -o yaml | kubectl apply -f -

for color in blue green; do
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: ${color}-config
  namespace: agentgateway-${color}
spec:
  deployment:
    spec:
      replicas: 1
  service:
    spec:
      type: LoadBalancer
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-${color}
  namespace: agentgateway-${color}
spec:
  gatewayClassName: enterprise-agentgateway
  infrastructure:
    parametersRef:
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayParameters
      name: ${color}-config
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: ${color}-llm
  namespace: agentgateway-${color}
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-4o"
      host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth: { passthrough: {} }
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: ${color}-proxy-route
  namespace: agentgateway-${color}
spec:
  parentRefs:
    - name: agentgateway-${color}
      namespace: agentgateway-${color}
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      filters:
        - type: ResponseHeaderModifier
          responseHeaderModifier:
            set:
              - name: x-gateway-color
                value: ${color}
      backendRefs:
        - name: ${color}-llm
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
EOF
done

kubectl wait --for=condition=Programmed gateway/agentgateway-blue -n agentgateway-blue --timeout=120s
kubectl wait --for=condition=Programmed gateway/agentgateway-green -n agentgateway-green --timeout=120s
```

Confirm each proxy serves the LLM directly and stamps its color (hitting the color proxy's own LoadBalancer IP, before the edge is involved):

```bash
for color in blue green; do
  ip=$(kubectl get svc agentgateway-${color} -n agentgateway-${color} \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}')
  echo "== ${color} @ ${ip} =="
  curl -s -D - -o /dev/null -X POST "http://${ip}:8080/openai" \
    -H 'Content-Type: application/json' \
    -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"hi"}]}' \
    | grep -iE 'HTTP/|x-gateway-color'
done
```

Expected: each prints `HTTP/1.1 200` and `x-gateway-color: <color>`. Both proxies are now live and independent — neither is fronted yet.

## Step 3 — Wire delegation and the weighted parent route

Give each color a small **delegate** route that forwards to its proxy `Service`, then attach a **parent** route to the edge that delegates `/openai` to those two delegates — starting at **100% blue, 0% green**:

```bash
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: blue-delegate
  namespace: agentgateway-blue
spec:
  rules:
    - matches:
        - path: { type: PathPrefix, value: /openai }
      backendRefs:
        - name: agentgateway-blue
          kind: Service
          port: 8080
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: green-delegate
  namespace: agentgateway-green
spec:
  rules:
    - matches:
        - path: { type: PathPrefix, value: /openai }
      backendRefs:
        - name: agentgateway-green
          kind: Service
          port: 8080
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bluegreen-parent
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: bluegreen-edge
      namespace: agentgateway-system
  rules:
    - matches:
        - path: { type: PathPrefix, value: /openai }
      backendRefs:
        - group: gateway.networking.k8s.io
          kind: HTTPRoute
          name: blue-delegate
          namespace: agentgateway-blue
          weight: 100
        - group: gateway.networking.k8s.io
          kind: HTTPRoute
          name: green-delegate
          namespace: agentgateway-green
          weight: 0
EOF
```

The edge delegates **by name** to `blue-delegate` / `green-delegate` (not `"*"`), so it forwards to each color *proxy* and never merges the proxies' own LLM routes. Note the delegate routes intentionally have **no `parentRefs`** — a delegation child is attached by the parent's `backendRef`, not by binding itself to a Gateway. Confirm the parent resolved, then smoke-test through the edge — all traffic should be blue:

```bash
kubectl get httproute bluegreen-parent -n agentgateway-system \
  -o jsonpath='{range .status.parents[*]}{.conditions[?(@.type=="ResolvedRefs")].status}{"\n"}{end}'

export EDGE_IP=$(kubectl get svc bluegreen-edge -n agentgateway-system \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}')
curl -s -D - -o /dev/null -X POST "http://${EDGE_IP}:8080/openai" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"hi"}]}' \
  | grep -iE 'HTTP/|x-gateway-color'
```

Expected: `ResolvedRefs` is `True`, and the edge request returns `HTTP/1.1 200` with `x-gateway-color: blue`. Traffic is flowing client → edge → blue proxy → LLM.

## Step 4 — Validate the cutover (and rollback) under load

Now prove the cutover is zero-downtime. k6 drives a steady stream of short completions at the **edge**, reads the `x-gateway-color` header on each response, and counts blue vs. green. We flip the parent weights mid-run, then flip them back — no request should fail through either flip.

Create the load namespace and the k6 script:

```bash
kubectl create namespace loadgenerator --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: k6-zdt-bluegreen
  namespace: loadgenerator
data:
  bluegreen.js: |-
    import http from 'k6/http';
    import { check } from 'k6';
    import { Counter } from 'k6/metrics';
    const BASE = __ENV.GATEWAY_URL;
    const blue = new Counter('blue_responses');
    const green = new Counter('green_responses');
    export const options = {
      scenarios: { steady: { executor: 'constant-arrival-rate', rate: 50, timeUnit: '1s',
        duration: __ENV.DURATION || '3m', preAllocatedVUs: 20, maxVUs: 100 } },
      thresholds: { http_req_failed: ['rate==0'], checks: ['rate==1.0'] },
    };
    export default function () {
      const res = http.post(`${BASE}/openai`,
        JSON.stringify({ model: 'mock-gpt-4o', messages: [{ role: 'user', content: 'hi' }] }),
        { headers: { 'Content-Type': 'application/json' } });
      const color = res.headers['X-Gateway-Color'] || res.headers['x-gateway-color'] || '';
      if (color === 'blue') blue.add(1);
      else if (color === 'green') green.add(1);
      check(res, { 'status is 200': r => r.status === 200 });
    }
EOF

kubectl apply -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-zdt-bluegreen
  namespace: loadgenerator
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: k6
        image: grafana/k6:0.54.0
        command: ["k6","run","/scripts/bluegreen.js"]
        env:
        - name: GATEWAY_URL
          value: "http://bluegreen-edge.agentgateway-system.svc.cluster.local:8080"
        - name: DURATION
          value: "3m"
        volumeMounts:
        - { name: script, mountPath: /scripts }
      volumes:
      - { name: script, configMap: { name: k6-zdt-bluegreen } }
EOF
```

In a second terminal, watch the color flip live as you change weights:

```bash
export EDGE_IP=$(kubectl get svc bluegreen-edge -n agentgateway-system \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}')
while true; do curl -s -D - -o /dev/null -X POST "http://${EDGE_IP}:8080/openai" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"hi"}]}' \
  | grep -i '^x-gateway-color:'; sleep 1; done
```

~30s into the run, **cut over to green** (flip the weights):

```bash
kubectl patch httproute bluegreen-parent -n agentgateway-system --type=json \
  -p='[{"op":"replace","path":"/spec/rules/0/backendRefs/0/weight","value":0},{"op":"replace","path":"/spec/rules/0/backendRefs/1/weight","value":100}]'
```

The curl loop flips to `x-gateway-color: green`. ~30s later, **roll back to blue**:

```bash
kubectl patch httproute bluegreen-parent -n agentgateway-system --type=json \
  -p='[{"op":"replace","path":"/spec/rules/0/backendRefs/0/weight","value":100},{"op":"replace","path":"/spec/rules/0/backendRefs/1/weight","value":0}]'
```

When the Job finishes, read the summary:

```bash
kubectl logs job/k6-zdt-bluegreen -n loadgenerator | grep -E 'http_req_failed|checks|http_reqs|blue_responses|green_responses'
```

**Success criteria:** `http_req_failed` is `0.00%` and `checks` is `100.00%` — zero dropped requests across **both** the cutover and the rollback — and both `blue_responses` and `green_responses` are non-zero, proving traffic actually moved between the two proxies. Stop the curl loop with `Ctrl-C`.

**Observed result (v2026.6.1):** 9,001 requests, 0.00% failed, 100% checks across both the cutover and the rollback; blue_responses 7,495 / green_responses 1,506 — traffic moved blue→green→blue with zero dropped requests.

## Interpreting the results

| What | Result | Why |
|---|---|---|
| Cutover blue→green | **Zero downtime** | Weights are applied per request at the edge; flipping `100/0`→`0/100` routes new requests to the green proxy. A request already dispatched completes on its proxy. |
| Rollback green→blue | **Instant, zero downtime** | Rollback is the same weight flip in reverse — no redeploy, no drain. Blue was never torn down, so it resumes immediately. |
| Traffic moved | `blue_responses` and `green_responses` both > 0 | The `x-gateway-color` header proves requests were served by two different proxies across the run. |

The blue/green advantage over in-place: **green is validated as a real, running proxy before it takes traffic, and rollback is a traffic flip rather than a second rollout.** The cost is running two data planes at once.

Both this lab and in-place keep the upgrade within a single cluster. To move the boundary up a level — taking a whole cluster out of service while a peer cluster serves the same global LLM — see [Multi-Cluster Upgrades](multi-cluster-upgrades.md).

> **Sessions across a cutover:** weights are evaluated per request, so stateless completions cut over cleanly. A sticky or long-lived SSE session that is pinned to one proxy will treat a cutover as a session boundary unless you add session persistence (consistent hashing / `sessionPersistence`). For long-lived MCP/SSE specifics, see the [In-Place Rolling Upgrades](in-place-rolling-upgrades.md) lab's MCP section — the same single-replica session caveat applies.

> **In production**, green is the proxy you install at the new `--version` (or with new config/policies). The mechanism is identical: stand green up, validate it, shift weight, and keep blue as instant rollback until you're confident.

## Cleanup

```bash
kubectl delete job k6-zdt-bluegreen -n loadgenerator --ignore-not-found
kubectl delete configmap k6-zdt-bluegreen -n loadgenerator --ignore-not-found
kubectl delete httproute bluegreen-parent -n agentgateway-system --ignore-not-found
kubectl delete namespace agentgateway-blue agentgateway-green --ignore-not-found
kubectl delete gateway bluegreen-edge -n agentgateway-system --ignore-not-found
kubectl delete enterpriseagentgatewayparameters edge-config -n agentgateway-system --ignore-not-found
```

Deleting the two color namespaces removes their Gateways, routes, and backends. The base install (`agentgateway-proxy`) and the shared mock LLM are untouched.
