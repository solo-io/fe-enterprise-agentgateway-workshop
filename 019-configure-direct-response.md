# Configure Direct Response Action
In this lab, you’ll configure a direct response action that returns a fixed HTTP response without calling a backend LLM. This is useful when you need to quickly override an endpoint’s behavior, such as for health checks or temporarily isolating a problematic route

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Configure a `DirectResponse` CRD
- Use it in our `HTTPRoute`
- Curl the agentgateway endpoint
- Validate the request returns our direct response message

## Create our direct response

Return a status `200` and a response body `Status: Healthy`
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.kgateway.dev/v1alpha1
kind: DirectResponse
metadata:
  name: health-response
  namespace: gloo-system
spec:
  status: 200
  body: "Status: Healthy"
EOF
```

Apply the HTTPRoute
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: agentgateway
  namespace: gloo-system
spec:
  parentRefs:
    - name: agentgateway
      namespace: gloo-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      filters:
        - type: ExtensionRef
          extensionRef:
            group: gateway.kgateway.dev
            kind: DirectResponse
            name: health-response
EOF
```

## curl our agentgateway endpoint
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080" \
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
kubectl logs deploy/agentgateway -n gloo-system --tail 1 | jq .
```

Example output
```
{
  "level": "info",
  "time": "2025-11-21T18:19:00.300505Z",
  "scope": "request",
  "gateway": "gloo-system/agentgateway",
  "listener": "http",
  "route": "gloo-system/agentgateway",
  "src.addr": "10.42.0.1:18982",
  "http.method": "POST",
  "http.host": "192.168.107.2",
  "http.path": "/",
  "http.version": "HTTP/1.1",
  "http.status": 200,
  "trace.id": "6c04dab11b16c77aea2e22563ed2b60c",
  "span.id": "9df7aaf95d1a271f",
  "duration": "0ms",
  "rq.headers.all": {
    "content-length": "144",
    "user-agent": "curl/8.7.1",
    "content-type": "application/json",
    "accept": "*/*"
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
kubectl delete httproute -n gloo-system agentgateway
kubectl delete directresponses -n gloo-system health-response
```