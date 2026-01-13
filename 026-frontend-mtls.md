# Frontend mTLS Termination

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Create self-signed mTLS certificates
- Configure the agentgateway to terminate mTLS
- Create a route to OpenAI protected by mTLS
- Validate connectivity requires a valid client certificate

## Create self-signed TLS certificates

Create a root certificate for the glootest.com domain. You use this certificate to sign the certificate for your client and gateway later.
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

Create a ConfigMap to store the CA certificate for mTLS client validation.
```bash
kubectl create configmap -n enterprise-agentgateway ca-cert \
  --from-file=ca.crt=example_certs/glootest.com.crt
```

Create a client certificate and private key. You use these credentials later when sending a request to the gateway proxy. The client certificate is signed with the same root CA certificate that you used for the gateway proxy.
```bash
openssl req -out example_certs/client.glootest.com.csr -newkey rsa:2048 -nodes -keyout example_certs/client.glootest.com.key -subj "/CN=client.glootest.com/O=client organization"

openssl x509 -req -sha256 -days 365 -CA example_certs/glootest.com.crt -CAkey example_certs/glootest.com.key -set_serial 1 -in example_certs/client.glootest.com.csr -out example_certs/client.glootest.com.crt
```

## Configure the gateway to terminate mTLS

Configure the gateway with frontend TLS validation to require client certificates. The `spec.tls.frontend.default.validation` section enables mTLS by referencing the CA certificate ConfigMap.

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
  tls:
    frontend:
      default:
        validation:
          mode: AllowValidOnly
          caCertificateRefs:
            - name: ca-cert
              kind: ConfigMap
              group: ""
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

## Validate OpenAI access without a client mTLS certificate

curl OpenAI without a client cert, this should fail with a TLS handshake error
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -ikv "https://$GATEWAY_IP/openai" \
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
* TLSv1.3 (OUT), TLS handshake, Client hello (1):
* TLSv1.3 (IN), TLS handshake, Server hello (2):
* TLSv1.3 (IN), TLS handshake, Encrypted Extensions (8):
* TLSv1.3 (IN), TLS handshake, Request CERT (13):
* TLSv1.3 (IN), TLS handshake, Certificate (11):
* TLSv1.3 (IN), TLS handshake, CERT verify (15):
* TLSv1.3 (IN), TLS handshake, Finished (20):
* TLSv1.3 (OUT), TLS change cipher, Change cipher spec (1):
* TLSv1.3 (OUT), TLS handshake, Certificate (11):
* TLSv1.3 (OUT), TLS handshake, Finished (20):
* TLSv1.3 (IN), TLS alert, unknown (628):
* OpenSSL/3.0.2: error:0A00045C:SSL routines::tlsv13 alert certificate required
* Closing connection
curl: (35) OpenSSL/3.0.2: error:0A00045C:SSL routines::tlsv13 alert certificate required
```

## Validate OpenAI access with a trusted client mTLS certificate

curl OpenAI with the valid client cert that we created earlier, this should succeed
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
  --cert example_certs/client.glootest.com.crt \
  --key example_certs/client.glootest.com.key \
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
kubectl delete configmap -n enterprise-agentgateway ca-cert
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
