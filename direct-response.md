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
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
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
  namespace: agentgateway-system
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
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/health"
```

The response should look similar to below:
```
HTTP/1.1 200 OK
content-length: 15
date: Fri, 21 Nov 2025 18:17:00 GMT

Status: Healthy
```

## Cleanup
```bash
kubectl delete agentgatewaypolicy -n agentgateway-system health-response
kubectl delete httproute -n agentgateway-system health-check
```
