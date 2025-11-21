# Install Gloo Gateway with Agentgateway

In this workshop, you’ll deploy Gloo Gateway V2 with Agentgateway and complete hands-on labs that showcase routing, security, observability, and Gen AI features.

## Pre-requisites
- Kubernetes > 1.30
- Kubernetes Gateway API

## Lab Objectives
- Configure Kubernetes Gateway API CRDs
- Configure Gloo Gateway CRDs
- Install Gloo Gateway Controller
- Configure agentgateway
- Validate that components are installed

### Kubernetes Gateway API CRDs

Installing the Kubernetes Gateway API custom resources is a pre-requisite to using Gloo Gateway

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

## Install Gloo Gateway

### Configure Required Variables
Export your Gloo Trial license key variable and Gloo Gateway version
```bash
export GLOO_TRIAL_LICENSE_KEY=$GLOO_TRIAL_LICENSE_KEY
export GLOO_VERSION=2.0.1
```

### Gloo Gateway CRDs
```bash
kubectl create namespace gloo-system
```

```bash
helm upgrade -i --create-namespace --namespace gloo-system \
    --version $GLOO_VERSION gloo-gateway-crds \
    oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/gloo-gateway-crds
```

To check if the the Gloo Gateway CRDs are installed-

```bash
kubectl get crds | grep -E "solo.io|kgateway" | awk '{ print $1 }'
```

Expected output

```bash
authconfigs.extauth.solo.io
backendconfigpolicies.gateway.kgateway.dev
backends.gateway.kgateway.dev
directresponses.gateway.kgateway.dev
gatewayextensions.gateway.kgateway.dev
gatewayparameters.gateway.kgateway.dev
gloogatewayparameters.gloo.solo.io
glootrafficpolicies.gloo.solo.io
httplistenerpolicies.gateway.kgateway.dev
ratelimitconfigs.ratelimit.solo.io
trafficpolicies.gateway.kgateway.dev
```

## Install Gloo Gateway Controller
Using Helm:
```bash
helm upgrade -i -n gloo-system gloo-gateway oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/gloo-gateway \
--create-namespace \
--version $GLOO_VERSION \
--set-string licensing.glooGatewayLicenseKey=$GLOO_TRIAL_LICENSE_KEY \
--set-string licensing.agentgatewayLicenseKey=$GLOO_TRIAL_LICENSE_KEY \
-f -<<EOF
#--- Optional: global override for image registry/tag
#image:
#  registry: us-docker.pkg.dev/solo-public/gloo-gateway
#  tag: "$GLOO_VERSION"
#  pullPolicy: IfNotPresent
#--- Enable integration with agentgateway ---
agentgateway:
  enabled: true
EOF
```

Check that the Gloo Gateway Controller is now running:

```bash
kubectl get pods -n gloo-system -l app.kubernetes.io/name=gloo-gateway
```

Expected Output:

```bash
NAME                            READY   STATUS    RESTARTS   AGE
gloo-gateway-64ff8f5c96-sjv7p   1/1     Running   0          3h17m
```

## Configure agentgateway

We configure Agentgateway by applying a `ConfigMap`, `GlooGatewayParameters`, and a `Gateway` resource. The example below includes inline comments showing where configuration can be customized
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
            #request.body: json(request.body)
            # --- Capture a field in the request body
            #request.body.modelId: json(request.body).modelId
        format: json
      tracing: 
        otlpProtocol: grpc
        # Use the Jaeger endpoint
        #otlpEndpoint: http://jaeger-collector.observability.svc.cluster.local:4317
        # Use the Tempo distributor endpoint
        otlpEndpoint: http://tempo-distributor.monitoring.svc.cluster.local:4317
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
            # --- Capture the whole response body as JSON
            #response.body: 'json(response.body)'
---
apiVersion: gloo.solo.io/v1alpha1
kind: GlooGatewayParameters
metadata:
  name: agentgateway-params
  namespace: gloo-system
spec:
  kube:
    agentgateway:
      enabled: true
      logLevel: info
      customConfigMapName: agentgateway-config
      #--- Image overrides for deployment ---
      #image:  
      #  tag: "0.10.3"
    #--- Adding sample annotation specific to AWS env ---
    service:
      extraAnnotations:
        service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
      type: LoadBalancer
    #--- Uncomment to add gateway to ambient mesh ---
    #podTemplate:
    #  extraLabels:
    #    istio.io/dataplane-mode: ambient
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: gloo-system
spec:
  gatewayClassName: agentgateway-enterprise
  infrastructure:
    parametersRef:
      name: agentgateway-params
      group: gloo.solo.io
      kind: GlooGatewayParameters  
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
agentgateway-55ccdfb97f-vgj4t                               1/1     Running   0          22s
ext-auth-service-agentgateway-enterprise-76f699bd4d-8sm2b   1/1     Running   0          21s
gloo-ext-cache-agentgateway-enterprise-6d9fb97dc8-dn75s     1/1     Running   0          22s
gloo-gateway-6b589f4849-sqw6q                               1/1     Running   0          3m17s
rate-limiter-agentgateway-enterprise-5cc6d9586b-rxj9r       1/1     Running   0          21s
```