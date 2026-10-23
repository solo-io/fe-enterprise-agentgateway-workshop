# Install Enterprise Agentgateway

In this workshop, you’ll deploy Enterprise Agentgateway and complete hands-on labs that cover routing, security, observability, and agentic capabilities.

## Pre-requisites
- Kubernetes > 1.31
- Kubernetes Gateway API

## Lab Objectives
- Configure Kubernetes Gateway API CRDs
- Configure Enterprise Agentgateway CRDs
- Install Enterprise Agentgateway Controller and the Solo UI
- Configure agentgateway
- Validate that components are installed

### Kubernetes Gateway API CRDs

Installing the Kubernetes Gateway API custom resources is a pre-requisite to using Enterprise Agentgateway.

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
```

To check if the the Kubernetes Gateway API CRDS are installed

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
```

Expected Output:

```
NAME                 SHORTNAMES   APIVERSION                     NAMESPACED   KIND
backendtlspolicies   btlspolicy   gateway.networking.k8s.io/v1   true         BackendTLSPolicy
gatewayclasses       gc           gateway.networking.k8s.io/v1   false        GatewayClass
gateways             gtw          gateway.networking.k8s.io/v1   true         Gateway
grpcroutes                        gateway.networking.k8s.io/v1   true         GRPCRoute
httproutes                        gateway.networking.k8s.io/v1   true         HTTPRoute
listenersets         lset         gateway.networking.k8s.io/v1   true         ListenerSet
referencegrants      refgrant     gateway.networking.k8s.io/v1   true         ReferenceGrant
```

> [!NOTE]
> The command above installs the **standard** channel. If you also need `TCPRoute` or `UDPRoute` (`gateway.networking.k8s.io/v1alpha2`), install the **experimental** channel instead: swap `standard-install.yaml` for `experimental-install.yaml`. No lab in this workshop requires them.

## Install Enterprise Agentgateway

### Configure Required Variables
Export your Solo Trial license key variable and Enterprise Agentgateway version
```bash
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY
export ENTERPRISE_AGW_VERSION=v2026.10.1
```

### Enterprise Agentgateway CRDs
```bash
kubectl create namespace agentgateway-system
```

```bash
helm upgrade -i --create-namespace --namespace agentgateway-system \
    --version $ENTERPRISE_AGW_VERSION enterprise-agentgateway-crds \
    oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds
```

> [!NOTE]
> If the cluster already runs Gloo Gateway or kgateway, those installs own the `ratelimit.solo.io` CRDs. Add `--set installRateLimitCRDs=false` so this chart leaves them alone.

To check if the the Enterprise Agentgateway CRDs are installed-

```bash
kubectl api-resources | awk 'NR==1 || /enterpriseagentgateway\.solo\.io|agentgateway\.dev|ratelimit\.solo\.io|extauth\.solo\.io/'
```

Expected output

```
NAME                                    SHORTNAMES   APIVERSION                                NAMESPACED   KIND
agentgatewaybackends                    agbe         agentgateway.dev/v1alpha1                 true         AgentgatewayBackend
agentgatewaymodels                      agmodel      agentgateway.dev/v1alpha1                 true         AgentgatewayModel
agentgatewayparameters                  agpar        agentgateway.dev/v1alpha1                 true         AgentgatewayParameters
agentgatewaypolicies                    agpol        agentgateway.dev/v1alpha1                 true         AgentgatewayPolicy
enterpriseagentgatewaybackends          eagbe        enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayBackend
enterpriseagentgatewaybudgets           eagbud       enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayBudget
enterpriseagentgatewayexternalsecrets   eages        enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayExternalSecret
enterpriseagentgatewayparameters        eagpar       enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayParameters
enterpriseagentgatewaypolicies          eagpol       enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayPolicy
enterpriseagentgatewayrbacpolicies      eagrbacpol   enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayRBACPolicy
authconfigs                             ac           extauth.solo.io/v1                        true         AuthConfig
ratelimitconfigs                        rlc          ratelimit.solo.io/v1alpha1                true         RateLimitConfig
```

The OSS `agentgateway.dev` CRDs and the Enterprise `enterpriseagentgateway.solo.io` CRDs are distinct resources: the Enterprise kinds wrap their OSS counterparts. This workshop uses the Enterprise kinds (`eagbe`, `eagpar`, `eagpol`) throughout.

## Install Enterprise Agentgateway Controller

> [!NOTE]
> **Air-gapped or private-registry install?** This lab pulls images from the public registry. For the full set of charts and images to mirror, see the [image list](labs/installation/image-list.md). To mirror all chart-managed images into a private registry, follow the dedicated [air-gap install lab](labs/installation/airgap/001-airgap.md) instead.

The `enterprise-agentgateway` chart also installs the Solo UI through its `ui` subchart. The UI includes a built-in OpenTelemetry collector (`enterprise-agentgateway-ui-telemetry-collector`) that receives traces from Agentgateway, and a ClickHouse database that stores them. The UI reads the license key from the Secret that this release creates.

Using Helm:
```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
--create-namespace \
--version $ENTERPRISE_AGW_VERSION \
--set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
-f - <<EOF
ui:
  enabled: true
  #--- imagePullSecrets and registry override for the UI images (UI backend, autoauth, OTEL collectors) ---
  #global:
  #  imagePullSecrets:
  #  - name: my-registry-secret
  #  imageRegistry: my-registry.example.com
  service:
    type: ClusterIP
  features:
    #--- Cost Management dashboard (budgets, spend by model/team) ---
    costManagement: true
    #--- Set to false to make the dashboard read-only (no budget or dimension edits from the UI) ---
    costManagementWrites: true
  traces:
    verbose: true
  clickhouse:
    enabled: true
    #--- Image override for ClickHouse (no registry key, embed registry in repository if needed) ---
    #image:
    #  repository: clickhouse/clickhouse-server
    #  tag: ""
EOF
```

> [!NOTE]
> Later labs that re-run `helm upgrade` on `enterprise-agentgateway` pass `--reuse-values` so the license and `ui` values stay in place. If you run your own upgrade without it, include the `ui` block above or Helm removes the UI. To install without the UI, set `ui.enabled: false`. Tracing and the Cost Management labs need the UI.

Check that the Enterprise Agentgateway Controller is now running:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=enterprise-agentgateway
```

Expected Output:

```
NAME                                       READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-5fc9d95758-n8vvb   1/1     Running   0          87s
```

### Check the Solo UI

Check that the Solo UI components are running:

```bash
kubectl rollout status deploy/enterprise-agentgateway-ui -n agentgateway-system --timeout=300s
kubectl get pods -n agentgateway-system | awk 'NR==1 || /-ui|clickhouse/'
```

Expected Output:

```
NAME                                                              READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-clickhouse-shard0-0                       1/1     Running   0          60s
enterprise-agentgateway-ui-d77d84fc4-md7n8                        2/2     Running   0          60s
enterprise-agentgateway-ui-k8sobjects-collector-cc6948cbc-d6t95   1/1     Running   0          60s
enterprise-agentgateway-ui-telemetry-collector-0                  1/1     Running   0          60s
```

## Deploy Agentgateway with customizations
This applies a per-Gateway `EnterpriseAgentgatewayParameters` (`agentgateway-config`) and the `Gateway` that consumes it. It carries the gateway-specific infrastructure settings: deployment, service, and logging. You configure metric labels, access logs, and tracing separately, with the `EnterpriseAgentgatewayPolicy` resources in the sections below.

The parameters attach to the `Gateway` via `spec.infrastructure.parametersRef`, so the linkage is explicit on the Gateway that consumes it. Apply the parameters before or alongside the Gateway. If the Gateway is applied first, the proxy won't deploy until the referenced parameters exist.

```bash
kubectl apply -f- <<'EOF'
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-config
  namespace: agentgateway-system
spec:
  logging:
    level: info
  service:
    metadata:
      annotations:
        service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
    spec:
      type: LoadBalancer
  deployment:
    spec:
      replicas: 2
      template:
        #--- Uncomment to add gateway to ambient mesh ---
        #metadata:
        #  labels:
        #    istio.io/dataplane-mode: ambient
        spec:
          containers:
          - name: agentgateway
            resources:
              requests:
                cpu: 300m
                memory: 128Mi
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  #--- Attach the EnterpriseAgentgatewayParameters above to this Gateway ---
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

Check that the Agentgateway proxy is now running:

```bash
kubectl get pods -n agentgateway-system
```

Expected Output:

```
NAME                                                        READY   STATUS    RESTARTS   AGE
agentgateway-proxy-7d4c8c4d4b-lvdsq                         1/1     Running   0          11m
agentgateway-proxy-9f8e7d6c5b-xkpqr                         1/1     Running   0          11m
enterprise-agentgateway-5f9c5b95b4-gjblt                    1/1     Running   0          11m
ext-auth-service-enterprise-agentgateway-6fcc5bc989-22wgd   1/1     Running   0          11m
ext-cache-enterprise-agentgateway-6bfcb8c87d-vjzxn          1/1     Running   0          11m
rate-limiter-enterprise-agentgateway-589f66bb88-xz7nm       1/1     Running   0          11m
waf-server-enterprise-agentgateway-6fc78487cc-vbqd8         1/1     Running   0          11m
```

## Configure metric labels

Apply an `EnterpriseAgentgatewayPolicy` to add custom labels to every Prometheus metric the gateway exposes. Each label value is a [CEL expression](https://docs.solo.io/agentgateway/latest/reference/cel/) evaluated per request, so you can slice token usage and cost by organization, team, tier, or user. Wrap fields that are absent on some requests (for example `jwt.*` before a JWT policy is attached) in `default()`, otherwise the label value falls back to `unknown`.

Every distinct label value creates a new Prometheus time series, so keep the list short and low-cardinality. The policy accepts at most 16 labels.

```bash
kubectl apply -f- <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: metrics
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: agentgateway-proxy
  frontend:
    metrics:
      attributes:
        add:
        # --- Values extracted from a verified JWT token if present, falling back to the
        #     `x-org` / `x-user` request headers (e.g. ANTHROPIC_CUSTOM_HEADERS from Claude Code)
        - name: user_org
          expression: 'default(jwt.org, default(request.headers["x-org"], "public-tier"))'
        - name: user_team
          expression: 'default(jwt.team, "public-tier")'
        - name: user_tier
          expression: 'default(jwt.tier, "public-tier")'
        - name: user_name
          expression: 'default(jwt.preferred_username, default(request.headers["x-user"], "public-tier"))'
        # --- The virtual-key user_id extracted from the validated API key credential
        #     (empty when no API key is presented). The `llm-cost-tracking` lab relies
        #     on this label for per-user token/cost queries.
        - name: user_id
          expression: 'default(apiKey.user_id, "")'
        # --- Label all metrics with a value extracted from the JSON request body
        #- name: modelId
        #  expression: 'default(json(request.body).modelId, "")'
EOF
```

## Configure access logs (optional)

Agentgateway emits access logs by default. This step is optional: the enrichment fields below are not required by any later lab, but are useful for debugging and observability. Apply an `EnterpriseAgentgatewayPolicy` to enrich the default access logs with additional metadata extracted from the request and response. Each attribute is a [CEL expression](https://docs.solo.io/agentgateway/latest/reference/cel/); wrap fields that are absent on some requests (for example `llm.*` on non-LLM routes, or `jwt.*` before a JWT policy is attached) in `default()` so the log field is always present:

```bash
kubectl apply -f- <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: access-logs
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: agentgateway-proxy
  frontend:
    accessLog:
      attributes:
        add:
        # --- Request context
        - name: request_path
          expression: request.path
        - name: status_code
          expression: string(response.code)
        # --- Latency breakdown: time spent in the upstream (LLM) vs the gateway itself
        - name: llm_duration
          expression: proxy.upstreamDuration
        - name: request_proc_duration
          expression: proxy.requestProcessingDuration
        - name: response_proc_duration
          expression: proxy.responseProcessingDuration
        # --- LLM telemetry
        - name: provider
          expression: default(llm.provider, "none")
        - name: model
          expression: default(llm.responseModel, "none")
        - name: prompt_tokens
          expression: string(default(llm.inputTokens, 0))
        - name: completion_tokens
          expression: string(default(llm.outputTokens, 0))
        - name: total_cost_usd
          expression: string(default(llm.cost.total, 0.0))
        # Streaming vs buffered — useful for debugging latency differences
        - name: llm_streaming
          expression: default(llm.streaming, false)
        # Cache efficiency — shows cost savings from prompt caching
        - name: llm_cached_tokens
          expression: string(default(llm.cachedInputTokens, 0))
        # Reasoning tokens — relevant for reasoning models
        - name: llm_reasoning_tokens
          expression: string(default(llm.reasoningTokens, 0))
        # --- Identity
        - name: client_ip
          expression: source.address
        - name: user_id
          expression: default(jwt.sub, "anonymous")
        # All claims from a verified JWT (use to discover available fields, then narrow down)
        - name: jwt_all
          expression: default(jwt, {})
        # --- Content capture (debug only — perf impact for large prompts/responses)
        #- name: llm_prompt
        #  expression: llm.prompt
        #- name: llm_completion
        #  expression: 'default(llm.completion[0], "")'
        # --- Capture a single request header by name (example: x-foo)
        #- name: x_foo
        #  expression: 'default(request.headers["x-foo"], "")'
        # --- Capture a field from the JSON request body
        #- name: request_model
        #  expression: 'default(json(request.body).model, "")'
EOF
```

## Configure tracing

Apply an `EnterpriseAgentgatewayPolicy` to export traces to the Solo UI telemetry collector. Skip this step if you set `ui.enabled: false`.

```bash
kubectl apply -f- <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: tracing
  namespace: agentgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: agentgateway-proxy
  frontend:
    tracing:
      backendRef:
        name: enterprise-agentgateway-ui-telemetry-collector
        namespace: agentgateway-system
        port: 4317
      protocol: GRPC
      randomSampling: "true"
      attributes:
        add:
        # --- Identity: subject of the verified JWT if a JWT policy is enabled
        - name: enduser.id
          expression: 'default(jwt.sub, "anonymous")'
        # --- LLM telemetry
        - name: llm.is_streaming
          expression: 'default(llm.streaming, false)'
        - name: llm.usage.cached_tokens
          expression: 'default(llm.cachedInputTokens, 0)'
        - name: llm.usage.reasoning_tokens
          expression: 'default(llm.reasoningTokens, 0)'
        # First user message in the prompt (perf impact for large prompts)
        - name: llm.prompt.user
          expression: 'default(llm.prompt.filter(m, m.role == "user")[0].content, "")'
        # LLM response content (perf impact for large responses)
        - name: llm.completion.output
          expression: 'default(llm.completion[0], "")'
        # --- Capture all claims from a verified JWT token
        #- name: jwt
        #  expression: jwt
        # --- Capture a single request header by name (example: x-foo)
        #- name: http.request.header.x_foo
        #  expression: 'default(request.headers["x-foo"], "")'
EOF
```

## Access Solo UI

To access the Solo UI and view traces:

1. Port-forward to the Solo UI service:
```bash
kubectl port-forward -n agentgateway-system svc/enterprise-agentgateway-ui 4000:80
```

2. Open your browser and navigate to `http://localhost:4000`

## (Optional) Fine-Grained RBAC for the Solo UI

The UI installs with coarse roles (Admin, Writer, Reader) that apply to every resource. To scope budgets, virtual keys, and HTTPRoutes per team with `EnterpriseAgentgatewayRBACPolicy`, see the [Solo UI Fine-Grained RBAC lab](labs/observability/solo-ui-fine-grained-rbac.md).

## Uninstall

To tear everything down, work in reverse order. Delete the `Gateway` first so the controller can clean up the proxy deployment and service before you remove the controller itself.

```bash
kubectl delete enterpriseagentgatewaypolicy metrics access-logs tracing -n agentgateway-system --ignore-not-found
kubectl delete gateway agentgateway-proxy -n agentgateway-system --ignore-not-found
kubectl delete enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --ignore-not-found
helm uninstall enterprise-agentgateway -n agentgateway-system
helm uninstall enterprise-agentgateway-crds -n agentgateway-system
# The CRDs chart keeps the RBAC policy CRD on uninstall; delete it explicitly
kubectl delete crd enterpriseagentgatewayrbacpolicies.enterpriseagentgateway.solo.io --ignore-not-found
kubectl delete gatewayclass enterprise-agentgateway enterprise-agentgateway-waypoint --ignore-not-found
kubectl delete namespace agentgateway-system
# (Optional) Remove the Kubernetes Gateway API CRDs only if nothing else on the cluster uses them
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
```

## Next Steps
Enterprise Agentgateway and the Solo UI are now installed and configured with observability. Continue with `002` to set up monitoring tools (Prometheus, Grafana) to visualize metrics.
