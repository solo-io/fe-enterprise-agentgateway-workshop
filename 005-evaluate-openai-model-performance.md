# Evaluate OpenAI Model Performance with Promptfoo

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Install promptfoo on your local machine
- Run evaluations

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
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

## Install promptfoo

Using brew:
```bash
brew install promptfoo
```
For other installation methods, see: https://promptfoo.dev/docs/installation

Open up the promptfoo UI in another terminal
```bash
promptfoo view -y
```

Set the OpenAI base URL for promptfoo
```bash
export OPENAI_BASE_URL="http://$GATEWAY_IP:8080/openai"
```

Run a model eval for coding tasks using llm-as-a-judge and confidence scoring assertions
```bash
promptfoo eval --no-cache -c evaluations/openai_eval_coding.yaml
```
You should see results for the various tests in the Promptfoo UI as well as in the terminal output

Run a model eval for messaging tasks using llm-as-a-judge, confidence, regex, and icontains assertions
```bash
promptfoo eval --no-cache -c evaluations/openai_eval_messaging.yaml
```
You should see results for the various tests in the Promptfoo UI as well as in the terminal output

## Additional Evaluations
Feel free to review or test out the other evaluation examples in `/evaluations`

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway openai
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-all-models
kubectl delete secret -n enterprise-agentgateway openai-secret
rm -f promptfoo-errors.log
```