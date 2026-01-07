# Configure Basic Routing to Vertex AI (Service Account Auth)

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our Vertex AI OAuth credentials using a GCP service account
- Create a route to Vertex AI as our backend LLM provider using an `AgentgatewayBackend` and `HTTPRoute`
- Curl Vertex AI through the agentgateway proxy
- Validate the request went through the gateway in the Grafana UI

### Benefits of Service Account Authentication

- **Explicit and reproducible identity**: Authentication is tied to a specific service account, not a local user.
- **CI/CD friendly**: Works consistently across machines, environments, and automation pipelines.
- **Least-privilege access**: Permissions are controlled through IAM roles assigned to the service account.
- **Production-aligned**: Mirrors how AI Gateways typically authenticate to cloud providers in real deployments.
- **Auditable and predictable**: All requests are clearly attributable to a known service account.

### Caveats to Service Account Authentication

- **Requires credential management**: Service account keys must be created, stored securely, and rotated, which adds operational overhead compared to user-based authentication.

### Configure Required Variables

Set the following environment variables to match your GCP Vertex AI project.

**Note:** This demo uses a GCP service account with a JSON key file to mint an OAuth access token for routing requests to Vertex AI through the AI Gateway.

```bash
export GCP_PROJECT_ID="<YOUR-GCP-PROJECT-ID>"
export GCP_REGION="us-central1"  # or your preferred region
```

### Set up Service Account Authentication

Set the path to your service account key file:
```bash
export VERTEX_SA_KEY="./.vertex-ai-gcp.json"  # or your custom path
```

If you don't have a service account key yet, create one:
1. Go to the [GCP Console](https://console.cloud.google.com/iam-admin/serviceaccounts)
2. Select your project
3. Create a new service account or select an existing one
4. Grant the service account the "Vertex AI User" role
5. Create and download a JSON key file
6. Save it to the path specified in `VERTEX_SA_KEY`

Activate the service account and set credentials:
```bash
export GOOGLE_APPLICATION_CREDENTIALS="$VERTEX_SA_KEY"

gcloud auth activate-service-account \
  --key-file="$GOOGLE_APPLICATION_CREDENTIALS"
```

Retrieve an OAuth access token using the service account:
```bash
export VERTEXAI_ACCESS_TOKEN=$(gcloud auth print-access-token)
```

Create vertex ai oauth secret
```bash
kubectl create secret generic vertex-ai-secret -n enterprise-agentgateway \
  --from-literal="Authorization=Bearer $VERTEXAI_ACCESS_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Create vertex ai route and backend
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: vertex-ai
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /vertex
      backendRefs:
        - name: vertex-ai
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: vertex-ai
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      vertexai:
        model: "google/gemini-2.5-flash-lite"
        projectId: "${GCP_PROJECT_ID}"
        region: "${GCP_REGION}"
  policies:
    auth:
      secretRef:
        name: vertex-ai-secret
EOF
```

## curl vertex ai
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/vertex" \
  -H "content-type: application/json" \
  -d '{
    "model": "google/gemini-2.5-flash-lite",
    "messages": [
      {
        "role": "user",
        "content": "Whats your favorite poem?"
      }
    ]
  }'
```

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n enterprise-agentgateway deployment/agentgateway 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

### View Metrics and Traces in Grafana

For a comprehensive view of metrics and traces, use the AgentGateway Grafana dashboard installed in lab 002.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

The dashboard provides real-time visualization of:
- Core GenAI metrics (request rates, token usage by model)
- Streaming metrics (TTFT, TPOT)
- MCP metrics (tool calls, server requests)
- Connection and runtime metrics

### View Traces in Grafana

To view distributed traces with LLM-specific spans:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Filter traces by service, operation, or trace ID to find AgentGateway requests

Traces include LLM-specific spans with information like `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more.

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout:

```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 1
```

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

### (Optional) View Traces in Jaeger

If you installed Jaeger in lab `/install-on-openshift/002-set-up-monitoring-tools-ocp.md` instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger-query -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans including `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway vertex-ai
kubectl delete agentgatewaybackend -n enterprise-agentgateway vertex-ai
kubectl delete secret -n enterprise-agentgateway vertex-ai-secret
```
