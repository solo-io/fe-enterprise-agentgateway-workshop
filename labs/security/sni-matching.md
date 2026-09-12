# SNI Matching
In this guide, you learn how to set up an HTTPS Gateway that serves two different domains, `mock-openai-foo.try-solo.io` and `mock-openai-bar.try-solo.io` on the same port 443. When sending a request to the Gateway, you indicate the hostname you want to connect to. Based on the selected hostname, the Gateway presents the hostname-specific certificate.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy a mock OpenAI server for testing
- Create self-signed TLS certs for `mock-openai-foo.try-solo.io` and `mock-openai-bar.try-solo.io`
- Configure our gateway to terminate TLS with SNI matching
- Validate connectivity to the mock OpenAI server over HTTPS
- Validate that requests without matching SNI are rejected

## References
- [AgentGateway Docs - TLS](https://docs.solo.io/agentgateway/latest/)

## Deploy Mock OpenAI Server

Deploy the mock server using the manifest below. This mock server provides a lightweight implementation of the OpenAI-compatible `/v1/chat/completions` endpoint.

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
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
        - mock-gpt-4o
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
  namespace: agentgateway-system
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

Create the EnterpriseAgentgatewayBackend for the mock OpenAI server
```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: mock-openai
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-4o"
      host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth:
      passthrough: {}
EOF
```

## Create a self-signed TLS cert

Create a root certificate for the try-solo.io domain. You use this certificate to sign the certificate for your client and gateway later.
```bash
mkdir example_certs
openssl req -x509 -sha256 -nodes -days 365 -newkey rsa:2048 -subj '/O=Solo.io/CN=try-solo.io' -keyout example_certs/try-solo.io.key -out example_certs/try-solo.io.crt
```

Create a gateway certificate that is signed by the root CA certificate that you created in the previous step.

First for mock-openai-foo.try-solo.io
```bash
openssl req -out example_certs/mock-openai-foo.try-solo.io.csr -newkey rsa:2048 -nodes -keyout example_certs/mock-openai-foo.try-solo.io.key -subj "/CN=mock-openai-foo.try-solo.io/O=mock-openai organization"

openssl x509 -req -sha256 -days 365 -CA example_certs/try-solo.io.crt -CAkey example_certs/try-solo.io.key -set_serial 0 -in example_certs/mock-openai-foo.try-solo.io.csr -out example_certs/mock-openai-foo.try-solo.io.crt
```

Then for mock-openai-bar.try-solo.io
```bash
openssl req -out example_certs/mock-openai-bar.try-solo.io.csr -newkey rsa:2048 -nodes -keyout example_certs/mock-openai-bar.try-solo.io.key -subj "/CN=mock-openai-bar.try-solo.io/O=solo.io"

openssl x509 -req -sha256 -days 365 -CA example_certs/try-solo.io.crt -CAkey example_certs/try-solo.io.key -set_serial 1 -in example_certs/mock-openai-bar.try-solo.io.csr -out example_certs/mock-openai-bar.try-solo.io.crt
```

Store the credentials for the mock-openai-foo.try-solo.io domain in a Kubernetes secret.
```bash
kubectl create -n agentgateway-system secret tls foo \
--key=example_certs/mock-openai-foo.try-solo.io.key \
--cert=example_certs/mock-openai-foo.try-solo.io.crt

kubectl create -n agentgateway-system secret tls bar \
--key=example_certs/mock-openai-bar.try-solo.io.key \
--cert=example_certs/mock-openai-bar.try-solo.io.crt
```

## Set up SNI Routing

Set up an SNI Gateway that serves multiple hosts on the same port.

> [!NOTE]
> This creates a **separate** `agentgateway-sni` Gateway rather than editing the
> `agentgateway-proxy` Gateway from `001`. Applying a full Gateway manifest over
> the shared one would replace its listener list, dropping the HTTP `8080`
> listener the other labs route through, and detach its
> `infrastructure.parametersRef`. A dedicated Gateway also gets its own
> LoadBalancer address, which is what the SNI checks below resolve against.

```bash
kubectl apply -f - <<EOF
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-sni
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  listeners:
    - protocol: HTTPS
      port: 443
      name: foo
      hostname: mock-openai-foo.try-solo.io
      tls:
        mode: Terminate
        certificateRefs:
          - name: foo
            kind: Secret
      allowedRoutes:
        namespaces:
          from: All
    - protocol: HTTPS
      port: 443
      name: bar
      hostname: "mock-openai-bar.try-solo.io"
      tls:
        mode: Terminate
        certificateRefs:
          - name: bar
            kind: Secret
      allowedRoutes:
        namespaces:
          from: All
EOF
```

Next create our HTTPRoutes. Here we are going to create `mock-openai-foo`, `mock-openai-bar`, and `mock-openai-baz`. We should expect:
- A request to `mock-openai-foo` and `mock-openai-bar` will succeed
- A request to `mock-openai-baz` will fail TLS

```bash
kubectl apply -f - <<EOF
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai-foo-route
  namespace: agentgateway-system
spec:
  hostnames:
  - "mock-openai-foo.try-solo.io"
  parentRefs:
    - name: agentgateway-sni
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: mock-openai
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai-bar-route
  namespace: agentgateway-system
spec:
  hostnames:
  - "mock-openai-bar.try-solo.io"
  parentRefs:
    - name: agentgateway-sni
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: mock-openai
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai-baz-route
  namespace: agentgateway-system
spec:
  hostnames:
  - "mock-openai-baz.try-solo.io"
  parentRefs:
    - name: agentgateway-sni
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: mock-openai
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

## Validate connectivity to the application over HTTPS

curl mock-openai-foo over https:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-sni -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -ikv --resolve "mock-openai-foo.try-solo.io:443:${GATEWAY_IP}" https://mock-openai-foo.try-solo.io:443/ \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

We can see the TLS handshake occurring
```
* Connected to mock-openai-foo.try-solo.io (192.168.64.2) port 443
* ALPN: curl offers h2,http/1.1
* (304) (OUT), TLS handshake, Client hello (1):
* (304) (IN), TLS handshake, Server hello (2):
* (304) (IN), TLS handshake, Unknown (8):
* (304) (IN), TLS handshake, Certificate (11):
* (304) (IN), TLS handshake, CERT verify (15):
* (304) (IN), TLS handshake, Finished (20):
* (304) (OUT), TLS handshake, Finished (20):
* SSL connection using TLSv1.3 / AEAD-AES256-GCM-SHA384 / [blank] / UNDEF
* ALPN: server accepted h2
* Server certificate:
*  subject: CN=mock-openai-foo.try-solo.io; O=mock-openai organization
*  start date: Jan 23 18:46:30 2026 GMT
*  expire date: Jan 23 18:46:30 2027 GMT
*  issuer: O=Solo.io; CN=try-solo.io
```

curl mock-openai-bar over https:
```bash
curl -ikv --resolve "mock-openai-bar.try-solo.io:443:${GATEWAY_IP}" https://mock-openai-bar.try-solo.io:443/ \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

Again we can see the TLS handshake occurring, but this time for `mock-openai-bar.try-solo.io`
```
* Connected to mock-openai-bar.try-solo.io (192.168.64.2) port 443
* ALPN: curl offers h2,http/1.1
* (304) (OUT), TLS handshake, Client hello (1):
* (304) (IN), TLS handshake, Server hello (2):
* (304) (IN), TLS handshake, Unknown (8):
* (304) (IN), TLS handshake, Certificate (11):
* (304) (IN), TLS handshake, CERT verify (15):
* (304) (IN), TLS handshake, Finished (20):
* (304) (OUT), TLS handshake, Finished (20):
* SSL connection using TLSv1.3 / AEAD-AES256-GCM-SHA384 / [blank] / UNDEF
* ALPN: server accepted h2
* Server certificate:
*  subject: CN=mock-openai-bar.try-solo.io; O=solo.io
*  start date: Jan 23 18:46:35 2026 GMT
*  expire date: Jan 23 18:46:35 2027 GMT
*  issuer: O=Solo.io; CN=try-solo.io
```

Now curl mock-openai-baz over https:
```bash
curl -ikv --resolve "mock-openai-baz.try-solo.io:443:${GATEWAY_IP}" https://mock-openai-baz.try-solo.io:443/ \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

Although this is a valid route, this request should fail
```
* (304) (OUT), TLS handshake, Client hello (1):
* LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to mock-openai-baz.try-solo.io:443 
* Closing connection
curl: (35) LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to mock-openai-baz.try-solo.io:443
```

✅ The TCP connection was accepted
✅ ClientHello was sent (with SNI)
❌ Envoy immediately reset the connection
❌ TLS never reached certificate negotiation
❌ HTTP never happened


## Cleanup

Clean up objects created in this lab
```bash
rm -rf example_certs
kubectl delete gateway -n agentgateway-system agentgateway-sni
kubectl delete httproute -n agentgateway-system mock-openai-bar-route
kubectl delete httproute -n agentgateway-system mock-openai-baz-route
kubectl delete httproute -n agentgateway-system mock-openai-foo-route
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system mock-openai
kubectl delete secret -n agentgateway-system foo
kubectl delete secret -n agentgateway-system bar
kubectl delete -n agentgateway-system svc/mock-gpt-4o-svc
kubectl delete -n agentgateway-system deploy/mock-gpt-4o
```

The `agentgateway-proxy` Gateway from `001` is untouched by this lab, so nothing
needs to be restored.
