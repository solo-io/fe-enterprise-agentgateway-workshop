# Frontend TLS Termination

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Create self-signed TLS certificates
- Configure the agentgateway to terminate TLS
- Create a route to OpenAI protected by TLS
- Validate that HTTP traffic is blocked
- Validate connectivity over HTTPS

## Create self-signed TLS certificates

Create a root certificate for the glootest.com domain. You use this certificate to sign the certificate for your gateway.
```bash
mkdir example_certs
openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 -subj '/O=Solo.io/CN=glootest.com' -keyout example_certs/glootest.com.key -out example_certs/glootest.com.crt
```

Create a gateway certificate that is signed by the root CA certificate that you created in the previous step.
```bash
openssl req -out example_certs/gateway.csr -newkey rsa:2048 -nodes -keyout example_certs/gateway.key -subj "/CN=*/O=any domain"

openssl x509 -req -sha256 -days 365 -CA example_certs/glootest.com.crt -CAkey example_certs/glootest.com.key -set_serial 0 -in example_certs/gateway.csr -out example_certs/gateway.crt
```

Create a Kubernetes secret to store your gateway TLS certificate.
```bash
kubectl create secret tls -n enterprise-agentgateway https \
  --key example_certs/gateway.key \
  --cert example_certs/gateway.crt
```

## Configure the gateway to terminate TLS

Configure the gateway with HTTPS listener to terminate TLS connections.

```bash
kubectl apply -f - <<EOF
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: enterprise-agentgateway
spec:
  gatewayClassName: enterprise-agentgateway
  listeners:
    - protocol: HTTPS
      port: 443
      name: https
      tls:
        mode: Terminate
        certificateRefs:
          - name: https
            kind: Secret
      allowedRoutes:
        namespaces:
          from: All
EOF
```

## Configure OpenAI Route

### Configure Required Variables
Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create OpenAI api-key secret
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create OpenAI route and backend
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai-all-models
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

## Validate HTTP traffic is blocked

Try to access the gateway over HTTP without TLS. This should fail because the gateway only accepts HTTPS connections on port 443.

```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -ik "http://$GATEWAY_IP/openai" \
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

Expected output (connection should fail):
```
curl: (7) Failed to connect to 192.168.64.2 port 80 after 5 ms: Couldn't connect to server
```

## Validate OpenAI access over HTTPS

curl OpenAI over HTTPS using the gateway certificate
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -ik "https://$GATEWAY_IP/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }' \
  --cacert example_certs/gateway.crt
```

Expected output (should succeed with HTTP 200 and a poem response):
```
HTTP/2 200
content-type: application/json
...

{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1736736000,
  "model": "gpt-4o-mini-2024-07-18",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "One of my favorite poems is \"The Road Not Taken\" by Robert Frost..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 50,
    "total_tokens": 65
  }
}
```

## Observability

### View Metrics in Grafana

Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

Open http://localhost:3000 in your browser and navigate to **Dashboards > AgentGateway Dashboard** to view:
- Request rates and token usage by model
- Streaming metrics (TTFT, TPOT)
- Connection and runtime metrics

### View Access Logs

AgentGateway logs detailed information about LLM requests:
```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 5
```

## Cleanup
```bash
rm -rf example_certs
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret https
```

Restore the default Gateway from lab `001`
```bash
kubectl apply -f - <<EOF
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: enterprise-agentgateway
spec:
  gatewayClassName: enterprise-agentgateway
  listeners:
    - name: http
      port: 8080
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: All
EOF
```
