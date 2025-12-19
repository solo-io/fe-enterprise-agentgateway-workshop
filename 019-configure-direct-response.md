# Configure Direct Response Action
In this lab, you'll configure a direct response action that returns a fixed HTTP response without calling a backend LLM. This is useful when you need to quickly override an endpoint's behavior, such as for health checks or temporarily isolating a problematic route

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Configure a direct response using `AgentgatewayPolicy`
- Apply it to an `HTTPRoute`
- Curl the agentgateway endpoint
- Validate the request returns our direct response message

## Create HTTPRoute and Direct Response Policy

Create an HTTPRoute and configure it to return a direct response with status `200` and body `Status: Healthy` using an AgentgatewayPolicy
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: health-check
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /health
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: health-response
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: health-check
  traffic:
    directResponse:
      status: 200
      body: "Status: Healthy"
EOF
```

## curl our agentgateway endpoint
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/health"
```

The response should look similar to below:
```
HTTP/1.1 200 OK
content-length: 15
date: Fri, 21 Nov 2025 18:17:00 GMT

Status: Healthy
```

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 1 | jq .
```

Example output
```
{
  "level": "info",
  "time": "2025-11-21T18:19:00.300505Z",
  "scope": "request",
  "gateway": "enterprise-agentgateway/agentgateway",
  "listener": "http",
  "route": "enterprise-agentgateway/health-check",
  "src.addr": "10.42.0.1:18982",
  "http.method": "GET",
  "http.host": "192.168.107.2",
  "http.path": "/health",
  "http.version": "HTTP/1.1",
  "http.status": 200,
  "trace.id": "6c04dab11b16c77aea2e22563ed2b60c",
  "span.id": "9df7aaf95d1a271f",
  "duration": "0ms"
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

Navigate to http://localhost:3000 or http://localhost:16686 in your browser, you should be able to see traces for agentgateway

## Cleanup
```bash
kubectl delete agentgatewaypolicy -n enterprise-agentgateway health-response
kubectl delete httproute -n enterprise-agentgateway health-check
```
