# OpenAI Video Generation (Sora)

In this lab, you'll proxy OpenAI's Video API (Sora) through AgentGateway to generate video from a text prompt. Video generation is asynchronous — you submit a job, poll for completion, then download the result.

> **Note:** The Sora API (`sora-2`, `sora-2-pro`) is deprecated and will shut down on **September 24, 2026**.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Configure an AgentGateway backend with Passthrough routes for the OpenAI Video API
- Submit a video generation job
- Poll for completion and download the generated video

## Configure OpenAI Route with Video Support

Create an OpenAI secret:
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY

kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create the backend and route. The Video endpoints use the `Passthrough` route type:

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
    - backendRefs:
        - name: openai-video
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-video
  namespace: agentgateway-system
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
        "/v1/chat/completions": "Completions"
        "*": "Passthrough"
EOF
```

Get the Gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

## Generate a Video

Submit a video generation request. This returns immediately with a job ID:

```bash
curl -s "$GATEWAY_IP:8080/v1/videos" \
  -H "content-type: application/json" \
  -d '{
    "model": "sora-2",
    "prompt": "A slow tracking shot of a golden retriever running through a sunlit meadow with wildflowers",
    "size": "1280x720",
    "seconds": "4"
  }' | python3 -m json.tool
```

**Expected output:**

```json
{
  "id": "video_abc123...",
  "object": "video",
  "model": "sora-2",
  "status": "queued",
  "progress": 0,
  "created_at": 1746191234,
  "size": "1280x720",
  "seconds": "4"
}
```

Save the video ID:
```bash
export VIDEO_ID=<paste the id from the response>
```

## Poll for Completion

Video generation takes 1-3 minutes. Poll the status:

```bash
curl -s "$GATEWAY_IP:8080/v1/videos/$VIDEO_ID" | python3 -m json.tool
```

The `status` field will progress through: `queued` → `in_progress` → `completed`

The `progress` field shows a percentage (0-100). Keep polling until `status` is `completed`:

```bash
while true; do
  STATUS=$(curl -s "$GATEWAY_IP:8080/v1/videos/$VIDEO_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "Status: $STATUS"
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && echo "Generation failed" && break
  sleep 10
done
```

## Download the Video

Once the status is `completed`, download the video:

```bash
curl -L "$GATEWAY_IP:8080/v1/videos/$VIDEO_ID/content" \
  --output video.mp4
```

Play the generated video:
```bash
# macOS
open video.mp4

# Linux
xdg-open video.mp4
```

## Observability

### View Access Logs

Check AgentGateway logs for the video request details:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

You'll see log entries for each API call — the initial POST, each polling GET, and the content download.

### View Metrics in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

> **Note:** Since video endpoints use the `Passthrough` route type, token-level metrics are not available. AgentGateway still tracks request counts, latencies, and status codes.

## Cleanup

Delete the generated video from OpenAI and remove the lab resources:
```bash
curl -s -X DELETE "$GATEWAY_IP:8080/v1/videos/$VIDEO_ID"

kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-video
kubectl delete secret -n agentgateway-system openai-secret
rm -f video.mp4
```
