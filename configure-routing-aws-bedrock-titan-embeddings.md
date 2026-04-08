# AWS Bedrock Titan Embeddings

Embedding models use a different request format than standard chat completions, so they require the `Passthrough` route type. This lab demonstrates how to route requests to the Amazon Titan Embed Text v2 model through AgentGateway using a `Passthrough` route with a URL rewrite to the Bedrock InvokeModel endpoint.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

You will also need:
- An AWS account with Bedrock access enabled
- Amazon Titan Embed Text v2 model enabled in your region (us-east-1)

## Lab Objectives
- Create a Kubernetes secret that contains our AWS Access Key credentials
- Create an `AgentgatewayBackend` with a `Passthrough` route for the Titan Embed InvokeModel endpoint
- Create an `HTTPRoute` with a URL rewrite that maps a friendly path to the Bedrock InvokeModel API
- Test the embedding endpoint through the AgentGateway proxy

## Export AWS Credentials
Log in to AWS console and export the following variables:
```bash
export AWS_ACCESS_KEY_ID="<aws access key id>"
export AWS_SECRET_ACCESS_KEY="<aws secret access key>"
export AWS_SESSION_TOKEN="<aws session token>"
```

Echo the vars to make sure that they were exported:
```bash
echo $AWS_ACCESS_KEY_ID
echo $AWS_SECRET_ACCESS_KEY
echo $AWS_SESSION_TOKEN
```

Create a secret containing the AWS credentials:
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: bedrock-secret
  namespace: agentgateway-system
type: Opaque
stringData:
  accessKey: ${AWS_ACCESS_KEY_ID}
  secretKey: ${AWS_SECRET_ACCESS_KEY}
  sessionToken: ${AWS_SESSION_TOKEN}
EOF
```

## Create the AgentgatewayBackend

Create an `AgentgatewayBackend` for the Titan Embed v2 model. The `policies.ai.routes` block marks the InvokeModel endpoint as `Passthrough` — this is required for embedding models because they do not use the OpenAI-compatible Chat Completions format.

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: bedrock-titan-embed-v2
  namespace: agentgateway-system
spec:
  ai:
    provider:
      bedrock:
        model: amazon.titan-embed-text-v2:0
        region: us-east-1
  policies:
    auth:
      aws:
        secretRef:
          name: bedrock-secret
    ai:
      routes:
        /model/amazon.titan-embed-text-v2:0/invoke: Passthrough
EOF
```

## Create the HTTPRoute

Create an `HTTPRoute` with a URL rewrite that maps the friendly path `/bedrock/titan-embed` to the Bedrock InvokeModel endpoint:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bedrock-titan-embed
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /bedrock/titan-embed
      filters:
        - type: URLRewrite
          urlRewrite:
            hostname: bedrock-runtime.us-east-1.amazonaws.com
            path:
              type: ReplaceFullPath
              replaceFullPath: /model/amazon.titan-embed-text-v2:0/invoke
      backendRefs:
        - name: bedrock-titan-embed-v2
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "30s"
EOF
```

The URL rewrite rewrites both the hostname (to the Bedrock regional endpoint) and the full path (to the InvokeModel endpoint). Incoming requests to `/bedrock/titan-embed` are forwarded to `bedrock-runtime.us-east-1.amazonaws.com/model/amazon.titan-embed-text-v2:0/invoke`.

## Test the Embedding Endpoint

Export the gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

Send an embedding request. Amazon Titan Embed uses an `inputText` field (a single string), not the `texts` array format used by some other embedding models:
```bash
curl -i "$GATEWAY_IP:8080/bedrock/titan-embed" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "inputText": "What is Amazon Bedrock?"
  }'
```

> **Note:** Include the `Accept: application/json` header. Omitting it causes an "invalid Accept Type" error from the Bedrock API.

Expected response (the `embedding` array is truncated for readability):
```json
{
  "embedding": [
    0.015625,
    -0.0234375,
    0.0078125,
    ...
  ],
  "inputTextTokenCount": 5
}
```

## View Access Logs

AgentGateway automatically logs request details to stdout:
```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

## Observability

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

### View Traces in Grafana

To view distributed traces:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Filter by service or operation to find AgentGateway requests for this embedding call

## Cleanup
```bash
kubectl delete httproute -n agentgateway-system bedrock-titan-embed
kubectl delete agentgatewaybackend -n agentgateway-system bedrock-titan-embed-v2
kubectl delete secret -n agentgateway-system bedrock-secret
```
