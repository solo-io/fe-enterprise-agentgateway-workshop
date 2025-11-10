# Configure Mock OpenAI Server
In this lab, you’ll deploy a lightweight OpenAI compatible mock server to validate core routing, metrics, and tracing AI Gateway features without needing real OpenAI credentials. Later labs will use OpenAI as the backend, but this mock server can be swapped in with minimal changes.

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Configure a mock LLM server that serves the OpenAI spec
- Create a route to our mock server as our backend LLM provider using a `Backend` and `HTTPRoute`
- Curl mock server through the agentgateway proxy
- Validate the request went through the gateway in Jaeger UI

## Create a Mock vLLM Server
Deploy the mock server using the manifest below.  
This mock server, called **vLLM Simulator**, is maintained by the [vLLM community](https://github.com/llm-d/llm-d-inference-sim).  
It provides a lightweight implementation of the OpenAI-compatible `/v1/chat/completions` endpoint, which we’ll use throughout the labs to simulate LLM responses

Mock server for gpt-4o
```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o
  namespace: gloo-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-4o
  template:
    metadata:
      labels:
        app: mock-gpt-4o
    spec:
      containers:
      - args:
        - --model
        - gpt-4o
        - --port
        - "8000"
        - --max-loras
        - "2"
        - --lora-modules
        - '{"name": "food-review-1"}'
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        name: vllm-sim
        env:
          - name: POD_NAME
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.name
          - name: POD_NAMESPACE
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.namespace
        ports:
        - containerPort: 8000
          name: http
          protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpt-4o-svc
  namespace: gloo-system
  labels:
    app: mock-gpt-4o
spec:
  selector:
    app: mock-gpt-4o
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
      name: http
  type: ClusterIP
EOF
```

Create mock server route and backend
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai
  namespace: gloo-system
spec:
  parentRefs:
    - name: agentgateway
      namespace: gloo-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: mock-openai
          group: gateway.kgateway.dev
          kind: Backend
      timeouts:
        request: "120s"
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mock-openai
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      host: mock-gpt-4o-svc.gloo-system.svc.cluster.local
      port: 8000
      path:
        full: "/v1/chat/completions"
      openai:
        #--- Uncomment to configure model override ---
        model: "gpt-4o"
        authToken:
          kind: Passthrough
EOF
```

## curl mock openai
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## View all metrics
All metrics
```bash
echo
echo "Objective: curl /metrics endpoint and show all metrics"
kubectl port-forward -n gloo-system deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
``` 

Filter for number of requests served through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for number of requests served through the gateway"
kubectl port-forward -n gloo-system deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_requests_total && kill $!
``` 

Total input and output token usage through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for input/output token usage through the gateway"
kubectl port-forward -n gloo-system deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_gen_ai_client_token_usage_sum && kill $!
``` 
You can tell the difference between the two metrics from the `gen_ai_token_type="input/output"` label

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway -n gloo-system --tail 1 | jq .
```

Example output
```
2025-09-24T06:05:19.901893Z     info    request gateway=gloo-system/agentgateway listener=http route=gloo-system/openai endpoint=api.openai.com:443 src.addr=10.42.0.1:54955 http.method=POST http.host=192.168.107.2 http.path=/openai http.version=HTTP/1.1 http.status=200 trace.id=60488f5d01d8606cfe7ae7f57c20f981 span.id=be198303a1e1a64f llm.provider=openai llm.request.model=gpt-4o-mini llm.request.tokens=12 llm.response.model=gpt-4o-mini llm.response.tokens=46 duration=1669ms
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser, you should be able to see traces for agentgateway that include information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n gloo-system mock-openai
kubectl delete backend -n gloo-system mock-openai
kubectl delete -n gloo-system svc/mock-gpt-4o-svc
kubectl delete -n gloo-system deploy/mock-gpt-4o
```