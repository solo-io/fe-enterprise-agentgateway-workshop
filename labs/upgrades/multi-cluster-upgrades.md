# Multi-Cluster Upgrades

In this lab you'll upgrade Enterprise Agentgateway in one cluster while a **peer cluster keeps serving the same LLM** — with no loss of service for traffic that has somewhere healthy to go. Two clusters run a Solo Enterprise for Istio **ambient multicluster** mesh; the mock LLM is published as a **global service** (`*.mesh.internal`) backed by both clusters; and an Enterprise Agentgateway **ambient ingress** in each cluster routes to that global hostname. Draining a cluster's backend fails the global service over to the peer across the east-west gateway, so you can take a whole cluster down to upgrade it and the global service stays up.

This is the cross-cluster counterpart to the other two upgrade labs:

| Lab | Scope | Drain primitive | Rollback |
|---|---|---|---|
| [In-Place Rolling Upgrades](in-place-rolling-upgrades.md) | One proxy, one cluster | Pod drain (`shutdown.min/max`) | Redeploy |
| [Blue/Green Across Namespaces](blue-green-namespaces.md) | Two proxies, one cluster | Weighted route delegation | Flip weights |
| **Multi-Cluster Upgrades** (this lab) | Two clusters | Scale local backend to 0 → mesh failover to peer | Restore the cluster |

The per-cluster zero-downtime mechanism is the in-place recipe (≥2 replicas + PDB + graceful shutdown); the multi-cluster-specific part is **taking a whole cluster out of service while the global service continues from the peer.**

## Pre-requisites
- [001 — Install Enterprise Agentgateway](../../001-install-enterprise-agentgateway.md) and [002 — Set Up UI and Monitoring Tools](../../002-set-up-ui-and-monitoring-tools.md) for background; this lab installs its own two-cluster stack from scratch.
- [In-Place Rolling Upgrades](in-place-rolling-upgrades.md) — the per-cluster zero-downtime posture reused here.
- **Two clusters.** Locally: `vind-up-2` brings up `cluster1` (region `us-west`) and `cluster2` (region `us-east`) on a shared Docker network with LoadBalancer support.
- **Licenses:** an Enterprise-level **Solo Enterprise for Istio** license (ambient) and a **Solo Enterprise for agentgateway** license. Both are provided here via `SOLO_TRIAL_LICENSE_KEY`.
- **Solo distribution of Istio 1.30 or later** — the agentgateway ambient-ingress integration requires ≥1.30.

## Lab Objectives
- Stand up a Solo ambient multicluster mesh across two clusters and publish the LLM as a global service.
- Run Enterprise Agentgateway as the ambient ingress in each cluster, routing to the global `*.mesh.internal` hostname.
- Prove cross-cluster failover: scale a cluster's local LLM to 0 and watch its ingress serve from the peer cluster.
- Upgrade agentgateway in each cluster under continuous load and measure the result.

## Architecture

```
            external DNS / GSLB  (latency + health based)       ← client entry tier
               /                                  \                 (external; described, see end)
   cluster1  (us-west)                       cluster2  (us-east)
   agentgateway-ingress                      agentgateway-ingress
   (enterprise-agentgateway class,           (same)
    istio.autoEnabled → HBONE into mesh)
        \                                          /
         →   mock-gpt-4o-svc.llm.mesh.internal   (GLOBAL service)   ← ambient mesh tier
             backed by mock-LLM pods in BOTH clusters,                (validated in this lab)
             peered via istio-eastwest gateways; if the local
             backend has no endpoints, traffic fails over to
             the peer cluster over mutually-authenticated HBONE.
```

Two independent high-availability tiers:

| Tier | Mechanism | How you drain a cluster |
|---|---|---|
| **Backend (LLM)** | Global service `solo.io/service-scope=global` → `*.mesh.internal`, mesh failover | **Scale the local backend to 0.** North-south failover triggers when the local service has no endpoints. |
| **Ingress (agentgateway)** | One agentgateway ingress per cluster + an external GSLB | Remove the cluster's ingress from DNS rotation (external tier, described at the end). |

## Set cluster contexts

```bash
export KUBECONTEXT_CLUSTER1=cluster1
export KUBECONTEXT_CLUSTER2=cluster2
export MESH_NAME_CLUSTER1=cluster1
export MESH_NAME_CLUSTER2=cluster2
export ISTIO_VERSION=1.30.0
export ENTERPRISE_AGW_VERSION=v2026.7.0
: "${SOLO_TRIAL_LICENSE_KEY:?export your Solo license key first}"
```

Each cluster's istiod is configured with its own cluster name, network, and trust domain (`cluster1` / `cluster2`), so the two clusters can be told apart and traffic can cross between them.

## Step 1 — Install the Solo ambient multicluster mesh

This step mirrors the [Solo Enterprise for Istio ambient multicluster](https://docs.solo.io/istio/latest/ambient/multicluster/install/default/) install. Both clusters share one root of trust, run the ambient data plane (istiod + istio-cni + ztunnel), and are peered with east-west gateways.

### Topology labels

`vind` labels the API node but not the worker; label every node so locality routing works:

```bash
kubectl --context $KUBECONTEXT_CLUSTER1 label nodes --all \
  topology.kubernetes.io/region=us-west topology.kubernetes.io/zone=us-west-1 --overwrite
kubectl --context $KUBECONTEXT_CLUSTER2 label nodes --all \
  topology.kubernetes.io/region=us-east topology.kubernetes.io/zone=us-east-1 --overwrite
```

### Solo istioctl

```bash
OS=$(uname | tr '[:upper:]' '[:lower:]' | sed -E 's/darwin/osx/')
ARCH=$(uname -m | sed -E 's/aarch/arm/; s/x86_64/amd64/; s/armv7l/armv7/')
curl -sSL "https://storage.googleapis.com/soloio-istio-binaries/release/${ISTIO_VERSION}-solo/istioctl-${ISTIO_VERSION}-solo-${OS}-${ARCH}.tar.gz" | tar xzf - -C .
mv ./istioctl ./solo-istioctl && chmod +x ./solo-istioctl
```

### Shared root of trust

Both clusters must share one root CA so workloads can verify each other's certificates across the cluster boundary.

```bash
WORK_DIR=$(mktemp -d)
cat > "$WORK_DIR/root-openssl.cnf" <<'CNFEOF'
[ req ]
prompt = no
distinguished_name = dn
x509_extensions = v3_ca
[ dn ]
C  = US
ST = California
L  = San Francisco
O  = MyOrg
OU = MyUnit
CN = root-cert
[ v3_ca ]
basicConstraints = critical, CA:TRUE, pathlen:1
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
CNFEOF
cat > "$WORK_DIR/intermediate-req.cnf" <<'CNFEOF'
[ req ]
prompt = no
distinguished_name = dn
[ dn ]
C  = US
ST = California
L  = San Francisco
O  = MyOrg
OU = MyUnit
CN = istio-intermediate-ca
CNFEOF
cat > "$WORK_DIR/ca-ext.cnf" <<'CNFEOF'
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
CNFEOF
openssl req -x509 -sha256 -nodes -days 3650 -newkey rsa:2048 \
  -keyout "$WORK_DIR/root-key.pem" -out "$WORK_DIR/root-cert.pem" \
  -config "$WORK_DIR/root-openssl.cnf" -extensions v3_ca
openssl req -new -nodes -newkey rsa:2048 \
  -keyout "$WORK_DIR/ca-key.pem" -out "$WORK_DIR/ca.csr" \
  -config "$WORK_DIR/intermediate-req.cnf"
openssl x509 -req -sha256 -days 3650 -in "$WORK_DIR/ca.csr" \
  -CA "$WORK_DIR/root-cert.pem" -CAkey "$WORK_DIR/root-key.pem" -CAcreateserial \
  -out "$WORK_DIR/ca-cert.pem" -extfile "$WORK_DIR/ca-ext.cnf" -extensions v3_ca
cat "$WORK_DIR/ca-cert.pem" "$WORK_DIR/root-cert.pem" > "$WORK_DIR/cert-chain.pem"
cat <<EOF > /tmp/cacerts.yaml
apiVersion: v1
kind: Secret
metadata:
  name: cacerts
  namespace: istio-system
type: Opaque
data:
  ca-cert.pem: $(base64 < "$WORK_DIR/ca-cert.pem" | tr -d '\n')
  ca-key.pem: $(base64 < "$WORK_DIR/ca-key.pem" | tr -d '\n')
  cert-chain.pem: $(base64 < "$WORK_DIR/cert-chain.pem" | tr -d '\n')
  root-cert.pem: $(base64 < "$WORK_DIR/root-cert.pem" | tr -d '\n')
EOF
rm -rf "$WORK_DIR"

for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
  kubectl --context $c create namespace istio-system --dry-run=client -o yaml | kubectl --context $c apply -f -
  kubectl --context $c apply -f /tmp/cacerts.yaml
done
```

### Install ambient on each cluster

Run this block once per cluster. For `cluster1` use `C=$KUBECONTEXT_CLUSTER1; NET=$MESH_NAME_CLUSTER1`; then repeat with `C=$KUBECONTEXT_CLUSTER2; NET=$MESH_NAME_CLUSTER2`.

```bash
C=$KUBECONTEXT_CLUSTER1; NET=$MESH_NAME_CLUSTER1   # repeat with CLUSTER2 values

helm upgrade --kube-context $C --install istio-base \
  oci://us-docker.pkg.dev/soloio-img/istio-helm/base -n istio-system \
  --version $ISTIO_VERSION-solo --create-namespace
kubectl label namespace istio-system topology.istio.io/network=$NET --context $C --overwrite
kubectl get crd gateways.gateway.networking.k8s.io --context $C &>/dev/null || \
  kubectl --context $C apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml

helm upgrade --kube-context $C --install istio-cni \
  oci://us-docker.pkg.dev/soloio-img/istio-helm/cni -n istio-system --version=$ISTIO_VERSION-solo -f -<<EOF
profile: ambient
ambient: { dnsCapture: true }
excludeNamespaces: [istio-system, kube-system]
global: { hub: us-docker.pkg.dev/soloio-img/istio, tag: $ISTIO_VERSION-solo, variant: distroless }
EOF
kubectl rollout status ds/istio-cni-node -n istio-system --watch --timeout=180s --context $C

helm upgrade --kube-context $C --install istiod \
  oci://us-docker.pkg.dev/soloio-img/istio-helm/istiod -n istio-system --version=$ISTIO_VERSION-solo -f -<<EOF
profile: ambient
global:
  hub: us-docker.pkg.dev/soloio-img/istio
  tag: $ISTIO_VERSION-solo
  variant: distroless
  multiCluster: { clusterName: $NET }
  network: $NET
meshConfig: { trustDomain: $NET.local }
env:
  PILOT_ENABLE_IP_AUTOALLOCATE: "true"
  PILOT_ENABLE_K8S_SELECT_WORKLOAD_ENTRIES: "false"
  PILOT_SKIP_VALIDATE_TRUST_DOMAIN: "true"
platforms: { peering: { enabled: true } }
license: { value: $SOLO_TRIAL_LICENSE_KEY }
EOF
kubectl rollout status deploy/istiod -n istio-system --watch --timeout=180s --context $C

helm upgrade --kube-context $C --install ztunnel \
  oci://us-docker.pkg.dev/soloio-img/istio-helm/ztunnel -n istio-system --version=$ISTIO_VERSION-solo -f -<<EOF
profile: ambient
logLevel: info
global: { hub: us-docker.pkg.dev/soloio-img/istio, tag: $ISTIO_VERSION-solo, variant: distroless }
istioNamespace: istio-system
env: { L7_ENABLED: "true", SKIP_VALIDATE_TRUST_DOMAIN: "true" }
network: $NET
multiCluster: { clusterName: $NET }
EOF
kubectl rollout status ds/ztunnel -n istio-system --watch --timeout=180s --context $C
```

### Peer the clusters

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
  kubectl --context $c create ns istio-gateways --dry-run=client -o yaml | kubectl --context $c apply -f -
  ./solo-istioctl multicluster expose --namespace istio-gateways --context $c
done

# Wait until each east-west gateway has a LoadBalancer address before linking
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
  for d in $(kubectl --context $c get deploy -n istio-gateways -o jsonpath='{.items[*].metadata.name}'); do
    kubectl --context $c rollout status deploy/"$d" -n istio-gateways --watch --timeout=180s; done
  kubectl --context $c get svc -n istio-gateways -o wide
done

./solo-istioctl multicluster link \
  --contexts=$KUBECONTEXT_CLUSTER1,$KUBECONTEXT_CLUSTER2 --namespace istio-gateways
```

Confirm the peering — each cluster gets an `istio-remote-peer-*` Gateway pointing at the other's east-west address:

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do echo "== $c =="; \
  kubectl --context $c -n istio-gateways get gateway; done
```

Expected: on each cluster, `istio-eastwest` and an `istio-remote-peer-<other>` Gateway, both `PROGRAMMED=True`.

## Step 2 — Publish the mock LLM as a global service

Deploy the mock LLM in an ambient-enrolled namespace in **both** clusters, then mark the service global. The `solo.io/service-scope=global` label makes Istio generate a `ServiceEntry` publishing `mock-gpt-4o-svc.llm.mesh.internal`; `PreferNetwork` keeps traffic local while a local endpoint exists.

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
kubectl --context $c create ns llm --dry-run=client -o yaml | kubectl --context $c apply -f -
kubectl --context $c label ns llm istio.io/dataplane-mode=ambient --overwrite
kubectl --context $c apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata: { name: mock-gpt-4o, namespace: llm }
spec:
  replicas: 1
  selector: { matchLabels: { app: mock-gpt-4o } }
  template:
    metadata: { labels: { app: mock-gpt-4o } }
    spec:
      containers:
      - name: vllm-sim
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        args: ["--model","mock-gpt-4o","--port","8000","--max-num-seqs","100"]
        ports: [{ containerPort: 8000, name: http }]
---
apiVersion: v1
kind: Service
metadata: { name: mock-gpt-4o-svc, namespace: llm }
spec:
  selector: { app: mock-gpt-4o }
  ports: [{ protocol: TCP, port: 8000, targetPort: 8000, name: http }]
  type: ClusterIP
EOF
kubectl --context $c -n llm rollout status deploy/mock-gpt-4o --timeout=180s
kubectl --context $c -n llm label service mock-gpt-4o-svc solo.io/service-scope=global --overwrite
kubectl --context $c -n llm annotate service mock-gpt-4o-svc networking.istio.io/traffic-distribution=PreferNetwork --overwrite
done
```

Confirm the global hostname is published in both clusters:

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do echo "== $c =="; \
  kubectl --context $c get serviceentry -A | grep mock-gpt-4o-svc.llm.mesh.internal; done
```

Expected: each cluster shows an autogenerated `ServiceEntry` for `mock-gpt-4o-svc.llm.mesh.internal`.

## Step 3 — Install Enterprise Agentgateway as the ambient ingress

Install agentgateway in each cluster with the Istio integration enabled. Two settings are required in a multicluster mesh:

- `istio.autoEnabled=true` — runs the ingress pod outside the ambient data plane so it gets its own Istio certificate and opens HBONE connections into the mesh.
- `istio.clusterId` and `istio.network` — must match this cluster's istiod `clusterName`/`network` (`cluster1` / `cluster2`). Without them the ingress sends the default cluster ID and istiod rejects its certificate request.

> **`istio.clusterId`/`istio.network` must equal the mesh/network name, not the kube-context name.** This loop reuses `$c` for the context, the cluster ID, *and* the network only because the contexts are deliberately named `cluster1` / `cluster2` to match `MESH_NAME_CLUSTER*`. If your contexts are named differently (e.g. `kind-cluster1`), set these flags to the istiod `clusterName`/`network` values (`cluster1` / `cluster2`) explicitly — otherwise istiod rejects the ingress's certificate request.

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
helm upgrade -i --kube-context $c --create-namespace --namespace agentgateway-system \
  --version $ENTERPRISE_AGW_VERSION enterprise-agentgateway-crds \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds
helm upgrade -i --kube-context $c -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
  --set istio.autoEnabled=true \
  --set istio.clusterId=$c \
  --set istio.network=$c
kubectl --context $c -n agentgateway-system rollout status deploy/enterprise-agentgateway --timeout=180s
done
```

## Step 4 — Deploy the ingress and route to the global hostname

Create an `agentgateway-ingress` Gateway in each cluster, and an `HTTPRoute` to the global hostname. The route uses an **Istio `Hostname` backend** (`group: networking.istio.io`) and is placed in the **`llm` namespace** — the namespace named in `mock-gpt-4o-svc.llm.mesh.internal`. (agentgateway resolves a global-hostname backend from that namespace; a route for `*.<ns>.mesh.internal` must live in `<ns>`.) A `URLRewrite` maps `/openai` to the model's `/v1/chat/completions` path, and a response header stamps which cluster's ingress served the request.

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
kubectl --context $c apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-ingress
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  listeners:
  - name: http
    port: 8080
    protocol: HTTP
    allowedRoutes: { namespaces: { from: All } }
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcu-llm
  namespace: llm
spec:
  parentRefs:
  - name: agentgateway-ingress
    namespace: agentgateway-system
  rules:
  - matches:
    - path: { type: PathPrefix, value: /openai }
    filters:
    - type: URLRewrite
      urlRewrite:
        path: { type: ReplacePrefixMatch, replacePrefixMatch: /v1/chat/completions }
    - type: ResponseHeaderModifier
      responseHeaderModifier:
        set:
        - { name: x-cluster, value: $c }
    backendRefs:
    - group: networking.istio.io
      kind: Hostname
      name: mock-gpt-4o-svc.llm.mesh.internal
      port: 8000
    timeouts: { request: "120s" }
EOF
kubectl --context $c wait --for=condition=Programmed gateway/agentgateway-ingress -n agentgateway-system --timeout=120s
done
```

Capture each ingress address and smoke-test both:

```bash
GW1=$(kubectl --context $KUBECONTEXT_CLUSTER1 get svc agentgateway-ingress -n agentgateway-system -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}')
GW2=$(kubectl --context $KUBECONTEXT_CLUSTER2 get svc agentgateway-ingress -n agentgateway-system -o jsonpath='{.status.loadBalancer.ingress[0].ip}{.status.loadBalancer.ingress[0].hostname}')
echo "GW1=$GW1  GW2=$GW2"

for gw in "$GW1" "$GW2"; do echo "== $gw =="
  for attempt in $(seq 1 10); do
    out=$(curl -s -m 5 -D - -o /dev/null -X POST "http://${gw}:8080/openai" \
      -H 'Content-Type: application/json' \
      -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"hi"}]}')
    if echo "$out" | grep -qi 'HTTP/1.1 200'; then
      echo "$out" | grep -iE 'HTTP/|x-cluster'; break
    fi
    sleep 3
  done
done
```

Expected: each ingress returns `HTTP/1.1 200` with `x-cluster: cluster1` / `cluster2`. Each is serving from its **local** LLM (`PreferNetwork`).

> **First request may need a moment.** `gateway/agentgateway-ingress` reports `Programmed=True` before its route to the global hostname is fully warm, so the very first `curl` can return an empty response. The retry loop above absorbs that — a single one-shot `curl` piped to `grep` would silently print a blank line instead.

## Step 5 — Cross-cluster failover

This is the heart of the lab: when a cluster's local LLM has no endpoints, its ingress serves the global hostname from the **peer** cluster over the east-west gateway.

Tail ztunnel on cluster2 in a second terminal to watch cross-cluster traffic arrive:

```bash
kubectl --context cluster2 -n istio-system logs -l app=ztunnel -f --prefix | grep -i mock-gpt-4o
```

Drain cluster1's local LLM, then hit cluster1's ingress:

```bash
kubectl --context $KUBECONTEXT_CLUSTER1 -n llm scale deploy/mock-gpt-4o --replicas 0
kubectl --context $KUBECONTEXT_CLUSTER1 -n llm wait --for=delete pod -l app=mock-gpt-4o --timeout=60s

for i in $(seq 1 8); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "http://${GW1}:8080/openai" \
    -H 'Content-Type: application/json' \
    -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"hi"}]}'
done
```

Expected: all `200`. With cluster1's local LLM gone, the only way to a `200` is cross-cluster failover. The cluster2 ztunnel tail confirms it — inbound requests from `spiffe://cluster1.local/.../agentgateway-ingress` to `mock-gpt-4o-svc.llm.mesh.internal`, served by cluster2's LLM (`spiffe://cluster2.local/ns/llm/sa/default`).

**Observed result (v2026.6.1):** 8/8 requests returned `200`; cluster2 ztunnel logged the inbound cross-cluster requests, confirming cluster1's ingress reached cluster2's LLM over mutually-authenticated HBONE across the two trust domains.

Restore cluster1's LLM before continuing (wait for it to be Ready — local serving resumes only once the endpoint is repopulated):

```bash
kubectl --context $KUBECONTEXT_CLUSTER1 -n llm scale deploy/mock-gpt-4o --replicas 1
kubectl --context $KUBECONTEXT_CLUSTER1 -n llm rollout status deploy/mock-gpt-4o --timeout=120s
```

## Step 6 — Upgrade each cluster under load

Apply the in-place zero-downtime posture to both ingresses (≥2 replicas + PDB + graceful shutdown), bound to the Gateway:

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
kubectl --context $c apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata: { name: agentgateway-ingress, namespace: agentgateway-system }
spec:
  deployment: { spec: { replicas: 2 } }
  podDisruptionBudget: { spec: { minAvailable: 1 } }
  shutdown: { min: 15, max: 60 }
EOF
kubectl --context $c patch gateway agentgateway-ingress -n agentgateway-system --type=merge \
  -p '{"spec":{"infrastructure":{"parametersRef":{"group":"enterpriseagentgateway.solo.io","kind":"EnterpriseAgentgatewayParameters","name":"agentgateway-ingress"}}}}'
kubectl --context $c -n agentgateway-system rollout status deploy/agentgateway-ingress --timeout=180s
done
```

Drive continuous traffic at **both** ingresses with k6, splitting requests 50/50:

```bash
cat > mcu.js <<'EOF'
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
const GW1 = __ENV.GW1; const GW2 = __ENV.GW2;
const c1 = new Counter('cluster1_ingress'); const c2 = new Counter('cluster2_ingress');
export const options = {
  scenarios: { steady: { executor: 'constant-arrival-rate', rate: 40, timeUnit: '1s',
    duration: __ENV.DURATION || '6m', preAllocatedVUs: 30, maxVUs: 150 } },
  thresholds: { http_req_failed: ['rate<0.02'], checks: ['rate>0.98'] },
};
export default function () {
  const base = (Math.random() < 0.5) ? GW1 : GW2;
  const res = http.post(`http://${base}:8080/openai`,
    JSON.stringify({ model: 'mock-gpt-4o', messages: [{ role: 'user', content: 'hi' }] }),
    { headers: { 'Content-Type': 'application/json' }, timeout: '30s' });
  const cl = res.headers['X-Cluster'] || res.headers['x-cluster'] || '';
  if (cl === 'cluster1') c1.add(1); else if (cl === 'cluster2') c2.add(1);
  check(res, { 'status 200': r => r.status === 200 });
}
EOF
GW1=$GW1 GW2=$GW2 DURATION=6m k6 run mcu.js
```

While k6 runs, upgrade each cluster in turn. For a cluster: drain its backend (so the global service serves from the peer), roll its agentgateway ingress, then restore the backend. A production version upgrade is `helm upgrade ... --version <new>` with the same `istio.*` flags; `rollout restart` exercises the identical drain-and-replace path.

```bash
# cluster1
kubectl --context $KUBECONTEXT_CLUSTER1 -n llm scale deploy/mock-gpt-4o --replicas 0
kubectl --context $KUBECONTEXT_CLUSTER1 -n agentgateway-system rollout restart deploy/agentgateway-ingress
kubectl --context $KUBECONTEXT_CLUSTER1 -n agentgateway-system rollout status deploy/agentgateway-ingress --timeout=180s
kubectl --context $KUBECONTEXT_CLUSTER1 -n llm scale deploy/mock-gpt-4o --replicas 1
kubectl --context $KUBECONTEXT_CLUSTER1 -n llm rollout status deploy/mock-gpt-4o --timeout=120s

# cluster2 (repeat with CLUSTER2 context)
kubectl --context $KUBECONTEXT_CLUSTER2 -n llm scale deploy/mock-gpt-4o --replicas 0
kubectl --context $KUBECONTEXT_CLUSTER2 -n agentgateway-system rollout restart deploy/agentgateway-ingress
kubectl --context $KUBECONTEXT_CLUSTER2 -n agentgateway-system rollout status deploy/agentgateway-ingress --timeout=180s
kubectl --context $KUBECONTEXT_CLUSTER2 -n llm scale deploy/mock-gpt-4o --replicas 1
kubectl --context $KUBECONTEXT_CLUSTER2 -n llm rollout status deploy/mock-gpt-4o --timeout=120s
```

When k6 finishes, read the summary (`http_req_failed`, `checks`, and the per-ingress counters).

**Observed result (v2026.6.1):** 14,401 requests at 40 rps over 6m while both clusters' agentgateway was drained, rolled, and restored. `http_req_failed: 0.18%` (26 of 14,401); `checks: 99.81%`; `cluster1_ingress: 7,147`, `cluster2_ingress: 7,254` — both ingresses served throughout. The 26 failures land at the cutover instants, because this test points traffic **directly** at each ingress while that same ingress is rolling and its local backend is drained — there is no GSLB to take the cluster out of client rotation first. The backend cross-cluster failover on its own (Step 5) was clean.

## Interpreting the results

| What | Result | Why |
|---|---|---|
| Backend drained, peer serves | **Zero downtime** | With no local endpoints, the global hostname fails over to the peer cluster's LLM over the east-west gateway. |
| agentgateway upgraded in a cluster | **Bounded by the rollout window** | A 2-replica + PDB ingress rolls one pod at a time; the brief blips occur only because clients are pointed straight at the rolling ingress with no DNS layer to move them off it first. |
| Two clusters, rolled in turn | **Global service stays up** | While one cluster is fully drained and upgraded, the other serves the global hostname. |

The multi-cluster advantage over in-place and blue/green: you can take an **entire cluster** out of service — for an agentgateway upgrade, a Kubernetes upgrade, or any maintenance — and the global service continues from the peer. The cost is running and meshing two clusters.

## The client entry tier (external)

This lab points traffic directly at each cluster's ingress, so the only blips occur while rolling the very ingress under test. In production a global traffic manager (a health-checked DNS / global load balancer, e.g. Route53 latency routing, or Solo's multi-cluster routing) sits in front of the two ingresses. Before upgrading a cluster you remove it from that rotation, so clients are served entirely by the healthy cluster during the maintenance window and never touch the rolling ingress — making the client-visible result zero-downtime. The gateway does not perform cross-cluster client routing itself; that tier is external, and it is the one piece this single-environment lab describes rather than runs.

> **A note on what fails over where.** North-south failover (client → ingress → global service) triggers when the local backend has **no endpoints** — scaling to 0, as in Step 5. A rolling restart of the *backend* (pods briefly `NotReady`) does not trigger it on the north-south path, so drain a backend by scaling to 0 when you want a clean cluster handoff.

## Cleanup

```bash
for c in $KUBECONTEXT_CLUSTER1 $KUBECONTEXT_CLUSTER2; do
  kubectl --context $c -n llm delete httproute mcu-llm --ignore-not-found
  kubectl --context $c -n agentgateway-system delete gateway agentgateway-ingress --ignore-not-found
  kubectl --context $c -n agentgateway-system delete enterpriseagentgatewayparameters agentgateway-ingress --ignore-not-found
  kubectl --context $c delete namespace llm --ignore-not-found
done
rm -f mcu.js
```

To tear down the whole environment, delete the clusters (`vind-down-2`).
