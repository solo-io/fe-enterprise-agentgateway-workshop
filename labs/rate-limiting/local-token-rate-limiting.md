# Configure Local Input Token Based Rate Limiting

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `EnterpriseAgentgatewayBackend` and `HTTPRoute`
- Create a local rate limit policy to implement token-based rate limiting (input tokens) counting LLM tokens per replica (e.g. 5 tokens per minute)
- Validate token-based rate limiting

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
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
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
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

## Configure Local token-based rate limit of 5 input tokens per minute
The following policy will allow 5 tokens per minute
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: local-token-based-rate-limit
  namespace: agentgateway-system
spec:
  targetRefs:
    - name: agentgateway-proxy
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    rateLimit:
      local:
        - unit: Minutes
          tokens: 5
          burst: 0
EOF
```

## curl openai until the limit trips
Note that the following user prompt "Whats your favorite poem" contains 5 tokens based on the [OpenAI tokenizer](https://platform.openai.com/tokenizer), so a single request consumes the whole minute's budget.

Local limits are enforced per proxy replica, and `001` deploys two, so each replica carries its own 5-token budget. Send a short burst so every replica is hit:

```bash
for i in {1..6}; do
  echo -n "request $i: "
  curl -s -o /dev/null -w "HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
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
done
```

The first request to each replica returns `HTTP 200` and consumes its 5-token budget; every later request in the same minute returns `HTTP 429`.

## Local vs. Global Rate Limiting
Local rate limiting shown in this lab is enforced directly on each proxy, with every replica maintaining its own independent counter. This makes it useful as a coarse-grained, first line of defense to shed excess traffic before it reaches backend services or global rate limit servers.

Global rate limiting, by contrast, is enforced by a central service that all proxies consult. This allows requests across all proxies and replicas to share the same counter, enabling consistent, tenant-wide quotas and more fine-grained policies. Global limits can also incorporate request metadata such as headers or JWT claims for advanced API management scenarios.

Next, we’ll explore how to configure global rate limiting using the Enterprise Rate Limit server.


## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system local-token-based-rate-limit
```