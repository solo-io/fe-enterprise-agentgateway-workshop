# Configure JWT Auth for our OpenAI Route

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Configure JWT Auth
- Validate JWT Auth

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and `AgentgatewayBackend`
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
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
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
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: agentgateway-jwt-auth
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
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
Update the CEL expression in the EnterpriseAgentgatewayPolicy to experiment with RBAC behavior. For example, adjust the claims in your JWT and resend the request to see when access is allowed or denied:
```
rbac:
    policy:
      matchExpressions:
        - '(jwt.org == "internal") && (jwt.group == "engineering")'
```

## Dynamic JWT Auth


Create an AgentgatewayBackend for the Okta JWKS endpoint
```bash
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: okta-jwks
  namespace: agentgateway-system
spec:
  static:
    host: integrator-5513662.okta.com
    port: 443
  policies:
    tls: {}
EOF
```

Create agentgateway traffic policy
```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: agentgateway-jwt-auth
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: https://integrator-5513662.okta.com/oauth2/ausxkvmeftgcdj6HA697
          jwks:
            remote:
              backendRef:
                name: okta-jwks
                namespace: agentgateway-system
                kind: AgentgatewayBackend
                group: agentgateway.dev
              jwksPath: /oauth2/ausxkvmeftgcdj6HA697/v1/keys
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
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

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
export VALID_TOKEN="eyJraWQiOiJUbFoxcm0xX2h0cTV3ZWhtT09aTGVhNEFEZWZWOUZzLXNPckhwWHRJSkhJIiwiYWxnIjoiUlMyNTYifQ.eyJ2ZXIiOjEsImp0aSI6IkFULjRzb1A3MmlBelljdEFrUlhLVjYtMXVDbW9TSDlqZkJDU1IxelFoYjdON00iLCJpc3MiOiJodHRwczovL2ludGVncmF0b3ItNTUxMzY2Mi5va3RhLmNvbS9vYXV0aDIvYXVzeGt2bWVmdGdjZGo2SEE2OTciLCJhdWQiOiJhcGk6Ly9zb2xvIiwiaWF0IjoxNzY1NDA0Mjg3LCJleHAiOjE3NjU0MDc4ODcsImNpZCI6IjBvYXhrc2hrbDJsaDBlOHZjNjk3Iiwic2NwIjpbImFwaS5yZWFkIl0sInN1YiI6IjBvYXhrc2hrbDJsaDBlOHZjNjk3In0.kLfNG5OzT8hKTVgEJ2B3DYu9D3xL6hPwcXDRTt6N5vy5FW4BELyHeBZX1pRLdzVEvyKP2gDgP2H0pNdQgwk8A6lopnRtZv47SG2t3FWDeGUEj9PKQn-R1bZRrECg6vBsLgt71g-Ph3YD3PUxb97-hgNLk6U-baLXHC3rOAc_SLvQj7FWKuhE9NTkTGIqpJdfiiLiDa3Amda1QaJTZaZRHv3bkvfn-8sDCN5mqOZ4KHKzkM3C6q9WZkK0Cbr0oeqn-t1q8g4GjoF3n-fM7XDYAiywdU4zOZ9c1W4aujYgdVTBP0XNXA9fNipMTDL8pv0irSrDgAT4oyzR7tL7FlFjNQ"

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

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout:

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 1
```

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models
kubectl delete agentgatewaybackend -n agentgateway-system okta-jwks
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system agentgateway-jwt-auth
```