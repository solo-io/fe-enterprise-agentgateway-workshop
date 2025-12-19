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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mock-openai
  namespace: gloo-system
spec:
  ai:
    provider:
      openai:
        model: "gpt-4o"
      host: mock-gpt-4o-svc.gloo-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth:
      passthrough: {}
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
{
  "level": "info",
  "time": "2025-12-19T06:23:49.655336Z",
  "scope": "request",
  "gateway": "gloo-system/agentgateway",
  "listener": "http",
  "route": "gloo-system/mock-openai",
  "endpoint": "mock-gpt-4o-svc.gloo-system.svc.cluster.local:8000",
  "src.addr": "10.42.0.1:52000",
  "http.method": "POST",
  "http.host": "192.168.107.2",
  "http.path": "/openai",
  "http.version": "HTTP/1.1",
  "http.status": 200,
  "trace.id": "42d8b4df6a37562a3acfaabde69a16a8",
  "span.id": "c8bcc8a3f650398e",
  "protocol": "llm",
  "gen_ai.operation.name": "chat",
  "gen_ai.provider.name": "openai",
  "gen_ai.request.model": "gpt-4o",
  "gen_ai.response.model": "gpt-4o",
  "gen_ai.usage.input_tokens": 5,
  "gen_ai.usage.output_tokens": 31,
  "duration": "0ms",
  "request.body": {
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  },
  "response.body": {
    "do_remote_decode": false,
    "remote_block_ids": null,
    "choices": [
      {
        "index": 0,
        "message": {
          "content": "I am your AI assistant, how can I help you today? Give a man a fish and you feed him for a day; teach a man to fish ",
          "role": "assistant"
        },
        "finish_reason": "stop"
      }
    ],
    "created": 1766125429,
    "usage": {
      "completion_tokens": 31,
      "prompt_tokens": 5,
      "total_tokens": 36
    },
    "remote_port": 0,
    "do_remote_prefill": false,
    "object": "chat.completion",
    "id": "chatcmpl-f58ffb8c-95fc-4f78-8c66-9de4f55a0f58",
    "remote_engine_id": "",
    "remote_host": "",
    "model": "gpt-4o"
  },
  "rq.headers.all": {
    "accept": "*/*",
    "content-length": "144",
    "user-agent": "curl/8.7.1",
    "content-type": "application/json"
  }
}
```

## Port-forward to Grafana UI to view traces
Default credentials are admin:prom-operator
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

## Port-forward to Jaeger UI to view traces
```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:3000 or http://localhost:16686 in your browser, you should be able to see traces for agentgateway that include information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n gloo-system mock-openai
kubectl delete agentgatewaybackend -n gloo-system mock-openai
kubectl delete -n gloo-system svc/mock-gpt-4o-svc
kubectl delete -n gloo-system deploy/mock-gpt-4o
```