# OpenAI Audio (Text-to-Speech & Speech-to-Text)

In this lab, you'll proxy OpenAI's Audio API through AgentGateway to generate speech from text and transcribe audio back to text. This demonstrates AgentGateway's ability to handle audio payloads, both JSON and `multipart/form-data`, through the same `AgentgatewayModel` that serves chat completions.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics in Grafana.

## Lab Objectives
- Configure an `AgentgatewayModel` and `HTTPRoute` that serve the OpenAI Audio endpoints
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

Create the route and model:

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
        - group: agentgateway.dev
          kind: AgentgatewayModel
          name: "*"
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayModel
metadata:
  name: openai-audio
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
EOF
```

The rule has no path match, so the OpenAI paths are served at the root of the Gateway. The gateway reads the `model` from each request to pick the model: from the JSON body for `/v1/audio/speech`, and from the `model` form field for the `multipart/form-data` upload to `/v1/audio/transcriptions`.

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

For audio requests, you'll see:
- `http.path`: `/v1/audio/speech` or `/v1/audio/transcriptions`
- `http.status`: `200`
- `protocol`: `llm`
- `agw.ai.original_model`: the requested model, such as `whisper-1`

### View Metrics in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../../002-set-up-monitoring-tools.md). For traces, use the AgentGateway UI.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Navigate to **Dashboards > AgentGateway Dashboard** to view request metrics

> **Note:** Audio requests carry no token usage, so the token panels stay empty for them. The gateway tracks request counts, latencies, and status codes for both endpoints. For transcriptions the access log also records the audio length as `gen_ai.usage.audio_seconds` and the cost as `agw.ai.usage.cost.total`.

## Cleanup

Delete the lab resources:
```bash
kubectl delete httproute -n agentgateway-system openai
kubectl delete agentgatewaymodel -n agentgateway-system openai-audio
kubectl delete secret -n agentgateway-system openai-secret
rm -f speech.mp3 speech-nova.mp3 speech.wav
```
