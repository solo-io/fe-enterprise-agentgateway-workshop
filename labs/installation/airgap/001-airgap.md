# Install Enterprise Agentgateway (Air-Gap / Private Registry)

In this workshop, you'll deploy Enterprise Agentgateway and complete hands-on labs that showcase routing, security, observability, and agentic capabilities.

> **Air-gap note:** This lab uses `docker.io/ably7`, a public Docker Hub repo that stands in for the private registry you'd mirror to in a real air-gapped environment. Every chart-managed image comes from this single registry. Swap `docker.io/ably7` for your own private registry to reproduce a true air-gap install with no access to `us-docker.pkg.dev`, `gcr.io`, or upstream public images at runtime.

## Pre-requisites
- Kubernetes > 1.31
- Kubernetes Gateway API

## Lab Objectives
- Configure Kubernetes Gateway API CRDs
- Configure Enterprise Agentgateway CRDs
- Install Enterprise Agentgateway Controller
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
NAME                 SHORTNAMES   APIVERSION                           NAMESPACED   KIND
backendtlspolicies   btlspolicy   gateway.networking.k8s.io/v1         true         BackendTLSPolicy
gatewayclasses       gc           gateway.networking.k8s.io/v1         false        GatewayClass
gateways             gtw          gateway.networking.k8s.io/v1         true         Gateway
grpcroutes                        gateway.networking.k8s.io/v1         true         GRPCRoute
httproutes                        gateway.networking.k8s.io/v1         true         HTTPRoute
listenersets         lset         gateway.networking.k8s.io/v1         true         ListenerSet
referencegrants      refgrant     gateway.networking.k8s.io/v1         true         ReferenceGrant
tcproutes                         gateway.networking.k8s.io/v1alpha2   true         TCPRoute
tlsroutes                         gateway.networking.k8s.io/v1         true         TLSRoute
udproutes                         gateway.networking.k8s.io/v1alpha2   true         UDPRoute
```

## Install Enterprise Agentgateway

### Configure Required Variables
Export your Solo Trial license key variable and Enterprise Agentgateway version
```bash
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY
export ENTERPRISE_AGW_VERSION=v2026.8.2
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

To check if the the Enterprise Agentgateway CRDs are installed-

```bash
kubectl api-resources | awk 'NR==1 || /enterpriseagentgateway\.solo\.io|agentgateway\.dev|ratelimit\.solo\.io|extauth\.solo\.io/'
```

Expected output

```
NAME                                SHORTNAMES        APIVERSION                                NAMESPACED   KIND
agentgatewaybackends                agbe              agentgateway.dev/v1alpha1                 true         AgentgatewayBackend
agentgatewayparameters              agpar             agentgateway.dev/v1alpha1                 true         AgentgatewayParameters
agentgatewaypolicies                agpol             agentgateway.dev/v1alpha1                 true         AgentgatewayPolicy
enterpriseagentgatewayparameters    eagpar            enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayParameters
enterpriseagentgatewaypolicies      eagpol            enterpriseagentgateway.solo.io/v1alpha1   true         EnterpriseAgentgatewayPolicy
authconfigs                         ac                extauth.solo.io/v1                        true         AuthConfig
ratelimitconfigs                    rlc               ratelimit.solo.io/v1alpha1                true         RateLimitConfig
```

## Install Enterprise Agentgateway Controller

> [!NOTE]
> The top-level Helm `image.registry` is the global default registry for every chart-managed image: the controller, the agentgateway proxy, and the auto-provisioned extensions (`ext-auth-service`, `rate-limiter`, `waf-server`, and `ext-cache`/`redis`). A single `image.registry: docker.io/ably7` override covers them all. The Solo-built images inherit the chart-version tag (`2026.8.2`); the `ext-cache` Redis image keeps its own upstream tag (`8.6.6-alpine`). Mirror each image at the tag shown in the [Air-Gap Mirror Reference](../image-list.md#air-gap-mirror-reference-dockerioably7). The top-level Helm `imagePullSecrets` is the global default too, and it reaches the proxy and every extension. Add a per-CR pull-secret override only when one extension uses a different secret than the rest.

Using Helm:
```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
--create-namespace \
--version $ENTERPRISE_AGW_VERSION \
--set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
-f -<<EOF
# --- Point all chart-managed images at a private registry (air-gap) ---
# The top-level 'image' block is the GLOBAL default for the controller, the
# agentgateway proxy, AND the auto-provisioned extensions (ext-auth-service,
# rate-limiter, ext-cache/redis). A single registry override covers everything.
# The tag defaults to the chart version, so you normally do not set it here.
image:
  registry: docker.io/ably7
  pullPolicy: IfNotPresent
# Propagates to the controller, proxy, AND extensions automatically:
#imagePullSecrets:
#- name: my-registry-secret
# Extensions inherit image.registry; their tags are pinned by the chart.
# The default repositories below already match the chart's image names:
extAuth:
  image:
    repository: ext-auth-service
rateLimiter:
  image:
    repository: rate-limiter
extCache:
  image:
    repository: redis
EOF
```

Check that the Enterprise Agentgateway Controller is now running:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=enterprise-agentgateway
```

Expected Output:

```
NAME                                       READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-5fc9d95758-n8vvb   1/1     Running   0          87s
```

## Deploy Agentgateway with customizations

This is the developer's slice: a per-Gateway `EnterpriseAgentgatewayParameters` (`agentgateway-config`) plus the `Gateway` that consumes it. It carries the infrastructure settings the app team owns: deployment, service, and logging. You configure metric labels, access logs, and tracing separately, with the `EnterpriseAgentgatewayPolicy` resources in the sections below. The shared extensions are the controller's to set: it enables ext-auth, rate-limiter, and ext-cache on its own at one replica each. These parameters attach to the `Gateway` through `spec.infrastructure.parametersRef`.

If one extension needs a different registry, repository, or tag than the global default, set `spec.sharedExtensions.<name>.image` on `EnterpriseAgentgatewayParameters`. That override takes highest precedence.

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
  #--- Attach the developer's EnterpriseAgentgatewayParameters to this Gateway ---
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

Agentgateway emits access logs by default. This step is optional: no later lab needs the enrichment fields below, though they help with debugging and observability. Apply an `EnterpriseAgentgatewayPolicy` to enrich the default access logs with additional metadata extracted from the request and response. Each attribute is a [CEL expression](https://docs.solo.io/agentgateway/latest/reference/cel/); wrap fields that are absent on some requests (for example `llm.*` on non-LLM routes, or `jwt.*` before a JWT policy is attached) in `default()` so the log field is always present:

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
        # Streaming vs buffered: useful for debugging latency differences
        - name: llm_streaming
          expression: default(llm.streaming, false)
        # Cache efficiency: shows cost savings from prompt caching
        - name: llm_cached_tokens
          expression: string(default(llm.cachedInputTokens, 0))
        # Reasoning tokens: relevant for reasoning models
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
        # --- Content capture (debug only: perf impact for large prompts/responses)
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

Apply an `EnterpriseAgentgatewayPolicy` to export traces to the telemetry collector deployed in `002`. Skip this step if you are not setting up the Solo UI.

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
        name: solo-enterprise-telemetry-collector
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

## Deploy Solo UI

The Solo UI includes a built-in OpenTelemetry collector (`solo-enterprise-telemetry-collector`) that receives traces from AgentGateway and surfaces them in the UI.

### Set required variables

```bash
export AGW_UI_VERSION=0.5.5
```

### Step 1: Install/upgrade CRDs

```bash
helm upgrade -i management-crds oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management-crds \
  --namespace agentgateway-system \
  --create-namespace \
  --version "$AGW_UI_VERSION"
```

### Step 2: Install/upgrade the chart

The `management-crds` subchart is bundled inside `management` and enabled by default. Since we installed it separately in Step 1, disable it here to avoid ownership conflicts:

```bash
helm upgrade -i management oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management \
--namespace agentgateway-system \
--create-namespace \
--version "$AGW_UI_VERSION" \
-f - <<EOF
management-crds:
  enabled: false
licensing:
  licenseKey: "${SOLO_TRIAL_LICENSE_KEY}"
global:
  imagePullPolicy: IfNotPresent
  imagePullSecrets: []
  image:
    registry: docker.io
    repository: ably7
    tag: ""
service:
  type: ClusterIP
  clusterIP: ""
products:
  kagent:
    enabled: false
  agentgateway:
    enabled: true
    namespace: agentgateway-system
    features:
      #--- Cost Management dashboard (budgets, spend by model/team) ---
      cost-management: true
      #--- Set to false to make the dashboard read-only (no budget or dimension edits from the UI) ---
      cost-management-writes: true
  mesh:
    enabled: false
  agentregistry:
    enabled: false
idp:
  registry: docker.io
  repository: ably7
  name: solo-enterprise-autoauth
  tag: "v0.2.2"
telemetry:
  image:
    registry: docker.io
    repository: ably7
    name: opentelemetry-collector-contrib
    tag: "0.153.0"
clickhouse:
  enabled: true
  image:
    repository: docker.io/ably7/clickhouse-server
    tag: "26.3.17-alpine"
tracing:
  verbose: true
EOF
```

Check that the Solo UI components are running:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/instance=management
```

## Access Solo UI

1. Port-forward to the Solo UI service:
```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
```

2. Open your browser and navigate to `http://localhost:4000`

## Uninstall

To tear everything down, work in reverse order. Delete the `Gateway` first so the controller can clean up the proxy deployment and service before you remove the controller itself.

```bash
kubectl delete enterpriseagentgatewaypolicy metrics access-logs tracing -n agentgateway-system --ignore-not-found
kubectl delete gateway agentgateway-proxy -n agentgateway-system --ignore-not-found
kubectl delete enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system --ignore-not-found
helm uninstall management -n agentgateway-system
helm uninstall management-crds -n agentgateway-system
helm uninstall enterprise-agentgateway -n agentgateway-system
helm uninstall enterprise-agentgateway-crds -n agentgateway-system
kubectl delete gatewayclass enterprise-agentgateway enterprise-agentgateway-waypoint --ignore-not-found
kubectl delete namespace agentgateway-system
# (Optional) Remove the Kubernetes Gateway API CRDs only if nothing else on the cluster uses them
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
```
