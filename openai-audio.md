# OpenAI Audio (Text-to-Speech & Speech-to-Text)

In this lab, you'll proxy OpenAI's Audio API through AgentGateway to generate speech from text and transcribe audio back to text. This demonstrates AgentGateway's ability to handle audio payloads using the `Passthrough` route type.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Configure an AgentGateway backend with Passthrough routes for OpenAI Audio endpoints
- Generate speech audio from text using `/v1/audio/speech` (Text-to-Speech)
- Transcribe audio back to text using `/v1/audio/transcriptions` (Speech-to-Text)
- Complete a round-trip: text → audio → text

## Configure OpenAI Route with Audio Support

Create an OpenAI secret:
```bash
export OPENAI_API_KEY=$OPENAI_API_KEY

kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Create the backend and route. The Audio endpoints use the `Passthrough` route type since they are not standard chat completions:

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
        - name: openai-audio
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-audio
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
        "/v1/audio/speech": "Passthrough"
        "/v1/audio/transcriptions": "Passthrough"
        "*": "Passthrough"
EOF
```

The `policies.ai.routes` configuration routes each endpoint:
- `/v1/chat/completions`: `"Completions"` — standard chat with full AI gateway processing (metrics, guardrails)
- `/v1/audio/speech`: `"Passthrough"` — proxies TTS requests to OpenAI
- `/v1/audio/transcriptions`: `"Passthrough"` — proxies STT requests to OpenAI
- `*`: `"Passthrough"` — default passthrough for any other paths

Get the Gateway IP:
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

## Text-to-Speech (TTS)

Generate speech from text using OpenAI's TTS API. The response is raw audio bytes.

### Generate Speech

```bash
curl -s "$GATEWAY_IP:8080/v1/audio/speech" \
  -H "content-type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "Hello! Agent Gateway is proxying this text to speech request through to OpenAI.",
    "voice": "alloy"
  }' \
  --output speech.mp3
```

Play the generated audio:
```bash
# macOS
afplay speech.mp3

# Linux
aplay speech.mp3
# or
mpv speech.mp3
```

You should hear the text spoken in the "alloy" voice.

### Available Voices

OpenAI TTS supports several voices. Try a different one:

```bash
curl -s "$GATEWAY_IP:8080/v1/audio/speech" \
  -H "content-type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "This is the nova voice, speaking through Agent Gateway.",
    "voice": "nova"
  }' \
  --output speech-nova.mp3
```

Available voices: `alloy`, `ash`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, `shimmer`

### Output Formats

The default output format is `mp3`. You can specify other formats:

```bash
curl -s "$GATEWAY_IP:8080/v1/audio/speech" \
  -H "content-type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "This is WAV format audio.",
    "voice": "alloy",
    "response_format": "wav"
  }' \
  --output speech.wav
```

Supported formats: `mp3`, `opus`, `aac`, `flac`, `wav`, `pcm`

## Speech-to-Text (STT)

Transcribe audio back to text using OpenAI's Whisper model. This uses a `multipart/form-data` POST with the audio file.

### Transcribe the Generated Audio

Use the `speech.mp3` file generated in the TTS step:

```bash
curl -s "$GATEWAY_IP:8080/v1/audio/transcriptions" \
  -F file=@speech.mp3 \
  -F model=whisper-1
```

**Expected output:**

```json
{
  "text": "Hello, Agent Gateway is proxying this text to speech requests through to OpenAI."
}
```

The transcription should closely match the original input text, completing the round-trip: text → audio → text.

### Transcription with Verbose Output

Request additional metadata with `response_format` set to `verbose_json`:

```bash
curl -s "$GATEWAY_IP:8080/v1/audio/transcriptions" \
  -F file=@speech.mp3 \
  -F model=whisper-1 \
  -F response_format=verbose_json | python3 -m json.tool
```

**Expected output:**

```json
{
  "task": "transcribe",
  "language": "english",
  "duration": 4.99,
  "text": "Hello, Agent Gateway is proxying this text to speech requests through to OpenAI.",
  "segments": [
    {
      "id": 0,
      "start": 0.0,
      "end": 4.8,
      "text": " Hello, Agent Gateway is proxying this text to speech requests through to OpenAI.",
      ...
    }
  ]
}
```

This includes detected language, duration, and timestamped segments.

## Observability

### View Access Logs

Check AgentGateway logs for the audio request details:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

For Passthrough requests, you'll see:
- `http.path`: `/v1/audio/speech` or `/v1/audio/transcriptions`
- `http.status`: `200`
- `protocol`: `llm`

### View Metrics in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Navigate to **Dashboards > AgentGateway Dashboard** to view request metrics

> **Note:** Since audio endpoints use the `Passthrough` route type, token-level metrics are not available. AgentGateway still tracks request counts, latencies, and status codes.

## Cleanup

Delete the lab resources:
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaybackend -n agentgateway-system openai-audio
kubectl delete secret -n agentgateway-system openai-secret
rm -f speech.mp3 speech-nova.mp3 speech.wav
```
