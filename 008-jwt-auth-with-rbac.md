# Configure JWT Auth for our OpenAI Route

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure JWT Auth
- Validate JWT Auth

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n gloo-system \
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
  namespace: gloo-system
spec:
  parentRefs:
    - name: agentgateway
      namespace: gloo-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai-all-models
          group: gateway.kgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: gloo-system
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
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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

Create agentgateway traffic policy
```bash
kubectl apply -f- <<EOF
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: agentgateway-jwt-auth
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: solo.io
          jwks:
            inline: |
                {
                  "keys": [
                    {
                      "kty": "RSA",
                      "kid": "solo-public-key-001",
                      "n": "vlmc5pb-jYaOq75Y4r91AC2iuS9B0sm6sxzRm3oOG7nIt2F1hHd4AKll2jd6BZg437qvsLdREnbnVrr8kU0drmJNPHL-xbsTz_cQa95GuKb6AI6osAaUAEL3dPjuoqkGNRe1sAJyOi48qtcbV0kPWcwFmCV0-OiqliCms12jrd1PSI_LYiNc3GcutpxY6BiHkbxxNeIuWDxE-i_Obq8EhhGkwha1KVUvLHV-EwD4M_AY8BegGsX-sjoChXOxyueu_ReqWV227I-FTKwMnjwWW0BQkeI6g1w1WqADmtKZ2sLamwGUJgWt4ZgIyhQ-iQfeN1WN2iupTWa5JAsw--CQJw",
                      "e": "AQAB",
                      "use": "sig",
                      "alg": "RS256"
                    }
                  ]
                }
    authorization:
      policy:
        matchExpressions:
          - '(jwt.org == "solo.io") && (jwt.team == "team-id")'
EOF
```

Make a curl request to the OpenAI endpoint again (without a JWT), this time it should fail
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
Verify that the request is denied with a 403 HTTP response code 

## curl with valid JWT token
```bash
export DEV_TOKEN_1="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw"

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $DEV_TOKEN_1" \
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

We should see that we get a response from the backend LLM when JWT is provided
```
{"id":"chatcmpl-CDwyrpA4JiYZtZqykYZoH6a4Ea7hL","choices":[{"index":0,"message":{"content":"I don't have personal preferences, but one widely admired poem is \"The Road Not Taken\" by Robert Frost. It explores themes of choice, individuality, and the paths we take in life. Many find its reflective nature and imagery to be profound. If you're interested, I can provide an analysis or discuss its themes!","role":"assistant"},"finish_reason":"stop"}],"created":1757441021,"model":"gpt-4o-mini-2024-07-18","service_tier":"default","system_fingerprint":"fp_8bda4d3a2c","object":"chat.completion","usage":{"prompt_tokens":12,"completion_tokens":63,"total_tokens":75,"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0},"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0}}}
```

If you decode the JWT, you’ll see that agentgateway successfully verified it and enforced RBAC based on the `jwt.org` and `jwt.group` claims
```
{
  "iss": "solo.io",
  "org": "solo.io",
  "sub": "user-id",
  "team": "team-id",
  "exp": 2079556104,
  "llms": {
    "openai": [
      "gpt-4o"
    ]
  }
}
```

Bonus Exercise:
Update the CEL expression in the GlooTrafficPolicy to experiment with RBAC behavior. For example, adjust the claims in your JWT and resend the request to see when access is allowed or denied:
```
rbac:
    policy:
      matchExpressions:
        - '(jwt.org == "internal") && (jwt.group == "engineering")'
```

## Dynamic JWT Auth


Create agentgateway traffic policy
```bash
kubectl apply -f- <<EOF
---
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: agentgateway-jwt-auth
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: https://integrator-5513662.okta.com/oauth2/ausxkvmeftgcdj6HA697
          jwks:
            remote:
              jwksUri: http://integrator-5513662.okta.com/oauth2/ausxkvmeftgcdj6HA697/v1/keys
            #inline: |
            #  {
            #      "keys": [
            #          {
            #              "kty": "RSA",
            #              "alg": "RS256",
            #              "kid": "TlZ1rm1_htq5wehmOOZLea4ADefV9Fs-sOrHpXtIJHI",
            #              "use": "sig",
            #              "e": "AQAB",
            #              "n": "v-yfaGPLJNV-4PF63Xhxb8D5lVWieNGlt-Ak4WYcpDgfJyx-GmJ6TOgaWBl_AXeIArKyNFnNjo4B6Sj3bJUeov6SceA2M-xW7FGSipq4Jaj5JHyXoWUQS3E7pIjJrJb_9GPIepvhPslS1YbhoxE7WcvdAZExCRZTlABWA7LfJ3PwFEu5DR0HkmeK5d6GG4wLO9znu8bgNBeIqkhgyisAFZx5O5ulLpk8XEhYzCAK5YU0WLyfFlpoE8O52xvwSkAUGxgadY6Py-YLUWzndKzHgeCixRcHkgQ2McPV0aolv0TziJu4maGddbKOhKjzi7vkbMEY_M65AQEmOHloDe0LHw"
            #          }
            #      ]
            #  }
    authorization:
      policy:
        matchExpressions:
          #- '(jwt.org == "solo.io") && (jwt.team == "team-id")'
          - '(jwt.aud == "api://solo")'
EOF
```

## curl with no token
Make a curl request to the OpenAI endpoint again (without a JWT), this time it should fail with `authentication failure: no bearer token found`
```bash
export GATEWAY_IP=$(kubectl get svc -n gloo-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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

## curl with previous JWT token (invalid token)
```bash
export DEV_TOKEN_1="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw"

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $DEV_TOKEN_1" \
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

## curl with valid token
```bash
export VALID_TOKEN="eyJraWQiOiJUbFoxcm0xX2h0cTV3ZWhtT09aTGVhNEFEZWZWOUZzLXNPckhwWHRJSkhJIiwiYWxnIjoiUlMyNTYifQ.eyJ2ZXIiOjEsImp0aSI6IkFULjdwVmdpbl81bGhRa3dNQmJacDA1VXNtOTFLRUlzcmZVdnI1VmNVV2wyR2ciLCJpc3MiOiJodHRwczovL2ludGVncmF0b3ItNTUxMzY2Mi5va3RhLmNvbS9vYXV0aDIvYXVzeGt2bWVmdGdjZGo2SEE2OTciLCJhdWQiOiJhcGk6Ly9zb2xvIiwiaWF0IjoxNzY0MDg2MjU3LCJleHAiOjE3NjQwODk4NTcsImNpZCI6IjBvYXhrc2hrbDJsaDBlOHZjNjk3Iiwic2NwIjpbImFwaS5yZWFkIl0sInN1YiI6IjBvYXhrc2hrbDJsaDBlOHZjNjk3In0.ZgR6uBcR5Eu5sahjHK_FedeohN62--evRtjYLSoSdctuy4kgYZL16RU-nYp1cGQa0fbhUawM3Q1dZMQj490bIX9QqDzxTTl4AYAhWDtSxRNBDOdiKcYRv2ucByW-J0apjfCypDpote1ykjIqT9-XpLP29WRBCC3w-QyDl2MWflCUJQMcM_favog-hZIOO69BjORSUhvLYfuRic9oaOjP1mAp5kM9GQIUuul6kCL3E2OcQWoVjMeWWlkChz1TLibbiDxWMXDi3bqD0s5DHF3zkDdNe6jyI0ovol8PYc4SAzIcUZQB9Qlu_07eVhTZZsEU2iCqArdbG_9p2obaBarBsQ"

curl -i "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $VALID_TOKEN" \
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


## View access logs
Agentgateway enterprise automatically logs information about the LLM request to stdout
```bash
kubectl logs deploy/agentgateway -n gloo-system --tail 1
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

- The request without an JWT should have been rejected with a `http.status` of `403` and an `error` with `authentication failure: no bearer token found`
- The request with a JWT should be successful and you should see information such as `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n gloo-system openai
kubectl delete agentgatewaybackend -n gloo-system openai-all-models
kubectl delete secret -n gloo-system openai-secret
kubectl delete agentgatewayenterprisepolicy -n gloo-system agentgateway-jwt-auth
```