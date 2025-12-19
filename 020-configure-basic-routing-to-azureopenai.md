# Configure Basic Routing to Azure OpenAI

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our Azure OpenAI api-key credentials
- Create a route to Azure OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Curl Azure OpenAI through the agentgateway proxy
- Validate the request went through the gateway in Grafana/Jaeger UI

### Configure Required Variables

Set the following environment variables to match your Azure OpenAI deployment.
For reference, an endpoint typically follows this format:
`https://${ENDPOINT}/openai/deployments/${DEPLOYMENT_NAME}/chat/completions?api-version=2024-02-01`

**Note:** The ENDPOINT should be just the hostname without the `https://` scheme (e.g., `my-endpoint.openai.azure.com`)

```bash
export AZURE_OPENAI_API_KEY="<API-KEY>"
export ENDPOINT="<AZURE-OPENAI-ENDPOINT>"  # Just the hostname, no https://
export DEPLOYMENT_NAME="<DEPLOYMENT-NAME>"
```

Create azure openai api-key secret
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: azureopenai-secret
  namespace: enterprise-agentgateway # Putting in same ns where the redis, ext auth is getting deployed
type: Opaque
stringData:
  Authorization: "Bearer ${AZURE_OPENAI_API_KEY}"
EOF
```

Create azure openai route and backend
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: azure-openai
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /azure
      backendRefs:
        - name: azure-openai
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: azure-openai
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      azureopenai:
        endpoint: "${ENDPOINT}"
        deploymentName: "${DEPLOYMENT_NAME}"
  policies:
    auth:
      secretRef:
        name: azureopenai-secret
EOF
```

## curl azure openai
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/azure" \
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
2025-09-24T06:05:19.901893Z     info    request gateway=enterprise-agentgateway/gloo-agentgateway listener=http route=enterprise-agentgateway/openai endpoint=api.openai.com:443 src.addr=10.42.0.1:54955 http.method=POST http.host=192.168.107.2 http.path=/openai http.version=HTTP/1.1 http.status=200 trace.id=60488f5d01d8606cfe7ae7f57c20f981 span.id=be198303a1e1a64f llm.provider=openai llm.request.model=gpt-4o-mini llm.request.tokens=12 llm.response.model=gpt-4o-mini-2024-07-18 llm.response.tokens=46 duration=1669ms
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
kubectl delete httproute -n enterprise-agentgateway azure-openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway azure-openai
kubectl delete secret -n enterprise-agentgateway azureopenai-secret
```