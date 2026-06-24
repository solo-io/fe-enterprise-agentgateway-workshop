# LLM Track — Enterprise Agentgateway

## Introduction

The LLM Track guides platform engineers through deploying Enterprise Agentgateway as a centralized, secure, and observable gateway for all LLM traffic in an organization. Rather than every team managing their own SDK configuration and provider credentials, you'll stand up a single control plane that abstracts OpenAI, AWS Bedrock, Anthropic, Azure OpenAI, and Google Vertex AI behind one endpoint — and then layer on policy, cost controls, and observability on top.

By the end of this track you'll have hands-on experience with the complete lifecycle of production LLM infrastructure: from first install through routing, security, guardrails, rate limiting, cost attribution, and resilience validation.

---

## Goals & Expectations

After completing this track you should be able to:

- **Unify LLM access** — route traffic to multiple providers from a single gateway endpoint without changing client code
- **Enforce routing policies** — route by path, header, query parameter, or request body to control which model handles which request
- **Secure the gateway** — apply API key masking, virtual keys, JWT-based RBAC, TLS/mTLS, OPA, and BYO external authorization
- **Control costs with rate limits** — enforce per-request, per-user, and global token quotas; reset them on a schedule
- **Apply content guardrails** — block prompt injection, PII, and harmful content before requests reach the model
- **Enrich and transform** — inject system prompts, rewrite headers, and shape request/response payloads
- **Track spend and attribute costs** — generate per-user, per-key chargeback data via Prometheus and access logs
- **Validate resilience** — configure failover across providers, health-based routing, and load-test under realistic traffic

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Kubernetes cluster | v1.29.4 – v1.33.3 (or compatible) |
| Solo.io Trial License Key | Required for Enterprise Agentgateway |
| `kubectl` CLI | Configured against your cluster |
| `helm` CLI | v3+ |
| LLM provider API key | OpenAI is recommended for the quickstart; others are optional |

See [System Requirements](../labs/installation/system-requirements.md) for detailed cluster sizing and resource recommendations.

> **OpenShift users:** Use the OCP-specific installation labs linked in the [Installation](#use-case-1-installation) section.

---

## Curriculum by Use Case

### Use Case 1 — Installation

**Value:** Get a running gateway and monitoring stack as the foundation for every subsequent lab.

| Lab | What you'll do |
|---|---|
| [001 — Install Enterprise Agentgateway](../labs/installation/001-install-enterprise-agentgateway.md) | Deploy the gateway via Helm, apply a license key, and verify the control plane |
| [002 — Set Up UI and Monitoring Tools](../labs/installation/002-set-up-ui-and-monitoring-tools.md) | Install Prometheus, Grafana, and the demo UI |

> OpenShift: [001 (OCP)](../labs/installation/openshift/001-set-up-enterprise-agentgateway-ocp.md) · [002 (OCP)](../labs/installation/openshift/002-set-up-monitoring-tools-ocp.md)

---

### Use Case 2 — Unified LLM Access Point

**Value:** Replace per-team, per-provider SDK configurations with one stable gateway endpoint. Teams get the same OpenAI-compatible API regardless of which model is behind it.

| Lab | Provider |
|---|---|
| [Configure Mock OpenAI Server](../labs/routing/configure-mock-openai-server.md) | Mock — safe sandbox for testing |
| [Basic Routing to OpenAI](../labs/routing/configure-routing-openai.md) | OpenAI |
| [Routing to AWS Bedrock](../labs/routing/configure-routing-aws-bedrock.md) | AWS Bedrock |
| [Routing to AWS Bedrock via API Keys](../labs/routing/configure-routing-aws-bedrock-apikey.md) | AWS Bedrock |
| [AWS Bedrock with IRSA](../labs/routing/configure-routing-aws-bedrock-irsa.md) | AWS Bedrock / EKS |
| [Routing to Anthropic](../labs/routing/configure-routing-anthropic.md) | Anthropic |
| [Routing to Azure OpenAI](../labs/routing/configure-routing-azure-openai.md) | Azure OpenAI |
| [Routing to Google Vertex AI](../labs/routing/configure-routing-vertexai.md) | Google Vertex AI |
| [Routing to Google Vertex AI via Service Account](../labs/routing/configure-routing-vertexai-service-account.md) | Google Vertex AI |

---

### Use Case 3 — Advanced Routing Strategies

**Value:** Route intelligently based on request content, headers, or path to optimize for cost, latency, or model capability — and keep traffic moving when providers go down.

| Lab | What you'll learn |
|---|---|
| [Path-per-Model Routing](../labs/routing/routing-path-per-model.md) | Map URL paths to different models or providers |
| [Header Matching Routing](../labs/routing/routing-header-matching.md) | Route based on HTTP headers (e.g., `X-Model`) |
| [Query Parameter Matching Routing](../labs/routing/routing-query-parameter-matching.md) | Route based on URL query params |
| [Body-Based Routing](../labs/routing/configure-body-based-routing.md) | Route on request body fields (e.g., `model`, `stream`) |
| [Timeouts and Retries](../labs/routing/timeouts-and-retries.md) | Configure per-route timeouts and retry policies |
| [LLM Failover](../labs/routing/llm-failover.md) | Priority-group failover between providers |
| [Advanced LLM Failover Patterns](../labs/routing/llm-failover-advanced.md) | Health-based routing, 429 failover, intra-group P2C load balancing |

---

### Use Case 4 — Security & Access Control

**Value:** Enforce zero-trust access to LLM endpoints — from simple API key gating through full mTLS and policy-engine-driven authorization.

| Lab | What you'll learn |
|---|---|
| [API Key Masking](../labs/security/api-key-masking.md) | Prevent upstream provider keys from appearing in logs |
| [Virtual Keys](../labs/security/virtual-keys.md) | Issue per-user keys with independent token budgets |
| [JWT Auth with RBAC](../labs/security/jwt-auth-with-rbac.md) | Validate JWTs and enforce role-based access |
| [TLS Termination](../labs/security/tls-termination.md) | Terminate HTTPS at the gateway |
| [Frontend mTLS](../labs/security/frontend-mtls.md) | Require and validate client certificates |
| [SNI Matching](../labs/security/sni-matching.md) | Route HTTPS traffic by hostname without decryption |
| [OPA Authorization](../labs/security/opa-authorization.md) | Write custom Rego policies for fine-grained access control |
| [LLM BYO gRPC External Authorization](../labs/security/llm-byo-grpc-ext-authz.md) | Integrate your own ext-authz service |

---

### Use Case 5 — Rate Limiting & Cost Control

**Value:** Prevent runaway usage, enforce fair-use policies, and attribute spend back to the teams and users that generated it.

| Lab | What you'll learn |
|---|---|
| [Request-Based Rate Limiting](../labs/rate-limiting/request-based-rate-limiting.md) | Limit requests per second/minute by API key or user |
| [Local Token-Based Rate Limiting](../labs/rate-limiting/local-token-rate-limiting.md) | Enforce per-instance token quotas without a shared store |
| [Global Token-Based Rate Limiting](../labs/rate-limiting/global-token-rate-limiting.md) | Enforce cluster-wide token quotas with a Redis backend |
| [Virtual Keys](../labs/security/virtual-keys.md) | Per-key token budgets with budget isolation |
| [LLM Cost Tracking](../labs/observability/llm-cost-tracking.md) | Prometheus metrics + PromQL for per-user chargeback |

---

### Use Case 6 — Content Safety & Guardrails

**Value:** Stop prompt injection, jailbreaks, PII leakage, and harmful content before requests reach the model — or before responses reach the client.

| Lab | What you'll learn |
|---|---|
| [Built-in Guardrails](../labs/guardrails/builtin-guardrails.md) | Configure Agentgateway's prompt guard (injection, jailbreak, PII, secrets, encoding evasion) |
| [External Moderation (OpenAI)](../labs/guardrails/external-moderation-guardrails.md) | Route traffic through OpenAI's moderation API as a sidecar |
| [Advanced Guardrails Webhook](../labs/guardrails/advanced-guardrails-webhook.md) | Call a custom webhook for policy decisions |
| [Prompt Enrichment](../labs/transformations/prompt-enrichment.md) | Inject system prompts or metadata before forwarding |
| [Request/Response Transformations](../labs/transformations/transformations.md) | Rewrite headers, mutate body fields, shape responses |

---

### Use Case 7 — Embeddings, Batches & Multimodal

**Value:** Extend the gateway beyond chat completions to cover the full API surface — embeddings, async batch processing, audio, and video — without provider-specific client changes.

| Lab | What you'll learn |
|---|---|
| [OpenAI Embeddings](../labs/routing/configure-openai-embeddings.md) | Route embedding requests through the gateway |
| [AWS Bedrock Titan Embeddings](../labs/routing/configure-routing-aws-bedrock-titan-embeddings.md) | Route Bedrock embedding requests |
| [OpenAI Batch API](../labs/routing/configure-openai-batches.md) | Submit and retrieve async batch jobs |
| [OpenAI Streaming](../labs/routing/openai-streaming.md) | Stream responses for real-time token generation |
| [OpenAI Audio (TTS & STT)](../labs/routing/openai-audio.md) | Route Text-to-Speech and Speech-to-Text requests |
| [OpenAI Video Generation (Sora)](../labs/routing/openai-video.md) | Route Sora video generation requests |

---

### Use Case 8 — Inference Routing (In-Cluster Models)

**Value:** Route to self-hosted LLMs running in the cluster using the Gateway API Inference Extension — ideal for teams that need data sovereignty or lower-latency inference.

| Lab | What you'll learn |
|---|---|
| [Inference Routing with vLLM](../labs/inference/configure-inference-routing-with-vllm.md) | Configure `InferencePool` + `llm-d` Endpoint Picker with an in-cluster vLLM deployment |

---

### Use Case 9 — Resilience, Evaluations & Load Testing

**Value:** Validate model quality, test capacity limits, and tune autoscaling before a production rollout.

| Lab | What you'll learn |
|---|---|
| [Evaluate OpenAI Model Performance](../labs/evaluations/evaluate-openai-model-performance.md) | Run structured LLM evaluations |
| [LLM Load Testing with k6](../labs/load-testing/llm-load-testing-k6.md) | Ramping and constant load patterns; integration with Grafana |
| [Production Observability, Alerting, and Scaling](../labs/observability/production-observability-alerting-and-scaling.md) | Dashboards, alert rules, and HPA guidance |

---

## Suggested Completion Order

For a first run through the track, complete the use cases in order. You can stop after Use Case 5 for a solid production baseline, then add Use Cases 6–9 as your needs grow.

```
Installation → Unified Access → Advanced Routing → Security → Rate Limiting
    → Guardrails → Multimodal → Inference → Load Testing
```

Labs within each use case can generally be taken in any order unless they share prerequisites noted at the top of the lab file.
