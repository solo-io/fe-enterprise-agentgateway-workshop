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
  name: agentgateway-config
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
      #  tag: ""
    #--- Required for Openshift---
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
ext-auth-service-agentgateway-enterprise-5b5bcdc7fb-769ts   1/1     Running   0          28s
gloo-agentgateway-85fd5c587f-96b7p                          1/1     Running   0          29s
gloo-ext-cache-agentgateway-enterprise-59dc8ccf7b-5q5b6     1/1     Running   0          29s
gloo-gateway-6989b69f49-7q7db                               1/1     Running   0          52s
rate-limiter-agentgateway-enterprise-9fd599685-cpdsr        1/1     Running   0          28s
```

## Next Steps
You should now be able to go back to the root directory and continue with `003`
