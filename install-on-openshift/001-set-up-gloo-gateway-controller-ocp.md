# Install Enterprise Agentgateway on OpenShift

In this workshop, you'll deploy Enterprise Agentgateway on OpenShift and complete hands-on labs that showcase routing, security, observability, and Gen AI features.

## Pre-requisites
- Kubernetes > 1.30
- Kubernetes Gateway API
- OpenShift cluster

## Lab Objectives
- Configure Kubernetes Gateway API CRDs
- Configure Enterprise Agentgateway CRDs
- Install Enterprise Agentgateway Controller
- Configure agentgateway
- Validate that components are installed

### Kubernetes Gateway API CRDs

Installing the Kubernetes Gateway API custom resources is a pre-requisite to using Enterprise Agentgateway

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
```

To check if the the Kubernetes Gateway API CRDS are installed

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
```

Expected Output:

```bash
NAME                 SHORTNAMES   APIVERSION                          NAMESPACED   KIND
backendtlspolicies   btlspolicy   gateway.networking.k8s.io/v1        true         BackendTLSPolicy
gatewayclasses       gc           gateway.networking.k8s.io/v1        false        GatewayClass
gateways             gtw          gateway.networking.k8s.io/v1        true         Gateway
grpcroutes                        gateway.networking.k8s.io/v1        true         GRPCRoute
httproutes                        gateway.networking.k8s.io/v1        true         HTTPRoute
referencegrants      refgrant     gateway.networking.k8s.io/v1beta1   true         ReferenceGrant
```

## Install Enterprise Agentgateway

### Configure Required Variables
Export your Gloo Trial license key variable and Enterprise Agentgateway version
```bash
export GLOO_TRIAL_LICENSE_KEY=$GLOO_TRIAL_LICENSE_KEY
export GLOO_VERSION=2.1.0-beta.2
```

### Enterprise Agentgateway CRDs
```bash
kubectl create namespace enterprise-agentgateway
```

```bash
helm upgrade -i --create-namespace --namespace enterprise-agentgateway \
    --version $GLOO_VERSION enterprise-agentgateway-crds \
    oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/enterprise-agentgateway-crds
```

To check if the the Enterprise Agentgateway CRDs are installed-

```bash
kubectl get crds | grep -E "solo.io|agentgateway" | awk '{ print $1 }'
```

Expected output

```bash
agentgatewaybackends.agentgateway.dev
agentgatewayparameters.agentgateway.dev
agentgatewaypolicies.agentgateway.dev
authconfigs.extauth.solo.io
enterpriseagentgatewayparameters.enterpriseagentgateway.solo.io
enterpriseagentgatewaypolicies.enterpriseagentgateway.solo.io
ratelimitconfigs.ratelimit.solo.io
```

## Install Enterprise Agentgateway Controller
Using Helm:
```bash
helm upgrade -i -n enterprise-agentgateway enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/enterprise-agentgateway \
--create-namespace \
--version $GLOO_VERSION \
--set-string licensing.licenseKey=$GLOO_TRIAL_LICENSE_KEY \
-f -<<EOF
#--- Optional: override for image registry/tag for the controller
image:
  registry: us-docker.pkg.dev/solo-public/gloo-gateway
  tag: "$GLOO_VERSION"
  pullPolicy: IfNotPresent
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

## Configure agentgateway

**Note - SCC workaround for redis cache in 2.1.0-beta2**: For this beta release we will need to set `anyuid` for the redis cache until [#1235](https://github.com/solo-io/gloo-gateway/issues/1235) is completed
```bash
oc adm policy add-scc-to-user anyuid -z ext-cache-enterprise-agentgateway-enterprise-agentgateway-airgapped -n enterprise-agentgateway
```

## Air-gapped install (private repo images)
The config below shows how to override images for an air-gapped environment with images sourced from a private repo. This requires a custom `GatewayClass` to be created, in this example it is named `enterprise-agentgateway-airgapped`. 

If you do not have the requirement to use private images, **please skip to the next section to follow the standard install.**

We configure Agentgateway by applying a custom `GatewayClass`, `EnterpriseAgentgatewayParameters`, and a `Gateway` resource. The example below includes inline comments showing where configuration can be customized
```bash
kubectl apply -f- <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: enterprise-agentgateway-airgapped
spec:
  controllerName: solo.io/enterprise-agentgateway
  parametersRef:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-params
    namespace: enterprise-agentgateway
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
      replicas: 1
      container:
        image:
          registry: gcr.io
          repository: gloo-mesh/ext-auth-service
          tag: "0.71.4"
    ratelimiter:
      enabled: true
      replicas: 1
      container:
        image:
          registry: gcr.io
          repository: gloo-mesh/rate-limiter
          tag: "0.16.4"
    extCache:
      enabled: true
      replicas: 1
      container:
        image:
          registry: docker.io
          repository: redis
          tag: "7.2.4-alpine"
  deployment:
    spec:
      template:
        spec:
          containers:
          - name: agentgateway
            securityContext:
              allowPrivilegeEscalation: false
              capabilities:
                add:
                - NET_BIND_SERVICE
                drop:
                - ALL
              readOnlyRootFilesystem: true
              runAsNonRoot: true
              runAsUser:
                $patch: delete
          securityContext:
            sysctls:
            - name: net.ipv4.ip_unprivileged_port_start
              value: "0"
  logging:
    level: info
  #--- Image overrides for deployment ---
  image:
    registry: ghcr.io
    repository: agentgateway/agentgateway
    tag: "0.11.0-alpha.5e5533a2c6bfb8914d69662b06aef48b4e7b85d5"
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
        #otlpEndpoint: http://tempo-distributor.monitoring.svc.cluster.local:4317
        otlpEndpoint: http://jaeger-collector.observability.svc.cluster.local:4317
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
  #--- Uncomment to add gateway to ambient mesh ---
  #deployment:
  #  spec:
  #    template:
  #      metadata:
  #        labels:
  #          istio.io/dataplane-mode: ambient
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: enterprise-agentgateway
spec:
  gatewayClassName: enterprise-agentgateway-airgapped
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
EOF
```

## Standard Installation (public images)

**NOTE:** if you have already configured the setup from the air-gapped installation, please skip this step

We configure Agentgateway by applying a `EnterpriseAgentgatewayParameters`, and a `Gateway` resource. The example below includes inline comments showing where configuration can be customized

```bash
kubectl apply -f- <<'EOF'
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-params
  namespace: enterprise-agentgateway
spec:
  deployment:
    spec:
      template:
        spec:
          containers:
          - name: agentgateway
            securityContext:
              allowPrivilegeEscalation: false
              capabilities:
                add:
                - NET_BIND_SERVICE
                drop:
                - ALL
              readOnlyRootFilesystem: true
              runAsNonRoot: true
              runAsUser:
                $patch: delete
          securityContext:
            sysctls:
            - name: net.ipv4.ip_unprivileged_port_start
              value: "0"
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
        #otlpEndpoint: http://tempo-distributor.monitoring.svc.cluster.local:4317
        otlpEndpoint: http://jaeger-collector.observability.svc.cluster.local:4317
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
  #--- Uncomment to add gateway to ambient mesh ---
  #deployment:
  #  spec:
  #    template:
  #      metadata:
  #        labels:
  #          istio.io/dataplane-mode: ambient
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: enterprise-agentgateway
spec:
  gatewayClassName: enterprise-agentgateway
  infrastructure:
    parametersRef:
      name: agentgateway-params
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayParameters
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
EOF
```

Check that the Gloo Agentgateway Proxy is now running:

```bash
kubectl get pods -n enterprise-agentgateway
```

Expected Output:

```bash
NAME                                                        READY   STATUS    RESTARTS   AGE
agentgateway-778ff69fd4-wmcrv                               1/1     Running   0          34s
enterprise-agentgateway-5fc9d95758-v5jqf                    1/1     Running   0          3m45s
ext-auth-service-enterprise-agentgateway-544c6565cf-zwzzp   1/1     Running   0          33s
ext-cache-enterprise-agentgateway-67c78bfd44-5lmv8          1/1     Running   0          34s
rate-limiter-enterprise-agentgateway-666754f856-5gnjb       1/1     Running   0          34s
```
