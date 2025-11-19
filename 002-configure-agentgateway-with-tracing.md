# Configure the Gloo Agentgateway Proxy with Tracing enabled

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
  name: agentgateway-config
  namespace: gloo-system
data:
  config.yaml: |-
    config: 
      metrics:
        fields:
          add:
            modelId: json(request.body).modelId
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
            #request.body: json(request.body).modelId
        format: json
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
            rq.headers.all: 'request.headers'
            jwt: 'jwt'
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
      image:  
        tag: "0.10.3"
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