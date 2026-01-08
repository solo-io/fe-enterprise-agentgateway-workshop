# Configure Failover From HTTP 429 Server to OpenAI

In this lab, you'll configure priority group failover using an HTTP 429 server as priority group 1 and OpenAI as priority group 2. When the primary backend returns rate limit errors, it's marked as unhealthy, causing subsequent requests to route to the secondary priority group

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`

## Lab Objectives
- Deploy an HTTP 429 server that simulates rate limiting scenarios
- Configure OpenAI as a failover backend
- Create priority group failover configuration with http-429-server as priority 1 and OpenAI as priority 2
- Test failover from rate-limited backend to healthy OpenAI backend
- Observe failover behavior in logs and traces

## Deploy HTTP 429 Server

Deploy the HTTP 429 server using the manifest below. This server always returns a 429 response with a `Retry-After` header, simulating a rate-limited backend.

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: http-429-server
  namespace: enterprise-agentgateway
  labels:
    app: http-429-server
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: http-429-server
  namespace: enterprise-agentgateway
  labels:
    app: http-429-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: http-429-server
  template:
    metadata:
      labels:
        app: http-429-server
    spec:
      serviceAccountName: http-429-server
      containers:
      - name: http-429-server
        image: ably7/http-429:0.1
        ports:
        - containerPort: 9959
          protocol: TCP
        resources:
          requests:
            memory: "64Mi"
            cpu: "100m"
          limits:
            memory: "128Mi"
            cpu: "200m"
---
apiVersion: v1
kind: Service
metadata:
  name: http-429-server
  namespace: enterprise-agentgateway
  labels:
    app: http-429-server
spec:
  type: ClusterIP
  ports:
  - port: 9959
    targetPort: 9959
    protocol: TCP
    name: http
  selector:
    app: http-429-server
EOF
```

Verify the deployment:
```bash
kubectl get pods -n enterprise-agentgateway | grep http-429-server
kubectl get svc -n enterprise-agentgateway | grep http-429-server
```

You should see the http-429-server pod running and its service available on port 9959.

## Configure OpenAI Secret

Create a Kubernetes secret with your OpenAI API key for the failover backend:

```bash
export OPENAI_API_KEY=$OPENAI_API_KEY

kubectl create secret generic openai-secret -n enterprise-agentgateway \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

## Create Priority Group Failover Configuration

Configure the AgentgatewayBackend with priority groups and HTTPRoute. The backend will first attempt to use the http-429-server (priority group 1), and when it returns a 429 error it will be marked unhealthy, a second request will then fail over to OpenAI (priority group 2):

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: http-429
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /http-429
      backendRefs:
        - name: http-429
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: http-429
  namespace: enterprise-agentgateway
spec:
  ai:
    groups:
      # Priority Group 1: HTTP 429 Server (always returns rate limit errors)
      - providers:
          - name: http-429-provider
            openai:
              model: "gpt-5"
            host: http-429-server.enterprise-agentgateway.svc.cluster.local
            port: 9959
            path: "/"
            policies:
              auth:
                passthrough: {}
      # Priority Group 2: OpenAI (failover when group 1 returns 429)
      - providers:
          - name: openai-provider
            openai:
              model: "gpt-4o-mini"
            policies:
              auth:
                secretRef:
                  name: openai-secret
EOF
```

## Test Priority Group Failover

Now test the failover behavior. Priority group failover works across requests rather than within a single request.

Get the Gateway IP address:
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### First Request - Triggers Failover

Send the first request:

```bash
curl -v "$GATEWAY_IP:8080/http-429" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }'
```

Expected output from first request:
```
< HTTP/1.0 429 Too Many Requests
< server: BaseHTTP/0.6 Python/3.11.14
< retry-after: 60
< content-type: application/json
<
{"error":{"message":"Rate limit exceeded","type":"rate_limit_error"}}
```

**What happened:**
- The gateway tried priority group 1 (http-429-server)
- Received a 429 error
- Returned the 429 to the client
- **Marked the http-429-server provider as unhealthy/rate-limited**

### Second Request - Uses Failover Backend

Immediately send a second request:

```bash
curl -v "$GATEWAY_IP:8080/http-429" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }'
```

Expected output from second request:
```
< HTTP/1.1 200 OK
< Content-Type: application/json
<
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": ...,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "2 + 2 equals 4."
      },
      "finish_reason": "stop"
    }
  ],
  ...
}
```

**What happened:**
- The gateway detected that priority group 1 is unhealthy
- Skipped priority group 1 and went directly to priority group 2 (OpenAI)
- Received a successful 200 response from OpenAI
- Subsequent requests will continue using priority group 2 until group 1 becomes healthy again

## Observability

### View Metrics in Grafana

Use the AgentGateway Grafana dashboard to observe aggregated metrics:

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

The Grafana dashboard provides aggregated metrics including:
- HTTP status code distribution (429 vs 200 responses)
- Request rates over time
- Error rates and percentages
- Token usage by model
- Request duration percentiles

### View Access Logs

Check AgentGateway logs to see the failover behavior:

```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 50 | jq .
```

In the access logs (entries with `"scope": "request"`), you can observe:

**First Request (429 error):**
- `"endpoint": "http-429-server.enterprise-agentgateway.svc.cluster.local:9959"`
- `"http.status": 429`
- `"duration": "2ms"` (very fast since it's just returning an error)
- `"response.body"` showing the rate limit error message

**Second Request (successful failover):**
- `"endpoint": "api.openai.com:443"`
- `"http.status": 200`
- `"duration": "1372ms"` (longer due to actual LLM processing)
- `"gen_ai.response.model"`, `"gen_ai.usage.input_tokens"`, `"gen_ai.usage.output_tokens"` showing successful OpenAI response
- `"response.body"` containing the actual LLM completion

The change in `endpoint` field between requests clearly shows the failover from the http-429-server to OpenAI.

### View Traces in Grafana

To view distributed traces and see failover behavior across requests:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Compare traces from your two requests:
   - **First trace**: Shows attempt to http-429-server with 429 error span
   - **Second trace**: Shows successful routing to OpenAI backend with 200 response
5. You can see how the backend selection changes based on provider health

## Understanding Priority Group Failover

The priority group failover configuration demonstrates several key concepts:

### How Priority Groups Work

1. **Priority Ordering**: The gateway prefers providers in higher priority groups (group 1 over group 2, etc.)
2. **Health-Based Failover**: When a provider returns certain error codes (like 429), it's marked as unhealthy
   - The **first request** that encounters the error will fail with that error code
   - **Subsequent requests** will skip unhealthy providers and use the next priority group
3. **Across-Request Failover**: Unlike retry policies that work within a single request, priority group failover works across multiple requests based on provider health state
4. **Production Use Case**: This pattern is ideal for scenarios where you have:
   - Primary backends that may experience temporary rate limiting
   - Fallback backends as safety nets for subsequent requests
   - Different cost tiers (prefer cheaper model, fall back to more expensive when primary is unavailable)
   - Circuit-breaking behavior without explicit circuit breaker configuration

## Cleanup
```bash
kubectl delete httproute -n enterprise-agentgateway http-429
kubectl delete agentgatewaybackend -n enterprise-agentgateway http-429
kubectl delete secret -n enterprise-agentgateway openai-secret
kubectl delete -n enterprise-agentgateway svc/http-429-server
kubectl delete -n enterprise-agentgateway deploy/http-429-server
kubectl delete -n enterprise-agentgateway serviceaccount/http-429-server
```
