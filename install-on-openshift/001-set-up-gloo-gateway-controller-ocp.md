# Gloo Gateway with Agentgateway on OpenShift

This minimal guide covers the installation and simple usage of Gloo Gateway with Agentgateway routing to OpenAI as a backend LLM

## Pre-requisites
- Kubernetes > 1.30
- Kubernetes Gateway API

## Lab Objectives
- Configure Kubernetes Gateway API CRDs
- Configure Gloo Gateway CRDs
- Install Gloo Gateway Controller
- Install Jaeger

### Kubernetes Gateway API CRDs

Installing the Kubernetes Gateway API custom resources is a pre-requisite to using Gloo Gateway

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml
```

To check if the the Kubernetes Gateway API CRDS are installed

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
```

Expected Output:

```bash
NAME              SHORTNAMES   APIVERSION                          NAMESPACED   KIND
gatewayclasses    gc           gateway.networking.k8s.io/v1        false        GatewayClass
gateways          gtw          gateway.networking.k8s.io/v1        true         Gateway
grpcroutes                     gateway.networking.k8s.io/v1        true         GRPCRoute
httproutes                     gateway.networking.k8s.io/v1        true         HTTPRoute
referencegrants   refgrant     gateway.networking.k8s.io/v1beta1   true         ReferenceGrant
```

### Configure Required Variables
Export your Gloo Trial license key variable and Gloo Gateway version
```bash
export GLOO_TRIAL_LICENSE_KEY=$GLOO_TRIAL_LICENSE_KEY
export GLOO_VERSION=2.0.0-rc.3
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

```bash
helm upgrade -i -n gloo-system gloo-gateway oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/gloo-gateway \
--create-namespace \
--version $GLOO_VERSION \
--set licensing.glooGatewayLicenseKey=$GLOO_TRIAL_LICENSE_KEY \
--set licensing.agentgatewayLicenseKey=$GLOO_TRIAL_LICENSE_KEY \
-f -<<EOF
imagePullSecrets: []
nameOverride: ""
fullnameOverride: "gloo-gateway"
serviceAccount:
  create: true
  annotations: {}
  name: ""
deploymentAnnotations: {}
podAnnotations:
  prometheus.io/scrape: "true"
podSecurityContext: {}
securityContext: {}
resources: {}
nodeSelector: {}
tolerations: []
affinity: {}
controller:
  replicaCount: 1
  logLevel: info
  #--- Image overrides for controller deployment ---
  #image:
  #  registry: ""
  #  repository: gloo-gateway-controller
  #  pullPolicy: ""
  #  tag: ""
  service:
    type: ClusterIP
    ports:
      grpc: 9977
      health: 9093
      metrics: 9092
  extraEnv: {}
#--- Image overrides for deployment ---
image:
  registry: us-docker.pkg.dev/solo-public/gloo-gateway
  tag: "$GLOO_VERSION"
  pullPolicy: IfNotPresent
inferenceExtension:
  enabled: false
  autoProvision: false
discoveryNamespaceSelectors: []
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

## Install Jaeger

Openshift SCC:
```bash
oc adm policy add-scc-to-group anyuid system:serviceaccounts:observability
```

Install Jaeger on the cluster, since we will be enabling tracing for the AI Gateway in a later step
```bash
helm upgrade -i jaeger jaegertracing/jaeger \
    -n observability \
    --create-namespace \
    -f - <<EOF
provisionDataStore:
  cassandra: false
allInOne:
  enabled: true
storage:
  type: memory
agent:
  enabled: false
collector:
  enabled: false
query:
  enabled: false
EOF
```

Check that the Gloo Gateway Controller is now running:

```bash
kubectl get pods -n observability
```

Expected Output:

```bash
NAME                      READY   STATUS    RESTARTS   AGE
jaeger-54b6c8b5d5-8s74n   1/1     Running   0          18m
```