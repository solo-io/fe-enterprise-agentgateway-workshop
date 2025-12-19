# Configure Local Input Token Based Rate Limiting

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Create an Local rate limit policy to implement token-based rate limiting (input tokens) using a simple counter (e.g. all users get 10 tokens per hour)
- Validate token-based rate limiting

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and backend
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
        #--- Uncomment to configure model override ---
        #model: ""
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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

## Configure Local token-based rate limit of 10 input tokens per hour
The following policy will allow 1 token per 100s
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: local-token-based-rate-limit
  namespace: enterprise-agentgateway
spec:
  targetRefs:
    - name: agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    rateLimit:
      local:
        - unit: Minutes
          requests: 1
          burst: 0          
EOF
```

## curl openai
Note that the following user prompt "Whats your favorite poem" contains 5 tokens based on the [OpenAI tokenizer](https://platform.openai.com/tokenizer)
```bash
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
You should be rate limited on the second request to LLM because we will have hit our token-based rate limit of 1 input tokens per 100s

## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 1
```

Example output, you should see that the `http.status=429`
```
2025-10-20T17:12:35.122531Z     info    request gateway=enterprise-agentgateway/gloo-agentgateway listener=http route=enterprise-agentgateway/openai src.addr=10.42.0.1:42671 http.method=POST http.host=192.168.107.2 http.path=/openai http.version=HTTP/1.1 http.status=429 trace.id=3ad6e9fbc49d0ec2dceda4ec85d411f8 span.id=df920a4246c1b338 error="rate limit exceeded" duration=0ms
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

Navigate to http://localhost:3000 or http://localhost:16686 in your browser, you should be able to see traces for our recent requests

- The rate limited requests should have been rejected with a `http.status` of `429`

## Local vs. Global Rate Limiting
Local rate limiting shown in this lab is enforced directly on each proxy, with every replica maintaining its own independent counter. This makes it useful as a coarse-grained, first line of defense to shed excess traffic before it reaches backend services or global rate limit servers.

Global rate limiting, by contrast, is enforced by a central service that all proxies consult. This allows requests across all proxies and replicas to share the same counter, enabling consistent, tenant-wide quotas and more fine-grained policies. Global limits can also incorporate request metadata such as headers or JWT claims for advanced API management scenarios.

Next, we’ll explore how to configure global rate limiting using the Gloo Rate Limit server.


## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret
kubectl delete enterpriseagentgatewaypolicy -n enterprise-agentgateway local-token-based-rate-limit
```