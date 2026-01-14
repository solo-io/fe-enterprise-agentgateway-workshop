# Configure OpenAI Batches API
Configure access to OpenAI Batches API for asynchronous batch processing of requests through the AgentgatewayBackend.

## Pre-requisites
This lab assumes that you have completed the setup in `001`, and `002`

## Lab Objectives
- Create a Kubernetes secret that contains our OpenAI api-key credentials
- Create a route to OpenAI as our backend LLM provider using a `Backend` and `HTTPRoute`
- Configure AI routes to handle different OpenAI Batch API endpoints (batches, files)
- Test batch creation, file uploads, and status checking through the agentgateway proxy
- Validate the requests went through the gateway in Grafana UI

### Configure Required Variables
Replace with a valid OpenAI API key
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
```

Create openai api-key secret
```bash
kubectl create secret generic openai-secret -n enterprise-agentgateway \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create openai route and backend with AI routes configuration for batches
```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai-batches
  namespace: enterprise-agentgateway
spec:
  parentRefs:
    - name: agentgateway
      namespace: enterprise-agentgateway
  rules:
    - backendRefs:
        - name: openai-batches-backend
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-batches-backend
  namespace: enterprise-agentgateway
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
    ai:
      routes:
        "/v1/batches": "Passthrough"
        "/v1/batches/*": "Passthrough"
        "/v1/files": "Passthrough"
        "/v1/files/*": "Passthrough"
        "*": "Passthrough"
EOF
```

The `policies.ai.routes` configuration allows you to route different OpenAI Batch API endpoints through the gateway:
- `/v1/batches`: `"Passthrough"` - Create and list batch jobs
- `/v1/batches/*`: `"Passthrough"` - Retrieve, cancel specific batch jobs
- `/v1/files`: `"Passthrough"` - Upload and list files
- `/v1/files/*`: `"Passthrough"` - Retrieve, download, delete specific files
- `*`: `"Passthrough"` - Default passthrough for any other paths

## Understanding OpenAI Batches API

The Batches API allows you to send asynchronous groups of requests with 50% lower costs and a separate pool of significantly higher rate limits. The workflow involves:

1. Creating a JSONL file with your batch requests
2. Uploading the file using `/v1/files`
3. Creating a batch job using `/v1/batches`
4. Polling the batch status
5. Retrieving results when complete

## Test OpenAI Batches API

Export the gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n enterprise-agentgateway --selector=gateway.networking.k8s.io/gateway-name=agentgateway -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### Step 1: Create a Batch Request File

Create a JSONL file containing batch requests. Each line must be a valid JSON object with:
- `custom_id`: Your unique identifier for the request
- `method`: HTTP method (POST)
- `url`: The API endpoint (e.g., `/v1/chat/completions`)
- `body`: The request payload

```bash
cat > batch_requests.jsonl <<'EOF'
{"custom_id": "request-1", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is the capital of France?"}], "max_tokens": 100}}
{"custom_id": "request-2", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "What is 2+2?"}], "max_tokens": 100}}
{"custom_id": "request-3", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Name a color."}], "max_tokens": 100}}
EOF
```

### Step 2: Upload the Batch File

Upload your JSONL file to OpenAI's file storage:

```bash
curl -i "$GATEWAY_IP:8080/v1/files" \
  -F purpose="batch" \
  -F file="@batch_requests.jsonl"
```

Example response:
```json
{
  "id": "file-abc123",
  "object": "file",
  "bytes": 512,
  "created_at": 1234567890,
  "filename": "batch_requests.jsonl",
  "purpose": "batch"
}
```

Save the file ID from the response:
```bash
export FILE_ID="file-abc123"  # Replace with actual file ID from response
```

### Step 3: Create a Batch Job

Create a batch processing job using the uploaded file:

```bash
curl -i "$GATEWAY_IP:8080/v1/batches" \
  -H "content-type: application/json" \
  -d "{
    \"input_file_id\": \"$FILE_ID\",
    \"endpoint\": \"/v1/chat/completions\",
    \"completion_window\": \"24h\"
  }"
```

Example response:
```json
{
  "id": "batch_abc123",
  "object": "batch",
  "endpoint": "/v1/chat/completions",
  "errors": null,
  "input_file_id": "file-abc123",
  "completion_window": "24h",
  "status": "validating",
  "output_file_id": null,
  "error_file_id": null,
  "created_at": 1234567890,
  "in_progress_at": null,
  "expires_at": 1234654290,
  "finalizing_at": null,
  "completed_at": null,
  "failed_at": null,
  "expired_at": null,
  "cancelling_at": null,
  "cancelled_at": null,
  "request_counts": {
    "total": 3,
    "completed": 0,
    "failed": 0
  },
  "metadata": null
}
```

Save the batch ID:
```bash
export BATCH_ID="batch_abc123"  # Replace with actual batch ID from response
```

### Step 4: Check Batch Status

Poll the batch status to see when it's completed:

```bash
curl -i "$GATEWAY_IP:8080/v1/batches/$BATCH_ID" \
  -H "content-type: application/json"
```

Batch statuses include:
- `validating`: File is being validated
- `in_progress`: Batch is processing
- `finalizing`: Batch is finalizing
- `completed`: Batch completed successfully
- `failed`: Batch failed
- `expired`: Batch expired
- `cancelled`: Batch was cancelled

Example response when completed:
```json
{
  "id": "batch_abc123",
  "object": "batch",
  "endpoint": "/v1/chat/completions",
  "errors": null,
  "input_file_id": "file-abc123",
  "completion_window": "24h",
  "status": "completed",
  "output_file_id": "file-xyz789",
  "error_file_id": null,
  "created_at": 1234567890,
  "in_progress_at": 1234567895,
  "expires_at": 1234654290,
  "finalizing_at": 1234568000,
  "completed_at": 1234568010,
  "failed_at": null,
  "expired_at": null,
  "cancelling_at": null,
  "cancelled_at": null,
  "request_counts": {
    "total": 3,
    "completed": 3,
    "failed": 0
  },
  "metadata": null
}
```

### Step 5: Download Batch Results

Once the batch status is `completed`, download the results using the `output_file_id`:

```bash
export OUTPUT_FILE_ID="file-xyz789"  # Replace with actual output_file_id from status response

curl -i "$GATEWAY_IP:8080/v1/files/$OUTPUT_FILE_ID/content" \
  -H "content-type: application/json" \
  -o batch_results.jsonl
```

View the results:
```bash
cat batch_results.jsonl
```

Example output (each line is a separate JSON response):
```json
{"id": "batch_req_abc123", "custom_id": "request-1", "response": {"status_code": 200, "request_id": "req_123", "body": {"id": "chatcmpl-123", "object": "chat.completion", "created": 1234567890, "model": "gpt-4o-mini", "choices": [{"index": 0, "message": {"role": "assistant", "content": "The capital of France is Paris."}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}}}, "error": null}
{"id": "batch_req_def456", "custom_id": "request-2", "response": {"status_code": 200, "request_id": "req_456", "body": {"id": "chatcmpl-456", "object": "chat.completion", "created": 1234567891, "model": "gpt-4o-mini", "choices": [{"index": 0, "message": {"role": "assistant", "content": "2+2 equals 4."}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14}}}, "error": null}
{"id": "batch_req_ghi789", "custom_id": "request-3", "response": {"status_code": 200, "request_id": "req_789", "body": {"id": "chatcmpl-789", "object": "chat.completion", "created": 1234567892, "model": "gpt-4o-mini", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Blue."}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}}}, "error": null}
```

## Additional Batch Operations

### List All Batches

List all batch jobs:
```bash
curl -i "$GATEWAY_IP:8080/v1/batches" \
  -H "content-type: application/json"
```

### Cancel a Batch

Cancel a batch that is in progress:
```bash
curl -i "$GATEWAY_IP:8080/v1/batches/$BATCH_ID/cancel" \
  -X POST \
  -H "content-type: application/json"
```

### List Files

List all uploaded files:
```bash
curl -i "$GATEWAY_IP:8080/v1/files" \
  -H "content-type: application/json"
```

### Delete a File

Delete a file:
```bash
curl -i "$GATEWAY_IP:8080/v1/files/$FILE_ID" \
  -X DELETE \
  -H "content-type: application/json"
```

## View Access Logs

AgentGateway automatically logs information about the batch API requests to stdout:

```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 5
```

Example output for batch creation:
```json
{
  "level": "info",
  "time": "2026-01-14T19:20:15.123456Z",
  "scope": "request",
  "gateway": "enterprise-agentgateway/agentgateway",
  "listener": "http",
  "route": "enterprise-agentgateway/openai-batches",
  "endpoint": "api.openai.com:443",
  "src.addr": "10.42.0.1:45678",
  "http.method": "POST",
  "http.host": "192.168.107.2",
  "http.path": "/v1/batches",
  "http.version": "HTTP/1.1",
  "http.status": 200,
  "trace.id": "abc123def456",
  "span.id": "xyz789",
  "protocol": "llm",
  "duration": "456ms",
  "request.body": {
    "input_file_id": "file-abc123",
    "endpoint": "/v1/chat/completions",
    "completion_window": "24h"
  },
  "response.body": {
    "id": "batch_abc123",
    "object": "batch",
    "endpoint": "/v1/chat/completions",
    "status": "validating",
    "input_file_id": "file-abc123",
    "completion_window": "24h",
    "created_at": 1234567890,
    "request_counts": {
      "total": 3,
      "completed": 0,
      "failed": 0
    }
  }
}
```

## Use Cases for Batches API

The Batches API is ideal for:
- **Bulk processing**: Running evaluations across large datasets
- **Cost optimization**: 50% discount compared to synchronous API calls
- **Higher throughput**: Separate rate limits from synchronous requests
- **Overnight jobs**: 24-hour completion window for non-urgent tasks
- **A/B testing**: Testing multiple prompts or models at scale
- **Data analysis**: Processing large amounts of text for embeddings or completions

## Observability

### View Access Logs

AgentGateway automatically logs detailed information about batch API requests including file uploads, batch creation, and status checks. All requests include trace IDs for correlation with distributed traces in Grafana.

Check the logs:
```bash
kubectl logs deploy/agentgateway -n enterprise-agentgateway --tail 20
```

## Cleanup

Clean up the resources created in this lab:

```bash
# Delete the batch (if still active)
curl -X POST "$GATEWAY_IP:8080/v1/batches/$BATCH_ID/cancel" \
  -H "content-type: application/json"

# Delete uploaded files
curl -X DELETE "$GATEWAY_IP:8080/v1/files/$FILE_ID" \
  -H "content-type: application/json"

curl -X DELETE "$GATEWAY_IP:8080/v1/files/$OUTPUT_FILE_ID" \
  -H "content-type: application/json"

# Delete local files
rm -f batch_requests.jsonl batch_results.jsonl

# Delete Kubernetes resources
kubectl delete httproute -n enterprise-agentgateway openai-batches
kubectl delete agentgatewaybackend -n enterprise-agentgateway openai-batches-backend
kubectl delete secret -n enterprise-agentgateway openai-secret
```
