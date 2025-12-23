# Configure Basic Routing to Vertex AI

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our Vertex AI OAuth credentials
- Create a route to Vertex AI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Curl Vertex AI through the agentgateway proxy
- Validate the request went through the gateway in Grafana/Jaeger UI

### Configure Required Variables

Set the following environment variables to match your GCP Vertex AI project.

**Note:** This demo uses the currently active `gcloud auth login` user identity to mint an OAuth access token for routing requests to Vertex AI through the AI Gateway.

```bash
export GCP_PROJECT_ID="<YOUR-GCP-PROJECT-ID>"
export GCP_REGION="us-central1"  # or your preferred region
```

Retrieve an OAuth access token using gcloud:
```bash
export VERTEXAI_ACCESS_TOKEN=$(gcloud auth print-access-token)
```

Create vertex ai oauth secret
```bash
kubectl create secret generic vertex-ai-secret -n enterprise-agentgateway \
  --from-literal="Authorization=Bearer $VERTEXAI_ACCESS_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Create vertex ai route and backend
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: vertex-ai
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /vertex
      backendRefs:
        - name: vertex-ai
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: vertex-ai
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      vertexai:
        model: "google/gemini-2.5-flash-lite"
        projectId: "${GCP_PROJECT_ID}"
        region: "${GCP_REGION}"
  policies:
    auth:
      secretRef:
        name: vertex-ai-secret
EOF
```

## curl vertex ai
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/vertex" \
  -H "content-type: application/json" \
  -d '{
    "model": "google/gemini-2.5-flash-lite",
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
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

Filter for number of requests served through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for number of requests served through the gateway"
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_requests_total && kill $!
```

Total input and output token usage through the gateway
```bash
echo
echo "Objective: curl /metrics endpoint and filter for input/output token usage through the gateway"
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics | grep agentgateway_gen_ai_client_token_usage_sum && kill $!
```
You can tell the difference between the two metrics from the `gen_ai_token_type="input/output"` label

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 1
```

Example output
```
2025-12-19T00:47:17.454755Z     info    request gateway=enterprise-agentgateway/agentgateway listener=http route=enterprise-agentgateway/vertex-ai endpoint=us-central1-aiplatform.googleapis.com:443 src.addr=10.42.0.1:4478 http.method=POST http.host=192.168.107.2 http.path=/vertex http.version=HTTP/1.1 http.status=200 protocol=llm gen_ai.operation.name=chat gen_ai.provider.name=vertexai gen_ai.request.model=google/gemini-2.5-flash-lite gen_ai.response.model=google/gemini-2.5-flash-lite gen_ai.usage.input_tokens=12 gen_ai.usage.output_tokens=52 duration=2163ms
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
kubectl delete httproute -n enterprise-agentgateway vertex-ai
kubectl delete agentgatewaybackend -n enterprise-agentgateway vertex-ai
kubectl delete secret -n enterprise-agentgateway vertex-ai-secret
```
