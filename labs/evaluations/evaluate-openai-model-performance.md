# Evaluate OpenAI Model Performance with Promptfoo

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our LLM provider using an `AgentgatewayModel` and `HTTPRoute`
- Curl OpenAI through the agentgateway proxy
- Install promptfoo on your local machine
- Run evaluations

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and `AgentgatewayModel`
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
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
  match:
    model: "*"
  provider: OpenAI
  policies:
    auth:
      secretRef:
        name: openai-secret
    #--- Uncomment to always send one model, whatever the client requests ---
    #transformations:
    #  - field: model
    #    expression: '"gpt-5.4-nano"'
EOF
```

## curl openai
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')


curl -i "$GATEWAY_IP:8080/openai/v1/chat/completions" \
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
export OPENAI_BASE_URL="http://$GATEWAY_IP:8080/openai/v1"
```

Run a model eval for coding tasks using llm-as-a-judge and confidence scoring assertions
```bash
promptfoo eval --no-cache -c labs/evaluations/openai_eval_coding.yaml
```
You should see results for the various tests in the Promptfoo UI as well as in the terminal output

Run a model eval for messaging tasks using llm-as-a-judge, confidence, regex, and icontains assertions
```bash
promptfoo eval --no-cache -c labs/evaluations/openai_eval_messaging.yaml
```
You should see results for the various tests in the Promptfoo UI as well as in the terminal output

## Additional Evaluations
Feel free to review or test out the other evaluation examples in `labs/evaluations/`

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system openai --ignore-not-found
kubectl delete agentgatewaymodel -n agentgateway-system openai-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret --ignore-not-found
rm -f promptfoo-errors.log
```