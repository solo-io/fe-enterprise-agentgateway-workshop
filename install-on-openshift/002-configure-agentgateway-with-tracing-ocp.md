# Configure the Gloo Agentgateway Proxy on OpenShift with Tracing enabled

## Pre-requisites
This lab assumes that you have completed the setup in `001`

## Lab Objectives
- To deploy agentgateway, we need to create a new `GatewayClass`, `GlooGatewayParameters`, and `Gateway`
- Enable tracing configuration in agentgateway using configmap override
- We will also configure a `HTTPListenerPolicy` to capture access logs for the agentgateway

Install agentgateway
```bash
kubectl apply -f- <<EOF
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-gateway-config
  namespace: gloo-system
data:
  config.yaml: |-
    config: 
      tracing: 
        otlpProtocol: grpc
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
---
apiVersion: gloo.solo.io/v1alpha1
kind: GlooGatewayParameters
metadata:
  name: gloo-agentgateway-params
  namespace: gloo-system
spec:
  kube:
    agentGateway:
      enabled: true
      logLevel: trace
      customConfigMapName: agent-gateway-config
      #--- Image overrides for deployment ---
      #image:  
      #  tag: "0.7.5"
    #--- Required for Openshift--- (not working as of 2.0.0-beta.3)
    #floatingUserId: true
    #--- Adding sample annotation specific to AWS env ---
    service:
      extraAnnotations:
        service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
    #--- Uncomment to add gateway to ambient mesh ---
    #podTemplate:
    #  extraLabels:
    #    istio.io/dataplane-mode: ambient
---
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
 name: gloo-agentgateway
spec:
 controllerName: solo.io/gloo-gateway-v2
 description: Specialized class for agentgateway.
 parametersRef:
   group: gloo.solo.io
   kind: GlooGatewayParameters
   name: gloo-agentgateway-params
   namespace: gloo-system
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: gloo-agentgateway
  namespace: gloo-system
spec:
  gatewayClassName: gloo-agentgateway
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
EOF
```

### Temporary patching of agentgateway, redis, and ext-auth

> Note:
> There is currently an issue with auto deploying the agentgateway, ext-auth, and redis components on OpenShift. As a temporary workaround, we can patch these Deployments and update the securityContext as per requirement in OCP.

#### gloo-agentgateway

```bash
kubectl patch deployment gloo-agentgateway -n gloo-system --type='json' -p='[
  {"op": "remove", "path": "/spec/template/spec/containers/0/securityContext/runAsUser"}
]'
```

Check that the Gloo Gateway Proxy is now running:

```bash
kubectl get pods -n gloo-system -l app.kubernetes.io/name=gloo-agentgateway
```

Expected Output:

```bash
NAME                               READY   STATUS    RESTARTS   AGE
gloo-agentgateway-8984f7f7-rr2qq   1/1     Running   0          16s
```

#### redis-gloo-agentgateway

To remove `runAsUser` from `redis`:

```bash
kubectl patch deployment redis-gloo-agentgateway -n gloo-system --type='json' -p='[
  {"op": "remove", "path": "/spec/template/spec/containers/0/securityContext/runAsUser"}
]'
```

#### ext-auth-service-gloo-agentgateway

- Remove `runAsUser` from ext-auth
- Add `runAsNonRoot: true` to ext-auth

```bash
kubectl patch deployment ext-auth-service-gloo-agentgateway -n gloo-system --type='json' -p='[
  {"op": "remove", "path": "/spec/template/spec/securityContext/runAsUser"},
  {"op": "add", "path": "/spec/template/spec/securityContext/runAsNonRoot", "value": true}
]'
```

Check that all these pods are now running in `gloo-system` NameSpace:

```bash
kubectl get pods -n gloo-system
```

Expected output:

```bash
NAME                                                READY   STATUS    RESTARTS       AGE
ext-auth-service-gloo-gateway-v2-7d5798b89b-wqc9l   1/1     Running   1 (103s ago)   114s
gloo-gateway-75b8749fbc-q9vmw                       1/1     Running   0              3m20s
gloo-gateway-proxy-5b687dd576-dcnnw                 1/1     Running   0              2m59s
rate-limiter-gloo-gateway-v2-f5d4647d8-mmm2v        1/1     Running   0              2m59s
redis-gloo-gateway-v2-57fd84df9d-544vd              1/1     Running   0              2m1s
```

## Next Steps
You should now be able to go back to the root directory and continue with `003`
