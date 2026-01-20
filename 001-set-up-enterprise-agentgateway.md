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

Installing the Kubernetes Gateway API custom resources is a pre-requisite to using Enterprise Agentgateway. We're using the experimental CRDs to enable advanced features like mTLS frontend validation (lab 026). If frontend mTLS is not a requirement, you can continue with the standard install.

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/experimental-install.yaml
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
referencegrants      refgrant     gateway.networking.k8s.io/v1beta1    true         ReferenceGrant
tcproutes                         gateway.networking.k8s.io/v1alpha2   true         TCPRoute
tlsroutes                         gateway.networking.k8s.io/v1alpha3   true         TLSRoute
udproutes                         gateway.networking.k8s.io/v1alpha2   true         UDPRoute
```

## Install Enterprise Agentgateway

### Configure Required Variables
Export your Solo Trial license key variable and Enterprise Agentgateway version
```bash
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY
export ENTERPRISE_AGW_VERSION=2.1.0
```

### Enterprise Agentgateway CRDs
```bash
kubectl create namespace enterprise-agentgateway
```

```bash
helm upgrade -i --create-namespace --namespace enterprise-agentgateway \
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
helm upgrade -i -n enterprise-agentgateway enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
--create-namespace \
--version $ENTERPRISE_AGW_VERSION \
--set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
-f -<<EOF
#--- Optional: override for image registry/tag for the controller
image:
  registry: us-docker.pkg.dev/solo-public/enterprise-agentgateway
  tag: "$ENTERPRISE_AGW_VERSION"
  pullPolicy: IfNotPresent
# --- Override the default Agentgateway parameters used by this GatewayClass
# If the referenced parameters are not found, the controller will use the defaults
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-params
    namespace: enterprise-agentgateway
EOF
```

Check that the Enterprise Agentgateway Controller is now running:

```bash
kubectl get pods -n enterprise-agentgateway -l app.kubernetes.io/name=enterprise-agentgateway
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
  name: agentgateway-params
  namespace: enterprise-agentgateway
spec:
  ### -- uncomment to override shared extensions -- ###
  sharedExtensions:
    extauth:
      enabled: true
      deployment:
        spec:
          replicas: 1
      #--- Image overrides for deployment ---
      #image:
      #  registry: gcr.io
      #  repository: gloo-mesh/ext-auth-service
      #  tag: "0.71.4"
    ratelimiter:
      enabled: true
      deployment:
        spec:
          replicas: 1
      #--- Image overrides for deployment ---
      #image:
      #  registry: gcr.io
      #  repository: gloo-mesh/rate-limiter
      #  tag: "0.17.2"
    extCache:
      enabled: true
      deployment:
        spec:
          replicas: 1
      #--- Image overrides for deployment ---
      #image:
      #  registry: docker.io
      #  repository: redis
      #  tag: "7.2.12-alpine"
  logging:
    level: info
  #--- Image overrides for deployment ---
  #image:
  #  registry: ghcr.io
  #  repository: solo-io/agentgateway-enterprise
  #  tag: "0.11.1-patch1"
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
      #metrics:
      #  fields:
      #    add:
      #      modelId: json(request.body).modelId
      logging:
        fields:
          add:
            rq.headers.all: 'request.headers'
            jwt: 'jwt'
            request.body: json(request.body)
            response.body: json(response.body)
            # --- Capture all request headers as individual keys (flattened)
            rq.headers: 'flatten(request.headers)'
            # --- Capture a single header by name (example: x-foo)
            x-foo: 'request.headers["x-foo"]'
            # --- Capture entire request body
            request.body: json(request.body)
            # --- Capture a field in the request body
            request.body.modelId: json(request.body).modelId
        format: json
      tracing:
        otlpProtocol: grpc
        otlpEndpoint: http://tempo-distributor.monitoring.svc.cluster.local:4317
        randomSampling: 'true'
        fields:
          add:
            gen_ai.operation.name: '"chat"'
            gen_ai.system: "llm.provider"
            gen_ai.prompt: 'llm.prompt'
            gen_ai.completion: 'llm.completion.map(c, {"role":"assistant", "content": c})'
            gen_ai.request.model: "llm.requestModel"
            gen_ai.response.model: "llm.responseModel"
            gen_ai.usage.completion_tokens: "llm.outputTokens"
            gen_ai.usage.prompt_tokens: "llm.inputTokens"
            gen_ai.request: 'flatten(llm.params)'
            # --- Capture all request headers as a single map under rq.headers.all
            rq.headers.all: 'request.headers'
            # --- Capture claims from a verified JWT token if JWT policy is enabled
            jwt: 'jwt'
            # --- Capture the whole response body as JSON
            response.body: 'json(response.body)'
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
  name: agentgateway
  namespace: enterprise-agentgateway
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
kubectl get pods -n enterprise-agentgateway
```

Expected Output:

```bash
NAME                                                        READY   STATUS    RESTARTS   AGE
agentgateway-7d4c8c4d4b-lvdsq                               1/1     Running   0          11m
enterprise-agentgateway-5f9c5b95b4-gjblt                    1/1     Running   0          11m
ext-auth-service-enterprise-agentgateway-6fcc5bc989-22wgd   1/1     Running   0          11m
ext-cache-enterprise-agentgateway-6bfcb8c87d-vjzxn          1/1     Running   0          11m
rate-limiter-enterprise-agentgateway-589f66bb88-xz7nm       1/1     Running   0          11m
```