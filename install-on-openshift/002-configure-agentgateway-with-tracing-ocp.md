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
    agentgateway:
      enabled: true
      logLevel: info
      customConfigMapName: agent-gateway-config
      #--- Image overrides for deployment ---
      #image:  
      #  tag: ""
    #--- Required for Openshift---
    floatingUserId: true
    omitDefaultSecurityContext: true
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
kind: Gateway
metadata:
  name: gloo-agentgateway
  namespace: gloo-system
spec:
  gatewayClassName: agentgateway-enterprise
  infrastructure:
    parametersRef:
      name: gloo-agentgateway-params
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
kubectl get pods -n gloo-system -l app.kubernetes.io/name=gloo-agentgateway
```

Expected Output:

```bash
NAME                               READY   STATUS    RESTARTS   AGE
gloo-agentgateway-8984f7f7-rr2qq   1/1     Running   0          16s
```

### Temporary patching of redis, and ext-auth

> Note:
> There is currently an issue with auto deploying the ext-auth, and redis components on OpenShift. As a temporary workaround, we can patch these Deployments and update the securityContext as per requirement in OCP.

#### redis-gloo-agentgateway

To remove `runAsUser` from `redis`:

```bash
kubectl patch deployment gloo-ext-cache-agentgateway-enterprise -n gloo-system --type='json' -p='[
  {"op": "remove", "path": "/spec/template/spec/containers/0/securityContext/runAsUser"}
]'
```

#### ext-auth-service-gloo-agentgateway

- Remove `runAsUser` from ext-auth
- Add `runAsNonRoot: true` to ext-auth

```bash
kubectl patch deployment ext-auth-service-agentgateway-enterprise -n gloo-system --type='json' -p='[
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
NAME                                                        READY   STATUS    RESTARTS   AGE
ext-auth-service-agentgateway-enterprise-5f8dc65f48-ssjh2   1/1     Running   0          19s
gloo-agentgateway-67fd6668d8-hmzj6                          1/1     Running   0          8m25s
gloo-ext-cache-agentgateway-enterprise-674dc8f989-pf9vn     1/1     Running   0          34s
gloo-gateway-855cc5b4fd-ghgnl                               1/1     Running   0          8m26s
rate-limiter-agentgateway-enterprise-5f5d8b85c-lq8qv        1/1     Running   0          8m25s
```

## Next Steps
You should now be able to go back to the root directory and continue with `003`
