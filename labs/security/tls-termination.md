# Frontend TLS Termination

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

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
kubectl create secret tls -n agentgateway-system https \
  --key example_certs/gateway.key \
  --cert example_certs/gateway.crt
```

## Configure the gateway to terminate TLS

Add an HTTPS listener to the `agentgateway-proxy` Gateway from `001` so it terminates TLS on port 443 alongside the plaintext listener it already serves on 8080. This is how you would roll TLS out in front of an existing gateway.

Append the listener with a **JSON patch** rather than `kubectl apply`. A full `apply` replaces the whole object, so it would drop any field `001` set that this manifest does not restate — including `infrastructure.parametersRef`, which supplies the proxy's replica count, logging, and model catalog. The patch below adds one list entry and touches nothing else.

```bash
kubectl patch gateway agentgateway-proxy -n agentgateway-system --type=json -p '[
  {
    "op": "add",
    "path": "/spec/listeners/-",
    "value": {
      "name": "https",
      "port": 443,
      "protocol": "HTTPS",
      "tls": {
        "mode": "Terminate",
        "certificateRefs": [
          {
            "name": "https",
            "kind": "Secret"
          }
        ]
      },
      "allowedRoutes": {
        "namespaces": {
          "from": "All"
        }
      }
    }
  }
]'
```

Confirm both listeners are present and the parameters reference survived:
```bash
kubectl get gateway agentgateway-proxy -n agentgateway-system \
  -o jsonpath='listeners={.spec.listeners[*].port} params={.spec.infrastructure.parametersRef.name}{"\n"}'
```

Expected output:
```
listeners=8080 443 params=agentgateway-config
```

## Configure OpenAI Route

### Configure Required Variables
Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create OpenAI api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
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
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
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

Try to access the route over plaintext HTTP on port 80. This fails because the gateway has no listener there — TLS traffic is served on 443, and the plaintext listener from `001` is on 8080.

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -ik "http://$GATEWAY_IP/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
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
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -ik "https://$GATEWAY_IP/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-5.4-nano",
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
  "model": "gpt-5.4-nano-2026-03-17",
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
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

## Cleanup
```bash
rm -rf example_certs
kubectl delete httproute -n agentgateway-system openai
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret https
```

Remove the HTTPS listener, leaving the rest of the Gateway from `001` as it was:
```bash
kubectl patch gateway agentgateway-proxy -n agentgateway-system --type=json -p '[
  {
    "op": "remove",
    "path": "/spec/listeners/1"
  }
]'
```

Verify the baseline is back to a single plaintext listener with its parameters still attached:
```bash
kubectl get gateway agentgateway-proxy -n agentgateway-system \
  -o jsonpath='listeners={.spec.listeners[*].port} params={.spec.infrastructure.parametersRef.name}{"\n"}'
```
