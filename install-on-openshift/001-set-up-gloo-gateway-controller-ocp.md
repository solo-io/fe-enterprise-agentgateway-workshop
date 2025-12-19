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
kubectl create namespace gloo-system
```

```bash
helm upgrade -i --create-namespace --namespace gloo-system \
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
helm upgrade -i -n gloo-system enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/enterprise-agentgateway \
--create-namespace \
--version $GLOO_VERSION \
--set-string licensing.licenseKey=$GLOO_TRIAL_LICENSE_KEY \
-f -<<EOF
#--- Optional: global override for image registry/tag
#image:
#  registry: us-docker.pkg.dev/solo-public/gloo-gateway
#  tag: "$GLOO_VERSION"
#  pullPolicy: IfNotPresent
EOF
```

Check that the Enterprise Agentgateway Controller is now running:

```bash
kubectl get pods -n gloo-system -l app.kubernetes.io/name=enterprise-agentgateway
```

Expected Output:

```bash
NAME                                       READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-5fc9d95758-n8vvb   1/1     Running   0          87s
```

## Configure agentgateway

We configure Agentgateway by applying a `ConfigMap`, `EnterpriseAgentgatewayParameters`, and a `Gateway` resource. The example below includes inline comments showing where configuration can be customized
```bash
kubectl apply -f- <<EOF
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentgateway-config
  namespace: gloo-system
data:
  config.yaml: |-
    config:
      # --- Label all metrics using a value extracted from the request body
      #metrics:
      #  fields:
      #    add:
      #      modelId: json(request.body).modelId
      logging:
        fields:
          add:
            # --- Capture all request headers as a single map under rq.headers.all
            rq.headers.all: 'request.headers'
            # --- Capture claims from a verified JWT token if JWT policy is enabled
            jwt: 'jwt'
            # --- Capture all request headers as individual keys (flattened)
            #rq.headers: 'flatten(request.headers)'
            # --- Capture a single header by name (example: x-foo)
            #x-foo: 'request.headers["x-foo"]'
            # --- Capture entire request body
            request.body: json(request.body)
            # --- Capture a field in the request body
            #request.body.modelId: json(request.body).modelId
            # --- Capture entire response body
            response.body: json(response.body)
        format: json
      tracing:
        otlpProtocol: grpc
        # Use the Jaeger endpoint (configured in lab 002)
        otlpEndpoint: http://jaeger-collector.observability.svc.cluster.local:4317
        randomSampling: 'true'
        headers: {}
        fields:
          add:
            gen_ai.operation.name: '"chat"'
            gen_ai.system: 'llm.provider'
            gen_ai.prompt: 'llm.prompt'
            gen_ai.completion: 'llm.completion.map(c, {"role":"assistant", "content": c})'
            gen_ai.usage.completion_tokens: 'llm.output_tokens'
            gen_ai.usage.prompt_tokens: 'llm.input_tokens'
            # Langfuse uses the wrong one here! Intentionally swap
            gen_ai.request.model: 'llm.response_model'
            gen_ai.response.model: 'llm.response_model'
            gen_ai.request: 'flatten(llm.params)'
            # --- Capture all request headers as a single map under rq.headers.all
            rq.headers.all: 'request.headers'
            # --- Capture claims from a verified JWT token if JWT policy is enabled
            jwt: 'jwt'
            # --- Capture entire request body
            request.body: json(request.body)
            # --- Capture a field in the request body
            #request.body.modelId: json(request.body).modelId
            # --- Capture the whole response body as JSON
            response.body: 'json(response.body)'
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-params
  namespace: gloo-system
spec:
  logging:
    level: info
  #--- Image overrides for deployment ---
  #image:
  #  tag: ""
  #  registry: us-docker.pkg.dev/solo-public/gloo-gateway
  #--- Required for OpenShift ---
  deployment:
    spec:
      securityContext: {}
      containers:
      - name: agentgateway
        securityContext: {}
  service:
    metadata:
      annotations:
        service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
    spec:
      type: LoadBalancer
  #--- Use rawConfig to inline custom configuration from ConfigMap ---
  #rawConfig:
  #  config:
  #    logging:
  #      fields:
  #        add:
  #          rq.headers.all: 'request.headers'
  #          jwt: 'jwt'
  #          request.body: json(request.body)
  #          response.body: json(response.body)
  #      format: json
  #    tracing:
  #      otlpProtocol: grpc
  #      otlpEndpoint: http://jaeger-collector.observability.svc.cluster.local:4317
  #      randomSampling: 'true'
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
  namespace: gloo-system
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
kubectl get pods -n gloo-system
```

Expected Output:

```bash
NAME                                                        READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-5fc9d95758-n8vvb                    1/1     Running   0          11m
ext-auth-service-enterprise-agentgateway-544c6565cf-t86ml   1/1     Running   0          5m4s
ext-cache-enterprise-agentgateway-9ddc746d8-cb7t2           1/1     Running   0          5m4s
rate-limiter-enterprise-agentgateway-6c8dd77b6b-n8v7m       1/1     Running   0          5m4s
```
