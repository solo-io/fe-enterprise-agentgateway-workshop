# Install Enterprise Agentgateway

In this workshop, you’ll deploy Enterprise Agentgateway and complete hands-on labs that showcase routing, security, observability, and Gen AI features.

## Pre-requisites
- Kubernetes > 1.30
- Kubernetes Gateway API

## Lab Objectives
- Configure Kubernetes Gateway API CRDs
- Configure Enterprise Agentgateway CRDs
- Install Enterprise Agentgateway Controller
- Configure agentgateway
- Validate that components are installed

### Kubernetes Gateway API CRDs

Installing the Kubernetes Gateway API custom resources is a pre-requisite to using Enterprise Agentgateway. We're using the experimental CRDs to enable advanced features like mTLS frontend validation (see the [Frontend mTLS lab](frontend-mtls.md)). If frontend mTLS is not a requirement, you can continue with the standard install.

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
export ENTERPRISE_AGW_VERSION=v2.3.2
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
Using Helm:
```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
--create-namespace \
--version $ENTERPRISE_AGW_VERSION \
--set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
-f -<<EOF
#--- Optional: override for image registry/tag for the controller
#controller:
#  image:
#    registry: us-docker.pkg.dev/solo-public/enterprise-agentgateway
#    tag: "$ENTERPRISE_AGW_VERSION"
#    pullPolicy: IfNotPresent
#  imagePullSecrets:
#  - name: my-registry-secret
# --- Override the default Agentgateway parameters used by this GatewayClass
# If the referenced parameters are not found, the controller will use the defaults
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-config
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

## Deploy Agentgateway with customizations
The configuration below demonstrates how to override container images for air-gapped environments by sourcing them from a private registry, along with other customizations exposed through `EnterpriseAgentgatewayParameters`, such as adding annotations or labels, modifying deployment and service settings, and extending observability capabilities. While this example uses the default public images, it illustrates how those images can be replaced with ones hosted in a private repository.

```bash
kubectl apply -f- <<'EOF'
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-config
  namespace: agentgateway-system
spec:
  ### -- uncomment to override shared extensions -- ###
  sharedExtensions:
    extauth:
      enabled: true
      deployment:
        spec:
          replicas: 1
          #--- imagePullSecrets for private registry ---
          #template:
          #  spec:
          #    imagePullSecrets:
          #    - name: my-registry-secret
      #--- Image overrides for deployment ---
      #image:
      #  registry: gcr.io
      #  repository: gloo-mesh/ext-auth-service
      #  tag: ""
    ratelimiter:
      enabled: true
      deployment:
        spec:
          replicas: 1
          #--- imagePullSecrets for private registry ---
          #template:
          #  spec:
          #    imagePullSecrets:
          #    - name: my-registry-secret
      #--- Image overrides for deployment ---
      #image:
      #  registry: gcr.io
      #  repository: gloo-mesh/rate-limiter
      #  tag: ""
    extCache:
      enabled: true
      deployment:
        spec:
          replicas: 1
          #--- imagePullSecrets for private registry ---
          #template:
          #  spec:
          #    imagePullSecrets:
          #    - name: my-registry-secret
      #--- Image overrides for deployment ---
      #image:
      #  registry: docker.io
      #  repository: redis
      #  tag: ""
  logging:
    level: info
  #--- Image overrides for deployment ---
  #image:
  #  registry: us-docker.pkg.dev
  #  repository: solo-public/enterprise-agentgateway/agentgateway-enterprise
  #  tag: ""
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
            user_id: 'request.headers["x-user-id"]'
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
          #--- imagePullSecrets for private registry ---
          #imagePullSecrets:
          #- name: my-registry-secret
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

Apply an `EnterpriseAgentgatewayPolicy` to export traces to the telemetry collector deployed in `002`. Skip this step if you are not setting up the Gloo UI.

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

## Next Steps
Enterprise Agentgateway is now installed and configured with observability. Continue with `002` to set up the Gloo UI and monitoring tools (Prometheus, Grafana) to visualize metrics, logs, and traces.