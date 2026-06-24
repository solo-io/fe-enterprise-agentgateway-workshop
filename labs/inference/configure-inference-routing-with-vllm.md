# Configure Inference Routing with vLLM

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

> **Node architecture (important):** this lab requires `linux/amd64` nodes. The upstream Endpoint Picker image (`registry.k8s.io/gateway-api-inference-extension/epp:v1.1.0`) is published as a single-platform `linux/amd64` image. On `arm64` clusters — e.g., kind on Apple Silicon Macs — the EPP pod will fail with `exec /epp: exec format error`. Run this lab on a cloud cluster (GKE/EKS/AKS) or an x86 VM. The vLLM CPU image also fails inside Docker Desktop on Mac with `AssertionError: Not enough allowed NUMA nodes`, even on amd64 Docker Desktop.

> **Cluster sizing:** the vLLM pod in this lab requests `cpu: 1500m` / `memory: 6Gi` — tuned to fit on a 4-vCPU node (e.g., GKE `n2-standard-4`) alongside the ~1.3 vCPU that GKE system pods (kube-proxy, fluent-bit, gke-metrics, etc.) typically consume. Memory is the binding dimension here — Qwen2.5-0.5B with `VLLM_CPU_KVCACHE_SPACE=2` needs ~5 GiB of resident memory and will be OOMKilled below 6 GiB. First-run model download plus the readiness probe's 180s initial delay means the pod typically takes 3–4 minutes to become ready.

## Lab Objectives
- Enable the Gateway API Inference Extension on the existing Enterprise Agentgateway install
- Deploy a vLLM pod that serves `Qwen/Qwen2.5-0.5B-Instruct` on CPU
- Install the Gateway API Inference Extension (GAIE) CRDs
- Deploy an `InferencePool` and the `llm-d` Endpoint Picker (EPP) via the upstream Helm chart
- Create an `HTTPRoute` that routes `/v1` to the `InferencePool`
- Curl `/v1/completions` through the gateway and observe the EPP-driven endpoint selection

## Enable the Inference Extension

The Inference Extension is gated behind a Helm value on the Enterprise Agentgateway chart. We re-upgrade the release that lab `001` already installed, reusing its values and toggling the flag on. The chart version is read from the existing release so this lab tracks whatever lab `001` deployed.

```bash
export ENTERPRISE_AGW_VERSION=$(helm list -n agentgateway-system \
  --filter '^enterprise-agentgateway$' -o json \
  | jq -r '.[0].chart' | sed 's/^enterprise-agentgateway-//')

echo "Using $ENTERPRISE_AGW_VERSION"

helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --set inferenceExtension.enabled=true \
  --reuse-values
```

## Deploy vLLM serving Qwen2.5-0.5B-Instruct

This deployment runs vLLM on CPU and exposes the OpenAI-compatible API on port 8000. The pod label `app: vllm-qwen25-05b-instruct` is what the `InferencePool` will select on in the next step.

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-qwen25-05b-instruct
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-qwen25-05b-instruct
  template:
    metadata:
      labels:
        app: vllm-qwen25-05b-instruct
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai-cpu:v0.18.0
          imagePullPolicy: IfNotPresent
          command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
          args:
            - "--model"
            - "Qwen/Qwen2.5-0.5B-Instruct"
            - "--port"
            - "8000"
          env:
            - name: PORT
              value: "8000"
            - name: VLLM_CPU_KVCACHE_SPACE
              value: "2"
          ports:
            - containerPort: 8000
              name: http
              protocol: TCP
          livenessProbe:
            failureThreshold: 240
            httpGet:
              path: /health
              port: http
              scheme: HTTP
            initialDelaySeconds: 180
            periodSeconds: 5
            successThreshold: 1
            timeoutSeconds: 1
          readinessProbe:
            failureThreshold: 600
            httpGet:
              path: /health
              port: http
              scheme: HTTP
            initialDelaySeconds: 180
            periodSeconds: 5
            successThreshold: 1
            timeoutSeconds: 1
          resources:
            limits:
              cpu: "1500m"
              memory: "6Gi"
            requests:
              cpu: "1500m"
              memory: "6Gi"
          volumeMounts:
            - mountPath: /data
              name: data
            - mountPath: /dev/shm
              name: shm
      restartPolicy: Always
      terminationGracePeriodSeconds: 30
      volumes:
        - name: data
          emptyDir: {}
        - name: shm
          emptyDir:
            medium: Memory
EOF
```

Wait for the pod to be Ready (this typically takes 3–5 minutes — model download plus the readiness probe's 180s initial delay):

```bash
kubectl rollout status -n agentgateway-system deployment/vllm-qwen25-05b-instruct --timeout=10m
```

## Install the Gateway API Inference Extension CRDs

This installs the `InferencePool` Custom Resource Definition that the Inference Extension uses for routing.

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.4.0/manifests.yaml
```

Verify the CRD is installed:

```bash
kubectl get crd | grep inference.networking.k8s.io
```

Expected output:

```
inferencepools.inference.networking.k8s.io      ...
```

## Deploy the InferencePool and Endpoint Picker (EPP)

The upstream GAIE `inferencepool` Helm chart creates an `InferencePool` resource and deploys the `llm-d` Endpoint Picker (EPP). The EPP is the component that actually picks which vLLM pod a given request goes to, based on real-time load. We pass `provider.name=none` because we already installed our own Gateway provider (Enterprise Agentgateway) in lab `001`.

```bash
export IGW_CHART_VERSION=v1.1.0
export GATEWAY_PROVIDER=none

helm install vllm-qwen25-05b-instruct \
  -n agentgateway-system \
  --set inferencePool.modelServers.matchLabels.app=vllm-qwen25-05b-instruct \
  --set provider.name=$GATEWAY_PROVIDER \
  --version $IGW_CHART_VERSION \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool
```

Verify the `InferencePool` was created:

```bash
kubectl get inferencepool -n agentgateway-system
```

Expected output:

```
NAME                       AGE
vllm-qwen25-05b-instruct   30s
```

Verify the EPP pod is running. The chart labels EPP pods with `inferencepool: <release-name>-epp`:

```bash
kubectl get pods -n agentgateway-system -l inferencepool=vllm-qwen25-05b-instruct-epp
```

## Create the HTTPRoute

This `HTTPRoute` attaches to the existing `agentgateway-proxy` Gateway from lab `001` and routes the `/v1` path prefix to the `InferencePool`. The `backendRefs.group: inference.networking.k8s.io` and `kind: InferencePool` tell agentgateway to delegate endpoint selection to the EPP.

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: inference-vllm
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /v1
      backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: vllm-qwen25-05b-instruct
      timeouts:
        request: 300s
EOF
```

## Send an inference request

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/v1/completions" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen2.5-0.5B-Instruct",
    "prompt": "What is the warmest city in the USA?",
    "max_tokens": 100,
    "temperature": 0.5
  }'
```

Expected output (truncated):

```
HTTP/1.1 200 OK
content-type: application/json
...
{"choices":[{"finish_reason":"length","index":0,"text":" The warmest city in the United States is Phoenix, Arizona ..."}],"model":"Qwen/Qwen2.5-0.5B-Instruct","object":"text_completion","usage":{"completion_tokens":100,"prompt_tokens":10,"total_tokens":110}}
```

## Validation checklist

1. `kubectl get inferencepool -n agentgateway-system` shows `vllm-qwen25-05b-instruct`.
2. The vLLM pod is `Running` and `Ready`, and the EPP pod is `Running`.
3. The HTTPRoute reports `Accepted: True` and `ResolvedRefs: True`:
   ```bash
   kubectl get httproute -n agentgateway-system inference-vllm -o jsonpath='{.status.parents[*].conditions[*].type}={.status.parents[*].conditions[*].status}{"\n"}'
   ```
4. The curl above returns HTTP 200 with a `choices[].text` body.
5. The agentgateway access log for the request carries an `inferencepool.selected_endpoint=<podIP>:<port>` field — proof that the EPP picked a specific pod for the request, not a Service VIP (see Observability below).

## Observability

### Confirm endpoint-picking via the access log (primary signal)

AgentGateway logs the EPP's per-request selection in its access log as `inferencepool.selected_endpoint=<podIP>:<port>`. This is the definitive proof that endpoint-picking happened.

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20 \
  | grep inference-vllm
```

Expected output (single line, wrapped for readability):

```
info request gateway=agentgateway-system/agentgateway-proxy listener=http
  route=agentgateway-system/inference-vllm
  endpoint=10.112.3.4:8000
  ...
  http.method=POST http.path=/v1/completions http.status=200
  inferencepool.selected_endpoint=10.112.3.4:8000 duration=8540ms
```

The `inferencepool.selected_endpoint` value matches a vLLM pod IP, confirming the EPP chose that endpoint rather than agentgateway round-robin'ing against a Service.

### Gateway request metrics with inference backend label

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. Inference-routed requests show up with a `backend` label ending in `.inference.cluster.local`:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 >/dev/null 2>&1 & \
PF=$!; sleep 2 && curl -s http://localhost:15020/metrics \
  | grep 'agentgateway_requests_total.*inference.cluster.local' ; kill $PF
```

If this returns empty, retry — the agentgateway proxy runs with multiple replicas and `port-forward` connects to one of them. Send another inference request and rerun.

### EPP startup logs

At default verbosity (`--v 1`), the EPP logs cover startup (controller registration, ext-proc gRPC server listening on `:9002`) but not per-request selection decisions. Use these logs to confirm the picker is healthy:

```bash
kubectl logs -n agentgateway-system -l inferencepool=vllm-qwen25-05b-instruct-epp --tail 50
```

Look for `gRPC server listening` on port `9002` — this is the ext-proc endpoint that agentgateway calls into for endpoint selection.

> **Note on EPP metrics:** the EPP exposes Prometheus metrics on container port `9090`, but the endpoint is gated by Kubernetes TokenReview authentication by default and returns `401 Unauthorized` without a valid ServiceAccount token. To scrape it, configure Prometheus with the appropriate token or run a custom scraper inside the cluster. Out of scope for this lab.

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../installation/002-set-up-ui-and-monitoring-tools.md). For traces, use Tempo through the Grafana **Explore** view.

1. Port-forward to the Grafana service:
   ```bash
   kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
   ```
2. Open <http://localhost:3000> in your browser.
3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)
4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics.
5. In **Home > Explore**, select **Tempo** and search for recent traces to find spans that include the InferencePool backend selection.

## Cleanup

```bash
kubectl delete httproute -n agentgateway-system inference-vllm
helm uninstall vllm-qwen25-05b-instruct -n agentgateway-system
kubectl delete deployment -n agentgateway-system vllm-qwen25-05b-instruct
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.4.0/manifests.yaml
```

Optionally, disable the Inference Extension on the gateway:

```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version $ENTERPRISE_AGW_VERSION \
  --set inferenceExtension.enabled=false \
  --reuse-values
```
