# Configure Semantic Routing with Jev

In this lab, you'll route LLM requests by prompt content instead of by the model name the client asks for. Clients send one stable virtual model name, `auto_model`. A small adapter, called by the gateway as an external processor, asks [Jev](https://docs.typesafe.ai/introduction) which of three price tiers the prompt needs and rewrites the model name before the gateway routes the request. OpenAI answers each prompt with the cheapest tier that can handle it, and the client code stays the same.

[Semantic Routing with vLLM Semantic Router](configure-semantic-routing-vllm-sr.md) solves the same problem with embedding similarity. This lab keeps the same client contract, route, and tiers, so you can compare the two.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- An OpenAI API key with access to three models of different price. This lab uses `gpt-5-nano` as the economy tier, `gpt-5.6-luna` as the mid tier, and `gpt-5.6-terra` as the high tier.
- A TypeSafe API key from the [TypeSafe console](https://console.typesafe.ai), exported as `TYPESAFE_AI_API_KEY`.
- Egress from the cluster to `pypi.org`. The adapter pod installs its two Python packages at startup.

> **Prompts leave the cluster twice.** The adapter sends the user's prompt to the hosted Jev API at `api.typesafe.ai` to classify it, before the gateway forwards it to OpenAI. Review TypeSafe's data handling terms before you route sensitive traffic this way.

## Lab Objectives
- Deploy an ExtProc adapter that classifies each prompt with one Jev `choice` question and rewrites `auto_model` to the selected tier
- Keep the tier definitions, the model for each tier, and the confidence thresholds in a ConfigMap profile you can tune without changing code
- Call the adapter from the gateway with an `EnterpriseAgentgatewayPolicy` using `traffic.extProc` in the `PreRouting` phase, with a configurable CEL condition that decides which requests reach the adapter
- Send requests that differ only in their prompt text, and observe different models answering
- Read the tier, probabilities, and confidence for each decision in the adapter log, and see a low-confidence prompt fall back to the mid tier
- Observe how the gateway behaves when Jev or the adapter is unavailable

## Architecture

```
Client Request
    │  body: { "model": "auto_model", "messages": [...] }
    ▼
EnterpriseAgentgatewayPolicy (traffic.phase: PreRouting, traffic.extProc)
    │  only when $JEV_ROUTING_CONDITION matches (default: /semantic + auto_model)
    │  gRPC → jev-extproc:50051, failureMode: FailClosed
    ▼
jev-extproc adapter
    │  POST https://api.typesafe.ai/v1/systemone
    │  one choice question: economy | mid | high, with probabilities and confidence
    │  confidence or margin below threshold?  → fallback tier
    │  rewrites body model: auto_model → gpt-5-nano | gpt-5.6-luna | gpt-5.6-terra
    ▼
HTTPRoute /semantic  →  EnterpriseAgentgatewayBackend (openai-all-models)
    │  no model override, so the rewritten name passes straight through
    ▼
OpenAI
```

## Overview

### Why a virtual model name?

Without this pattern, each client hardcodes a model name. Developers pick one that handles their hardest case, so `gpt-5.6-terra` ends up answering throwaway prompts like "Write a 500-word essay about nothing." at high-tier prices. With a virtual model name, model choice becomes a platform decision: `auto_model` is the only name clients need, and the rule behind it lives in a Kubernetes ConfigMap you change at the platform layer.

### How Jev makes the decision

Jev is a classification model. You send it state (here, the prompt) and typed questions, and it returns a structured answer for each question. This lab asks one `choice` question whose options are the three tiers, each described in plain language with examples. Jev returns the winning tier, a probability for every tier, and a confidence value derived from that distribution.

Compared with embedding similarity, you describe each tier in words instead of collecting candidate phrases and calibrating a cosine threshold for one embedding model. The probabilities also let the adapter act on uncertainty: when Jev splits its answer between two tiers, the adapter sends the prompt to a fallback tier.

### Why an adapter?

The gateway speaks the ExtProc gRPC protocol to external processors, and Jev is an HTTP API. The adapter translates between them, and it owns the routing policy:

- A request that names a real model passes through unchanged, and the adapter makes no Jev call for it.
- For `auto_model`, the adapter classifies the last user message, applies the confidence gate, and rewrites `model` in the body.
- It removes any `x-jev-tier` or `x-jev-reason` header the client sent, then sets them to the real decision, so a client cannot pick its own tier through those headers.
- If Jev is unreachable or rejects the call, the adapter answers `503` with a JSON error. The adapter treats a Jev outage as an error and picks no tier.

---

## Store the API keys

```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
export TYPESAFE_AI_API_KEY=$TYPESAFE_AI_API_KEY
```

The adapter reads the TypeSafe key from this Secret:

```bash
kubectl create secret generic typesafe-api-key -n agentgateway-system \
  --from-literal=TYPESAFE_AI_API_KEY=$TYPESAFE_AI_API_KEY \
  --dry-run=client -oyaml | kubectl apply -f -
```

The completion path authenticates with its own Secret, in the header format the gateway forwards to OpenAI:

```bash
kubectl create secret generic openai-secret -n agentgateway-system \
  --from-literal="Authorization=Bearer $OPENAI_API_KEY" \
  --dry-run=client -oyaml | kubectl apply -f -
```

## Define the routing profile

The profile holds everything you would tune: the Jev question, the model behind each tier, and the confidence gate.

- `question` is sent to Jev as-is. Each tier has a description and examples, which Jev reads as the definition of that option.
- `tiers` maps each answer to the OpenAI model that serves it.
- `minConfidence` and `minMargin` form the confidence gate. The margin is the gap between the winning tier's probability and the runner-up's. If either value is below its threshold, the request goes to `fallbackTier`.
- `fallbackTier` is `mid`. An uncertain prompt usually splits between two adjacent tiers, and the mid tier is never more than one step from the right answer.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: jev-routing-profile
  namespace: agentgateway-system
data:
  profile.json: |
    {
      "virtualModel": "auto_model",
      "jevModel": "jev-1.13.0",
      "requestTimeoutMs": 2000,
      "maxPromptChars": 20000,
      "minConfidence": 0.5,
      "minMargin": 0.2,
      "fallbackTier": "mid",
      "tiers": {
        "economy": "gpt-5-nano",
        "mid": "gpt-5.6-luna",
        "high": "gpt-5.6-terra"
      },
      "question": {
        "type": "choice",
        "instructions": "Choose the least expensive model tier that can fully answer the request in `prompt`.",
        "criteria": {
          "economy": {
            "description": "Everyday language tasks that need no technical expertise.",
            "examples": ["summarize or rewrite text", "answer a simple factual question", "translate a phrase", "brainstorm names or ideas", "free-form writing"]
          },
          "mid": {
            "description": "Routine software or technical work a competent engineer finishes quickly.",
            "examples": ["write or fix a short function or script", "explain or refactor code", "write a config file or query"]
          },
          "high": {
            "description": "Tasks that need rigorous multi-step reasoning, even when the prompt is short.",
            "examples": ["prove a mathematical statement", "derive a formula from first principles", "diagnose a subtle concurrency or race-condition bug", "design and justify a correct concurrent algorithm"]
          }
        }
      }
    }
EOF
```

## Deploy the adapter

The adapter is one Python file, mounted from a ConfigMap into a stock `python:3.12-slim` image. An init container installs `grpcio` and `xds-protos`, which supplies the ExtProc gRPC types.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: jev-extproc-code
  namespace: agentgateway-system
data:
  adapter.py: |
    """ExtProc adapter: asks Jev which price tier a prompt needs, then rewrites the model name."""
    import json
    import os
    import sys
    import time
    import urllib.error
    import urllib.request
    from concurrent import futures

    import grpc
    from envoy.config.core.v3 import base_pb2
    from envoy.service.ext_proc.v3 import external_processor_pb2 as pb
    from envoy.service.ext_proc.v3 import external_processor_pb2_grpc as pb_grpc
    from envoy.type.v3 import http_status_pb2

    JEV_URL = "https://api.typesafe.ai/v1/systemone"
    API_KEY = os.environ["TYPESAFE_AI_API_KEY"]
    PROFILE = json.load(open(os.environ.get("JEV_PROFILE_PATH", "/etc/jev/profile.json")))
    DECISION_HEADERS = ("x-jev-tier", "x-jev-reason")


    def log(**fields):
        print(json.dumps(fields), flush=True)


    def prompt_text(body):
        """Return the text of the last user message, or None."""
        for message in reversed(body.get("messages") or []):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        return None


    def ask_jev(text):
        request = {
            "model": PROFILE["jevModel"],
            "state": {"prompt": text[: PROFILE["maxPromptChars"]]},
            "questions": {"tier": PROFILE["question"]},
        }
        http_request = urllib.request.Request(
            JEV_URL,
            data=json.dumps(request).encode(),
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(http_request, timeout=PROFILE["requestTimeoutMs"] / 1000) as response:
            return json.load(response)["answers"]["tier"]


    def decide(answer):
        """Apply the confidence gate. Returns (tier, reason, margin)."""
        ranked = sorted(answer["probabilities"].values(), reverse=True)
        margin = ranked[0] - ranked[1]
        if answer["confidence"] < PROFILE["minConfidence"] or margin < PROFILE["minMargin"]:
            return PROFILE["fallbackTier"], "low_confidence", margin
        return answer["choice"], "classified", margin


    def header(key, value):
        return base_pb2.HeaderValueOption(header=base_pb2.HeaderValue(key=key, raw_value=value.encode()))


    def reject(status, message):
        return pb.ProcessingResponse(
            immediate_response=pb.ImmediateResponse(
                status=http_status_pb2.HttpStatus(code=status),
                headers=pb.HeaderMutation(set_headers=[header("content-type", "application/json")]),
                body=json.dumps({"error": {"message": message, "type": "routing_error"}}).encode(),
            )
        )


    def handle_body(raw):
        try:
            body = json.loads(raw)
        except ValueError:
            return pb.ProcessingResponse(request_body=pb.BodyResponse())
        # Only the virtual model name opts in. A request that names a real model
        # passes through untouched and costs no Jev call.
        if body.get("model") != PROFILE["virtualModel"]:
            return pb.ProcessingResponse(request_body=pb.BodyResponse())
        text = prompt_text(body)
        if not text:
            return reject(400, "auto_model requests need a user message")

        started = time.monotonic()
        try:
            answer = ask_jev(text)
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as err:
            # A classifier outage is not a low-confidence answer. Reject instead of
            # guessing, so the failure is visible to the client and in the logs.
            log(event="jev_error", error=str(err))
            return reject(503, "routing classifier unavailable")
        latency_ms = round((time.monotonic() - started) * 1000)

        tier, reason, margin = decide(answer)
        model = PROFILE["tiers"][tier]
        log(
            event="routing_decision",
            original_model=body["model"],
            selected_model=model,
            tier=tier,
            jev_choice=answer["choice"],
            reason=reason,
            confidence=round(answer["confidence"], 3),
            margin=round(margin, 3),
            probabilities={k: round(v, 3) for k, v in answer["probabilities"].items()},
            jev_latency_ms=latency_ms,
        )

        body["model"] = model
        new_body = json.dumps(body).encode()
        return pb.ProcessingResponse(
            request_body=pb.BodyResponse(
                response=pb.CommonResponse(
                    header_mutation=pb.HeaderMutation(
                        set_headers=[
                            header("x-jev-tier", tier),
                            header("x-jev-reason", reason),
                            # The gateway rejects a body mutation whose length
                            # disagrees with the request's content-length.
                            header("content-length", str(len(new_body))),
                        ]
                    ),
                    body_mutation=pb.BodyMutation(body=new_body),
                )
            )
        )


    class Processor(pb_grpc.ExternalProcessorServicer):
        def Process(self, request_iterator, context):
            for request in request_iterator:
                kind = request.WhichOneof("request")
                if kind == "request_headers":
                    # Drop any decision header the client sent, so a caller cannot
                    # pick its own tier.
                    yield pb.ProcessingResponse(
                        request_headers=pb.HeadersResponse(
                            response=pb.CommonResponse(
                                header_mutation=pb.HeaderMutation(remove_headers=list(DECISION_HEADERS))
                            )
                        )
                    )
                elif kind == "request_body":
                    yield handle_body(request.request_body.body)
                elif kind == "response_headers":
                    yield pb.ProcessingResponse(response_headers=pb.HeadersResponse())
                elif kind == "request_trailers":
                    yield pb.ProcessingResponse(request_trailers=pb.TrailersResponse())
                elif kind == "response_trailers":
                    yield pb.ProcessingResponse(response_trailers=pb.TrailersResponse())
                elif kind == "response_body":
                    yield pb.ProcessingResponse(response_body=pb.BodyResponse())


    def main():
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=32))
        pb_grpc.add_ExternalProcessorServicer_to_server(Processor(), server)
        server.add_insecure_port("[::]:50051")
        server.start()
        log(event="started", port=50051, tiers=PROFILE["tiers"])
        server.wait_for_termination()


    if __name__ == "__main__":
        sys.exit(main())
EOF
```

```bash
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jev-extproc
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: jev-extproc
  template:
    metadata:
      labels:
        app: jev-extproc
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
      initContainers:
        - name: install-deps
          image: python:3.12-slim
          command:
            - pip
            - install
            - --no-cache-dir
            - --target=/deps
            - grpcio==1.84.0
            - xds-protos==1.84.0
          volumeMounts:
            - name: deps
              mountPath: /deps
      containers:
        - name: adapter
          image: python:3.12-slim
          command:
            - python
            - /app/adapter.py
          env:
            - name: PYTHONPATH
              value: /deps
            - name: JEV_PROFILE_PATH
              value: /etc/jev/profile.json
            - name: TYPESAFE_AI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: typesafe-api-key
                  key: TYPESAFE_AI_API_KEY
          ports:
            - name: grpc
              containerPort: 50051
          readinessProbe:
            tcpSocket:
              port: 50051
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 256Mi
          volumeMounts:
            - name: deps
              mountPath: /deps
            - name: code
              mountPath: /app
            - name: profile
              mountPath: /etc/jev
      volumes:
        - name: deps
          emptyDir: {}
        - name: code
          configMap:
            name: jev-extproc-code
        - name: profile
          configMap:
            name: jev-routing-profile
---
apiVersion: v1
kind: Service
metadata:
  name: jev-extproc
  namespace: agentgateway-system
spec:
  selector:
    app: jev-extproc
  ports:
    - name: grpc
      port: 50051
      targetPort: grpc
      appProtocol: kubernetes.io/h2c
EOF
```

Wait for the rollout, then confirm the adapter loaded the profile:

```bash
kubectl rollout status deployment/jev-extproc -n agentgateway-system --timeout=300s
kubectl logs -n agentgateway-system deploy/jev-extproc -c adapter --tail=1
```

```json
{"event": "started", "port": 50051, "tiers": {"economy": "gpt-5-nano", "mid": "gpt-5.6-luna", "high": "gpt-5.6-terra"}}
```

## Create the OpenAI backend and route

One route and one backend serve all three tiers. The `EnterpriseAgentgatewayBackend` has no model override, so OpenAI serves the model name in the request body, including the name the adapter wrote there.

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: semantic
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /semantic
      backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

Confirm the plain path works before adding the adapter, so that a later failure has only one possible cause. This request names a model directly, so it should answer normally.

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo $GATEWAY_IP

curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-5-nano","messages":[{"role":"user","content":"say hi"}]}' | jq '.model'
```

```
"gpt-5-nano-2025-08-07"
```

## Call the adapter from the gateway

`PreRouting` runs on the Gateway before route selection, so the policy targets the Gateway. Without a condition, every request on the Gateway goes through the adapter, and an adapter outage fails every route with `500`. The `conditional` entry takes a CEL expression, and only requests that match it reach the adapter.

### Choose which requests reach the adapter

Set `JEV_ROUTING_CONDITION` to one of these expressions:

| Scope | `JEV_ROUTING_CONDITION` | Requests that depend on the adapter |
|---|---|---|
| Path and model (default) | `request.path.startsWith("/semantic") && json(request.body).model == "auto_model"` | `auto_model` requests on `/semantic` |
| Model on any path | `json(request.body).model == "auto_model"` | `auto_model` requests on any route |
| Path only | `request.path.startsWith("/semantic")` | Every request on `/semantic`, including requests that name a real model |
| Header opt-in | `request.headers["x-route-by"] == "jev"` | Requests that send `x-route-by: jev` |

The default limits the outage impact the most: requests on other routes, and requests on `/semantic` that name a real model, keep working when the adapter is down. The path check comes first, so the gateway only parses the body of `/semantic` requests. A non-JSON body makes `json()` fail the match, so non-JSON requests skip the adapter.

With the header opt-in, an `auto_model` request sent without the header skips the adapter, and OpenAI answers `404` with `model_not_found`.

```bash
export JEV_ROUTING_CONDITION='request.path.startsWith("/semantic") && json(request.body).model == "auto_model"'
```

If you change `virtualModel` in the routing profile, change the model name in this expression to match.

### Apply the policy

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: jev-router
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    phase: PreRouting
    extProc:
      conditional:
        - condition: '${JEV_ROUTING_CONDITION}'
          policy:
            backendRef:
              name: jev-extproc
              namespace: agentgateway-system
              port: 50051
            failureMode: FailClosed
            processingOptions:
              requestHeaderMode: Send
              # The adapter needs the whole prompt to classify it.
              requestBodyMode: Buffered
              # The adapter only acts on the request, so the gateway sends it
              # nothing from the response.
              responseHeaderMode: Skip
              responseBodyMode: None
              requestTrailerMode: Skip
              responseTrailerMode: Skip
              allowModeOverride: false
EOF
```

```bash
kubectl get enterpriseagentgatewaypolicy jev-router -n agentgateway-system
```

```
NAME         ACCEPTED   ATTACHED   AGE
jev-router   True       True       3s
```

> **Why `FailClosed`?** With `FailOpen`, an adapter outage sends the request on with `auto_model` still in the body, and OpenAI answers `model_not_found`, which points you at the provider instead of at the adapter. `FailClosed` rejects the request instead. An adapter outage surfaces as HTTP 500 with `reason=ExtProc` in the access log.

## Test the routing decision

The requests below differ only in their prompt text, and all name `auto_model`.

An everyday language task goes to the economy tier:

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"Summarize this email in one sentence: lunch moved to noon."}]}' | jq '.model'
```

```
"gpt-5-nano-2025-08-07"
```

A routine coding task goes to the mid tier:

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"Write a Python function that parses a CSV file and returns the sum of each numeric column."}]}' | jq '.model'
```

```
"gpt-5.6-luna"
```

A short proof request goes to the high tier. The prompt has no technical vocabulary, but the `high` description says rigorous reasoning qualifies "even when the prompt is short":

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"Show that there are infinitely many primes."}]}' | jq '.model'
```

```
"gpt-5.6-terra"
```

Asking what a regex does could be a quick lookup or a technical explanation, and Jev splits its answer between `economy` and `mid`. Because of the split, the adapter's confidence gate sends the prompt to the fallback tier:

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"Explain what this regex does: ^\\d{3}-\\d{4}$"}]}' | jq '.model'
```

```
"gpt-5.6-luna"
```

### Read the decision in the adapter log

The gateway access log records the model that *served* the request. The reason it was chosen is in the adapter's `routing_decision` line:

```bash
kubectl logs -n agentgateway-system deploy/jev-extproc -c adapter --tail=4 \
  | jq -c '{selected_model, jev_choice, reason, confidence, margin, probabilities, jev_latency_ms}'
```

```json
{"selected_model":"gpt-5-nano","jev_choice":"economy","reason":"classified","confidence":1.0,"margin":1.0,"probabilities":{"mid":0.0,"high":0.0,"economy":1.0},"jev_latency_ms":344}
{"selected_model":"gpt-5.6-luna","jev_choice":"mid","reason":"classified","confidence":1.0,"margin":1.0,"probabilities":{"high":0.0,"economy":0.0,"mid":1.0},"jev_latency_ms":135}
{"selected_model":"gpt-5.6-terra","jev_choice":"high","reason":"classified","confidence":0.91,"margin":0.88,"probabilities":{"mid":0.0,"economy":0.06,"high":0.94},"jev_latency_ms":130}
{"selected_model":"gpt-5.6-luna","jev_choice":"mid","reason":"low_confidence","confidence":0.35,"margin":0.12,"probabilities":{"economy":0.44,"high":0.0,"mid":0.56},"jev_latency_ms":161}
```

The last line is the regex prompt. Jev picked `mid`, but with a margin of about 0.1 against a `minMargin` of 0.2, so the adapter recorded `reason=low_confidence` and served the fallback tier. If you set `fallbackTier` to `economy`, the same prompt would be served by `gpt-5-nano`. Exact probabilities vary slightly between runs.

### Requests that name a real model

A request that names a real model skips classification. This proof request would go to the high tier as `auto_model`, but here it goes to the model the client named:

```bash
curl -s "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-5-nano","messages":[{"role":"user","content":"Prove that sqrt(2) is irrational."}]}' | jq '.model'
```

```
"gpt-5-nano-2025-08-07"
```

> **Clients can still name a premium model.** This lab makes `auto_model` the cheap default without taking choice away from clients. To require routing for every request, reject other model names on this route.

## Tune the profile

The tier descriptions are the routing rule. To change how prompts are classified, edit `jev-routing-profile`, then restart the adapter, which reads the profile at startup:

```bash
kubectl edit configmap jev-routing-profile -n agentgateway-system
kubectl rollout restart deployment/jev-extproc -n agentgateway-system
kubectl rollout status deployment/jev-extproc -n agentgateway-system --timeout=300s
```

When you tune the profile:

- Change one tier description at a time, then replay a fixed set of representative prompts and compare the `routing_decision` lines.
- Raise `minMargin` to send more borderline prompts to the fallback tier. Lower it to trust Jev's first choice more often.
- Describe what a tier *needs*, such as rigorous reasoning or routine code, rather than listing surface keywords. Jev matches the meaning of the description.

## Observe failure behavior

A Jev failure and an adapter failure produce different errors, so you can tell them apart in alerts.

To simulate Jev rejecting the call, give the adapter an invalid TypeSafe key:

```bash
kubectl create secret generic typesafe-api-key -n agentgateway-system \
  --from-literal=TYPESAFE_AI_API_KEY=invalid \
  --dry-run=client -oyaml | kubectl apply -f -
kubectl rollout restart deployment/jev-extproc -n agentgateway-system
kubectl rollout status deployment/jev-extproc -n agentgateway-system --timeout=300s

curl -s -w ' %{http_code}\n' "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"say hi"}]}'
```

```
{"error": {"message": "routing classifier unavailable", "type": "routing_error"}} 503
```

To simulate an adapter outage, scale it to zero:

```bash
kubectl scale deployment/jev-extproc -n agentgateway-system --replicas=0
kubectl wait --for=delete pod -l app=jev-extproc -n agentgateway-system --timeout=60s

curl -s -o /dev/null -w 'auto_model: %{http_code}\n' "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"auto_model","messages":[{"role":"user","content":"say hi"}]}'
curl -s -o /dev/null -w 'named model: %{http_code}\n' "$GATEWAY_IP:8080/semantic" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-5-nano","messages":[{"role":"user","content":"say hi"}]}'
curl -s -o /dev/null -w 'other path: %{http_code}\n' "$GATEWAY_IP:8080/other"
```

```
auto_model: 500
named model: 200
other path: 404
```

The `auto_model` request fails closed with `reason=ExtProc` in the access log. With the default condition, the request that names `gpt-5-nano` skips the adapter and OpenAI answers it. `/other` returns the gateway's normal `404` for an unmatched path. With the path-only condition, the named-model request would also return `500`.

Restore the key and the adapter:

```bash
kubectl create secret generic typesafe-api-key -n agentgateway-system \
  --from-literal=TYPESAFE_AI_API_KEY=$TYPESAFE_AI_API_KEY \
  --dry-run=client -oyaml | kubectl apply -f -
kubectl scale deployment/jev-extproc -n agentgateway-system --replicas=1
kubectl rollout status deployment/jev-extproc -n agentgateway-system --timeout=300s
```

## Observability

### Latency and cost of the routing call

The access log's `request_proc_duration` field measures time spent in request policies, which here is the adapter round trip:

```bash
kubectl logs -n agentgateway-system -l gateway.networking.k8s.io/gateway-name=agentgateway-proxy --tail=50 \
  | grep "http.path=/semantic" | grep "http.status=200" \
  | sed -E 's/.*gen_ai.request.model=([^ ]+).*request_proc_duration="([^"]+)".*/\1 \2/'
```

```
gpt-5-nano 0.168115666s
gpt-5.6-luna 0.185804125s
gpt-5.6-terra 0.241888250s
gpt-5-nano 0.001076333s
```

The `auto_model` requests spent 150 to 250ms in the adapter, nearly all of it the Jev call. The request that named `gpt-5-nano` directly spent about 1ms. Jev charges for input tokens only. With this profile, a decision costs about 530 input tokens, roughly $0.00002 at the [published Jev rate](https://docs.typesafe.ai/models).

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. Each tier appears as a distinct label set, so cost and token usage split by the model the adapter chose:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics \
  | grep -o 'gen_ai_request_model="[^"]*",gen_ai_response_model="[^"]*"' | sort -u && kill $!
```

```
gen_ai_request_model="gpt-5-nano",gen_ai_response_model="gpt-5-nano-2025-08-07"
gen_ai_request_model="gpt-5.6-luna",gen_ai_response_model="gpt-5.6-luna"
gen_ai_request_model="gpt-5.6-terra",gen_ai_response_model="gpt-5.6-terra"
```

The port-forward reaches one proxy replica, so with more than one replica you may see only the tiers that replica served.

> **`auto_model` does not appear in metrics.** The rewrite happens at `PreRouting`, so the gateway only sees the *selected* model and records that in `gen_ai_request_model`. To chart requested against selected, use the adapter's `routing_decision` log line.

Output tokens per tier:

```promql
sum by (gen_ai_request_model) (increase(agentgateway_gen_ai_client_token_usage_sum{gen_ai_token_type="output"}[5m]))
```

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../../002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

## Cleanup

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system jev-router --ignore-not-found
kubectl delete httproute -n agentgateway-system semantic --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models --ignore-not-found
kubectl delete deployment,service -n agentgateway-system jev-extproc --ignore-not-found
kubectl delete configmap -n agentgateway-system jev-extproc-code jev-routing-profile --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret typesafe-api-key --ignore-not-found
```
