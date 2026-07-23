# Migration Guide — Enterprise Agentgateway v2026.5.x → v2026.7.x

Upgrade path, prerequisites, compatibility notes, downtime expectations, and best practices for moving an Enterprise Agentgateway install from the v2026.5.x line to v2026.7.x.

Releases within v2026.5.x differ only by image-tag patches (no API, chart, or behavior change), so the starting patch version does not affect these steps. Where a concrete target is needed, this guide uses v2026.7.0 (the latest v2026.7.x). The deltas below are cumulative — several were introduced at v2026.6.0 and carry forward unchanged into v2026.7.0; one (pull-secret consolidation) is new at v2026.7.0.

The rollout mechanics are covered in three companion labs. Pick one; this guide covers the version-to-version deltas that apply regardless of strategy.

| Strategy | Lab | When to use |
|---|---|---|
| In-place rolling (default) | [In-Place Rolling Upgrades](in-place-rolling-upgrades.md) | Single cluster, one proxy. Drains and replaces pods under load. |
| Blue/green across namespaces | [Blue/Green Across Namespaces](blue-green-namespaces.md) | Rollback by traffic flip instead of redeploy. |
| Multi-cluster | [Multi-Cluster Upgrades](multi-cluster-upgrades.md) | A peer cluster serves while one is taken out of service. |

---

## 1. Changes between v2026.5.x and v2026.7.x

### v2026.6.0 — Image registry consolidation

This is the change that affects private-registry / air-gapped installs. In v2026.5.x the images were published across **three registries**, so an air-gapped install pinned each component's registry, repository, and tag separately:

| Component | v2026.5.x image |
|---|---|
| controller | `us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2026.5.2` |
| proxy | `us-docker.pkg.dev/solo-public/enterprise-agentgateway/agentgateway-enterprise:2026.5.2` |
| ext-auth-service | `gcr.io/gloo-mesh/ext-auth-service:0.81.1` |
| rate-limiter | `gcr.io/gloo-mesh/rate-limiter:0.18.6` |
| ext-cache (redis) | `docker.io/redis:8.6.2-alpine` |

Starting at v2026.6.0 every image is published under **one registry path**, and the chart exposes a top-level `image` block as the global default for all of them. That holds unchanged through v2026.7.0:

| Component | v2026.7.0 image |
|---|---|
| controller | `us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2026.7.0` |
| proxy | `us-docker.pkg.dev/solo-public/enterprise-agentgateway/agentgateway-enterprise:2026.7.0` |
| ext-auth-service | `us-docker.pkg.dev/solo-public/enterprise-agentgateway/ext-auth-service:2026.7.0` |
| rate-limiter | `us-docker.pkg.dev/solo-public/enterprise-agentgateway/rate-limiter:2026.7.0` |
| ext-cache (redis) | `us-docker.pkg.dev/solo-public/enterprise-agentgateway/redis:8.6.4-alpine` |

So from v2026.6.0 onward you mirror one registry and set it once, in the controller Helm values:

```yaml
image:
  registry: my.registry/agw    # global default for controller, proxy, AND extensions
  pullPolicy: IfNotPresent
  # tag defaults to the chart version — usually omit
```

> **Migration hazard.** The v2026.5.x per-component image blocks (Helm `controller.image` and the CR's `spec.image` / `spec.sharedExtensions.<name>.image`) are **still honored** in v2026.6.x+ — they are not removed. So if you carry them forward (via `helm ... --reuse-values`, or by leaving them in the CR), the chart moves to v2026.7.0 but the pinned `tag: 2026.5.2` keeps the pods on the **old** image: the controller Deployment ends up labeled `chart=enterprise-agentgateway-v2026.7.0` while still running `image=…controller:2026.5.2`, and the proxy and extension pods do not roll at all. The version bump silently does nothing to the running images.

To actually move to v2026.7.0, drop the per-component pins and let everything inherit the global block:
- **Controller Helm values:** delete the `controller.image` block; add the top-level `image` block above.
- **`agentgateway-config` CR:** delete `spec.image` (proxy) and every `spec.sharedExtensions.<name>.image` (extensions), so the proxy and extensions inherit the global registry and the chart's v2026.7.0 tags.

A per-image override is still available on the CR (`spec.sharedExtensions.<name>.image`, highest precedence) when one extension needs a different registry, repository, or tag. See the [image list](../installation/image-list.md) for the tags to mirror.

### v2026.7.0 — imagePullSecrets consolidation

Through v2026.6.x, pull secrets had to be set **per owner** — a single top-level Helm `imagePullSecrets` only reached the controller; the proxy and each extension needed their own `imagePullSecrets` set on the `EnterpriseAgentgatewayParameters` CR ([solo-io/agentgateway-enterprise#7562](https://github.com/solo-io/agentgateway-enterprise/pull/7562) was the tracking issue for this gap).

**As of v2026.7.0, that gap is closed and live-verified:** a single top-level Helm `imagePullSecrets` on the controller release now propagates automatically to the proxy and every shared extension (ext-auth, rate-limiter, ext-cache, waf-server) — no CR-level pull-secret configuration is required. The CR-level override still works but is now optional, only needed if a specific component must use a *different* secret than the rest.

```yaml
imagePullSecrets:
- name: my-registry-secret    # now covers the controller, proxy, AND every extension
```

### v2026.6.0 — Kubernetes floor raised, support matrix shift

| Component | v2026.5.x | v2026.7.x |
|---|---|---|
| Kubernetes | 1.31 – 1.35 (`> 1.30`) | 1.32 – 1.36 (`> 1.31`) |
| Gateway API CRDs | 1.4 – 1.5 | 1.3 – 1.5 |
| Helm | ≥ 3.12 | ≥ 3.12 |
| Istio (ambient/waypoint) | 1.26 – 1.29 | 1.26 – 1.29 |
| Solo UI | 0.3.16 | 0.5.1 |

The Kubernetes floor moved at v2026.6.0 and hasn't moved again since — the same 1.32–1.36 floor applies at v2026.7.0. Kubernetes must be ≥ 1.32 before upgrading; this is the only prerequisite that blocks the upgrade. Gateway API CRDs at v1.5.0 remain valid.

### No API-group migration in this window

The API-group renames (`gateway.kgateway.dev` → `agentgateway.dev`, `gloo.solo.io` → `enterpriseagentgateway.solo.io`) predate the v2026.5 line. Resources already on v2026.5.x use the current groups (`enterpriseagentgateway.solo.io/v1alpha1`, `agentgateway.dev/v1alpha1`) and need no conversion.

---

## 2. Prerequisites and pre-flight checks

```bash
# 1. Kubernetes version must be >= 1.32
kubectl version -o json | grep -i gitVersion

# 2. Record the current chart and images for rollback
helm list -n agentgateway-system
helm get values enterprise-agentgateway -n agentgateway-system -a > /tmp/agw-values-pre-upgrade.yaml
kubectl get deploy -n agentgateway-system -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}'

# 3. Confirm current CRDs / API groups (already enterpriseagentgateway.solo.io)
kubectl get crds | grep -E 'agentgateway|solo.io'

# 4. Check saved values for per-component image settings that move to the global block (see §1)
grep -nE 'controller:\s*$|image:|imagePullSecrets' /tmp/agw-values-pre-upgrade.yaml
```

Checklist:
- [ ] Kubernetes ≥ 1.32 on every node
- [ ] Gateway API CRDs present (1.3–1.5; v1.5.0 is standard)
- [ ] Current Helm values captured to a file
- [ ] Per-component registry settings moved to the global `image` block (see §1)
- [ ] Private-registry pull secret set once via the top-level Helm `imagePullSecrets` (see §1) — CR-level overrides only needed for a component using a different secret
- [ ] Zero-downtime posture in place: ≥2 replicas + PDB + graceful shutdown (see §4)

---

## 3. Upgrade (in-place)

Two Helm releases, upgraded CRDs first, then the controller. Same OCI-chart flow as [001](../../001-install-enterprise-agentgateway.md), re-pointed at the new version.

```bash
export ENTERPRISE_AGW_VERSION=v2026.7.0
```

Step 1 — Upgrade the CRDs chart to apply any CRD schema changes for the target version.

```bash
helm upgrade -i --namespace agentgateway-system \
    --version $ENTERPRISE_AGW_VERSION enterprise-agentgateway-crds \
    oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds
```

Step 2 — Upgrade the controller chart with an explicit values file.

> **Do not use `--reuse-values`.** It carries forward only your previously-supplied values and does **not** merge the new chart's defaults, so the v2026.7.0 chart fails to template:
> ```
> Error: UPGRADE FAILED: .../config-configmap.yaml: <.Values.externalSecrets.stores>: nil pointer evaluating interface {}.stores
> ```
> `--reset-then-reuse-values` gets past that error, but it also re-applies the old per-component `image` pins and leaves every component on the v2026.5.x tag (see §1). Supply a values file instead. Because you are not reusing values, re-pass the license key and the GatewayClass parameters ref.

```bash
helm upgrade enterprise-agentgateway \
    oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
    --namespace agentgateway-system \
    --version $ENTERPRISE_AGW_VERSION \
    --set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
    -f - <<'EOF'
# v2026.6.x+: single global registry for controller, proxy, AND extensions.
# Tag defaults to the chart version, so components move to v2026.7.0 automatically.
image:
  registry: us-docker.pkg.dev/solo-public/enterprise-agentgateway   # your mirror in a real air-gap
  pullPolicy: IfNotPresent
# Private registry: single pull secret for the controller, proxy, AND every extension
# (v2026.7.0+ — see §1). Drop this block for a public registry.
#imagePullSecrets:
#- name: my-registry-secret
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-config
    namespace: agentgateway-system
EOF
```

Step 3 — Remove the per-component image *registry/repository/tag* overrides from the `agentgateway-config` CR so the proxy and extensions inherit the global registry and the v2026.7.0 tags. Delete the `registry`/`repository`/`tag` fields from `spec.image` (proxy) and every `spec.sharedExtensions.<name>.image` (extensions). Pull secrets no longer need to live here either — the top-level Helm `imagePullSecrets` from Step 2 now covers the proxy and extensions automatically; only set them here if a component needs a *different* secret than the rest:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-config
  namespace: agentgateway-system
spec:
  # No image registry/repository/tag anywhere — the registry is inherited from the
  # global image.registry in the controller Helm values (Step 2) and the tag defaults
  # to the chart version (v2026.7.0). No pull secrets here either — the top-level
  # Helm imagePullSecrets from Step 2 already covers the proxy and extensions.
  sharedExtensions:
    extauth:
      enabled: true
      deployment:
        spec:
          replicas: 1
          # Only needed to override with a secret different from the global one above
          #template:
          #  spec:
          #    imagePullSecrets:
          #    - name: my-registry-secret
    ratelimiter:
      enabled: true
      deployment:
        spec:
          replicas: 1
          #template:
          #  spec:
          #    imagePullSecrets:
          #    - name: my-registry-secret
    extCache:
      enabled: true
      deployment:
        spec:
          replicas: 1
          #template:
          #  spec:
          #    imagePullSecrets:
          #    - name: my-registry-secret
  logging:
    level: info
  service:
    spec:
      type: LoadBalancer
  deployment:
    spec:
      replicas: 2
      template:
        spec:
          # Only needed to override with a secret different from the global one above
          #imagePullSecrets:
          #- name: my-registry-secret
          containers:
          - name: agentgateway
            resources:
              requests:
                cpu: 300m
                memory: 128Mi
EOF
```

Then wait for the rollout:

```bash
kubectl rollout status deployment/enterprise-agentgateway -n agentgateway-system --timeout=300s
kubectl rollout status deployment/agentgateway-proxy      -n agentgateway-system --timeout=300s
```

> If your v2026.5.x install did **not** use per-component image overrides (public-registry install, images left at chart defaults), Steps 2–3 simplify to a single `helm upgrade --version $ENTERPRISE_AGW_VERSION` with your normal values — there are no image pins to unwind.

Gateway edits: on v2026.6.x+ the per-Gateway parameters attach via `spec.infrastructure.parametersRef`. A full `kubectl apply` is fine as long as the manifest includes that ref:

```bash
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  infrastructure:
    parametersRef:
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayParameters
      name: agentgateway-config
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
EOF
```

The controller re-reconciles when its pod is replaced; the proxy data plane rolls per §4.

### Proxy data plane

The proxy is a separate deployment (`agentgateway-proxy`) programmed by the controller. It rolls when its image or config changes. To validate the drain-and-replace path under live traffic, follow [In-Place Rolling Upgrades](in-place-rolling-upgrades.md), which drives continuous k6 traffic through a rollout and reports the result.

---

## 4. Downtime expectations

Measured results from the in-place lab, with ≥2 replicas + a PodDisruptionBudget (`minAvailable: 1`) + graceful shutdown:

| Traffic pattern | Downtime | Notes |
|---|---|---|
| Short completions | None | 12,000 requests, 0.00% failed, 100% checks through the rollout. |
| Long-lived streaming | None for new traffic; in-flight bounded | 157/160 streams completed; 3 in-flight streams cut at `shutdown.max`. Raise `shutdown.max` to fit the longest stream. |
| Stateless / StreamableHTTP MCP (discrete POSTs) | None (1 error in 378,185 iterations) | Session tokens are stateless; the proxy routes them across replicas. |
| Persistent SSE MCP | Session breaks | An SSE `GET` stream is pinned to one replica; rolling it closes the stream, and adding replicas does not help. Use a maintenance window or migrate to StreamableHTTP. |

Minimum posture, applied before upgrading:

```yaml
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
    min: 15      # keep accepting + signal clients to migrate (Connection: close / GOAWAY)
    max: 110     # hard drain deadline; the operator sets terminationGracePeriodSeconds to match
```

The controller upgrade does not interrupt data-plane traffic; the proxy serves with its last-known config while the controller restarts.

---

## 5. Verify and monitor

During the rollout, watch these in the Grafana stack from [002](../../002-set-up-ui-and-monitoring-tools.md):

- `agentgateway_build_info` — old and new versions during the roll, then only the new one.
- `agentgateway_requests_total{status=~"5.."}` — error rate should not spike.
- `agentgateway_xds_connection_terminations` — expect `Reconnect` reasons as proxies restart, not `ConnectionError`.

Post-upgrade checks:

```bash
# Every image tag should read 2026.7.0 (redis: 8.6.4-alpine). A tag still showing
# 2026.5.x means a per-component pin was carried forward — see §1.
kubectl get pods -n agentgateway-system \
  -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' | sort -u
# Confirm the chart version and the running image agree (they diverge under the §1 pin hazard):
kubectl get deploy enterprise-agentgateway -n agentgateway-system \
  -o jsonpath='chart={.metadata.labels.helm\.sh/chart}  image={.spec.template.spec.containers[0].image}{"\n"}'
# Confirm every deployment inherited the single pull secret (v2026.7.0+ — see §1):
kubectl get deploy -n agentgateway-system -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.imagePullSecrets}{"\n"}{end}'
# drive a known-good route and confirm 200s (e.g. the /openai mock route)
```

After Steps 2–3 all pods run `…/…:2026.7.0` (redis `8.6.4-alpine`) from the single registry, with the single pull secret on every deployment. Expect one additional shared-extension pod compared to v2026.5.x.

---

## 6. Rollback

- In-place: re-run the controller `helm upgrade` at the previous `--version`, passing the saved `/tmp/agw-values-pre-upgrade.yaml` with `-f` (not `--reuse-values`), and re-apply the previous `agentgateway-config` CR. CRD downgrades are not automatic; leaving the newer CRDs in place is safe.
- Blue/green: flip the weight back to the old (blue) proxy — no redeploy. Use this when rollback speed is a requirement. See [Blue/Green Across Namespaces](blue-green-namespaces.md).

---

## 7. Best practices

1. Confirm Kubernetes ≥ 1.32 first — the only hard blocker.
2. Do not `helm upgrade --reuse-values` — it fails to template the v2026.7.0 chart (§3). Pass an explicit values file.
3. For a private registry, move the registry from the per-component settings to the global `image` block and remove the CR image overrides (§1), or the version pin silently keeps pods on v2026.5.x. Set the pull secret once via the top-level Helm `imagePullSecrets` — it now covers the controller, proxy, and every extension.
4. Apply the zero-downtime posture before upgrading: 2+ replicas, PDB, graceful shutdown sized to the longest in-flight request.
5. Upgrade the CRDs chart first, then the controller chart.
6. Keep `spec.infrastructure.parametersRef` in the Gateway manifest you apply so `kubectl apply` doesn't drop it.
7. Treat persistent-SSE MCP as a maintenance event; replicas do not make it zero-downtime.
8. Validate under live traffic with the k6 jobs from [In-Place Rolling Upgrades](in-place-rolling-upgrades.md).
9. Capture pre-upgrade values for rollback.
