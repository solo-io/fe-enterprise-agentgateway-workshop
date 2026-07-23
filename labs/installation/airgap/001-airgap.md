# Install Enterprise Agentgateway (Air-Gap / Private Registry)

In this workshop, you'll deploy Enterprise Agentgateway and complete hands-on labs that showcase routing, security, observability, and agentic capabilities.

> **Air-gap note:** This lab uses `docker.io/ably7`, a public Docker Hub repo that stands in for the private registry you'd mirror to in a real air-gapped environment. Every chart-managed image is pulled from this single registry — swap `docker.io/ably7` for your own private registry to reproduce a true air-gap install with no access to `us-docker.pkg.dev`, `gcr.io`, or upstream public images at runtime.

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

```bash
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
export ENTERPRISE_AGW_VERSION=v2026.7.0
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

```bash
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
> The top-level Helm `image.registry` is the global default registry for every chart-managed image — the controller, the agentgateway proxy, and the auto-provisioned extensions (`ext-auth-service`, `rate-limiter`, `waf-server`, and `ext-cache`/`redis`). A single `image.registry: docker.io/ably7` override covers them all. The Solo-built images inherit the chart-version tag (`2026.7.0`); the `ext-cache` Redis image keeps its own upstream tag (`8.6.4-alpine`). Mirror each image at the tag shown in the [Air-Gap Mirror Reference](../image-list.md#air-gap-mirror-reference-dockerioably7). As of `v2026.7.0`, the top-level Helm `imagePullSecrets` is likewise the global default and propagates to the proxy and every extension automatically — no per-CR pull-secret overrides are needed unless a specific extension uses a different secret than the rest.

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
# Propagates to the controller, proxy, AND extensions automatically (v2026.7.0+):
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
# Register the operator's GatewayClass-wide parameters default.
# Shared extensions apply at the GatewayClass level, so they live in
# 'agentgateway-shared-extensions' (applied in the next step). If the referenced
# parameters are not found, the controller falls back to its defaults.
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-shared-extensions
    namespace: agentgateway-system
EOF
```

Check that the Enterprise Agentgateway Controller is now running:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=enterprise-agentgateway
```

Expected Output:

```bash
NAME                                       READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-5fc9d95758-n8vvb   1/1     Running   0          87s
```

### Shared extensions (operator)

Apply the GatewayClass-wide parameters the controller install referenced above. This is the operator's slice — it enables the shared extensions and sets their replica counts. It is attached at the GatewayClass level, so every Gateway of the `enterprise-agentgateway` class inherits it.

```bash
kubectl apply -f- <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-shared-extensions
  namespace: agentgateway-system
spec:
  sharedExtensions:
    extauth:
      enabled: true
      deployment:
        spec:
          replicas: 1
    ratelimiter:
      enabled: true
      deployment:
        spec:
          replicas: 1
    extCache:
      enabled: true
      deployment:
        spec:
          replicas: 1
EOF
```

## Deploy Agentgateway with customizations

This is the developer's slice: a per-Gateway `EnterpriseAgentgatewayParameters` (`agentgateway-config`) and the `Gateway` that consumes it. It carries the settings the app team owns — deployment, service, logging, and observability — and omits `sharedExtensions`, which the operator set at the GatewayClass level in the previous step. The two resources merge, with this per-Gateway config layering on top of the class default. The parameters attach to the `Gateway` via `spec.infrastructure.parametersRef`.

(If a single extension ever needs a different registry, repository, or tag than the global default, `EnterpriseAgentgatewayParameters` supports a highest-precedence `spec.sharedExtensions.<name>.image` override.)

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
  #--- Use rawConfig to inline custom configuration from ConfigMap ---
  rawConfig:
    config:
      # --- Label all metrics using a value extracted from the request body
      metrics:
        fields:
          add:
            # --- Label all metrics with a value extracted from a verified JWT token if present,
            #     falling back to the `x-org` request header (e.g. ANTHROPIC_CUSTOM_HEADERS from Claude Code)
            user_org: default(jwt.org, default(request.headers["x-org"], "public-tier"))
            user_team: default(jwt.team, "public-tier")
            user_tier: default(jwt.tier, "public-tier")
            user_name: default(jwt.preferred_username, default(request.headers["x-user"], "public-tier"))
            # --- Label all metrics with the virtual-key user_id extracted from the validated
            #     API key credential (empty when no API key is presented). The `llm-cost-tracking`
            #     lab relies on this label for per-user token/cost queries.
            user_id: default(apiKey.user_id, "")
            # --- Label all metrics using a value extracted from the request body
            #modelId: json(request.body).modelId
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

```bash
NAME                                                        READY   STATUS    RESTARTS   AGE
agentgateway-proxy-7d4c8c4d4b-lvdsq                         1/1     Running   0          11m
agentgateway-proxy-9f8e7d6c5b-xkpqr                         1/1     Running   0          11m
enterprise-agentgateway-5f9c5b95b4-gjblt                    1/1     Running   0          11m
ext-auth-service-enterprise-agentgateway-6fcc5bc989-22wgd   1/1     Running   0          11m
ext-cache-enterprise-agentgateway-6bfcb8c87d-vjzxn          1/1     Running   0          11m
rate-limiter-enterprise-agentgateway-589f66bb88-xz7nm       1/1     Running   0          11m
```

## Configure access logs (optional)

Agentgateway emits access logs by default. This step is optional — the enrichment fields below are not required by any later lab, but are useful for debugging and observability. Apply an `EnterpriseAgentgatewayPolicy` to enrich the default access logs with additional metadata extracted from the request and response:

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
        # --- Capture all JWT claims (use to discover available fields, then narrow down)
        - name: jwt.all
          expression: jwt
        # Streaming vs buffered — useful for debugging latency differences
        - name: llm.streaming
          expression: llm.streaming
        # Cache efficiency — shows cost savings from prompt caching
        - name: llm.cached_tokens
          expression: llm.cachedInputTokens
        # Reasoning tokens — relevant for o1/o3 models
        - name: llm.reasoning_tokens
          expression: llm.reasoningTokens
        # Full prompt conversation (has perf impact for large prompts)
        - name: llm.prompt
          expression: llm.prompt
        # LLM response content
        - name: llm.completion
          expression: 'llm.completion[0]'
        # --- Capture a single request header by name (example: x-foo)
        #- name: x-foo
        #  expression: 'request.headers["x-foo"]'
        # --- Capture entire request body and parse it as JSON
        #- name: request.body
        #  expression: json(request.body)
        # --- Capture entire response body and parse it as JSON
        #- name: response.body
        #  expression: json(response.body)
        # --- Capture a field in the request body
        #- name: request.body.modelId
        #  expression: json(request.body).modelId
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
        # --- Capture the claims from a verified JWT token if JWT policy is enabled
        - name: jwt
          expression: jwt
        # --- Capture entire response body and parse it as JSON
        - name: response.body
          expression: json(response.body)
        # --- Capture a single request header by name (example: x-foo)
        #- name: x-foo
        #  expression: 'request.headers["x-foo"]'
EOF
```

## Deploy Solo UI

The Solo UI includes a built-in OpenTelemetry collector (`solo-enterprise-telemetry-collector`) that receives traces from AgentGateway and surfaces them in the UI.

### Set required variables

```bash
export AGW_UI_VERSION=0.5.1
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
    tag: "26.1.11.9-alpine"
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
kubectl delete enterpriseagentgatewaypolicy access-logs tracing -n agentgateway-system --ignore-not-found
kubectl delete gateway agentgateway-proxy -n agentgateway-system --ignore-not-found
kubectl delete enterpriseagentgatewayparameters agentgateway-config agentgateway-shared-extensions -n agentgateway-system --ignore-not-found
helm uninstall management -n agentgateway-system
helm uninstall management-crds -n agentgateway-system
helm uninstall enterprise-agentgateway -n agentgateway-system
helm uninstall enterprise-agentgateway-crds -n agentgateway-system
kubectl delete gatewayclass enterprise-agentgateway enterprise-agentgateway-waypoint --ignore-not-found
kubectl delete namespace agentgateway-system
# (Optional) Remove the Kubernetes Gateway API CRDs only if nothing else on the cluster uses them
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
```
