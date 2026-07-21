# Enterprise Agentgateway Workshop

![agentgateway-architecture.gif](images/agentgateway-architecture.gif)

## Prerequisites

Before starting this workshop, you will need:
- **Solo.io Trial License Key**: Enterprise Agentgateway requires a valid license key. You can obtain a free trial license by visiting [Solo.io](https://www.solo.io/) or contacting Solo.io sales.
- Kubernetes cluster (version 1.29.4 - 1.33.3 or compatible)
- kubectl CLI installed and configured
- helm CLI installed

See [System Requirements](labs/installation/system-requirements.md) for detailed cluster sizing, version support, and resource recommendations.

# Table of Contents

- [Installation](#installation)
- [Routing](#routing)
- [Inference](#inference)
- [Security](#security)
- [Rate Limiting](#rate-limiting)
- [Guardrails](#guardrails)
- [Transformations](#transformations)
- [MCP (Model Context Protocol)](#mcp-model-context-protocol)
- [Agent Frameworks](#agent-frameworks)
- [Identity & Delegation](#identity--delegation)
- [Evaluations](#evaluations)
- [Load Testing](#load-testing)
- [Observability](#observability)
- [Upgrades & Lifecycle](#upgrades--lifecycle)
- [Platform Engineering](#platform-engineering)

---

## Installation

> **Start here.** All other labs depend on these two.

- [001 — Install Enterprise Agentgateway](001-install-enterprise-agentgateway.md)
- [002 — Set Up UI and Monitoring Tools](002-set-up-ui-and-monitoring-tools.md)

> **OpenShift users:** Use the OpenShift-specific versions instead:
>
> [001 — Install Enterprise Agentgateway (OCP)](labs/installation/openshift/001-set-up-enterprise-agentgateway-ocp.md)
>
> [002 — Set Up Monitoring Tools (OCP)](labs/installation/openshift/002-set-up-monitoring-tools-ocp.md)

> **Air-gapped / private-registry users:** Use the air-gap variant that mirrors all chart-managed images into a private registry:
>
> [001 — Install Enterprise Agentgateway (Air-Gap)](labs/installation/airgap/001-airgap.md)

---

## Routing

- [Configure Mock OpenAI Server](labs/routing/configure-mock-openai-server.md) _(OpenAI)_
- [Basic Routing to OpenAI](labs/routing/configure-routing-openai.md) _(OpenAI)_
- [Path-per-Model Routing](labs/routing/routing-path-per-model.md) _(OpenAI)_
- [Header Matching Routing](labs/routing/routing-header-matching.md) _(OpenAI)_
- [Query Parameter Matching Routing](labs/routing/routing-query-parameter-matching.md) _(OpenAI)_
- [Body-Based Routing](labs/routing/configure-body-based-routing.md) _(OpenAI + Mock LLM)_ _(see also: Transformations)_
- [Routing to AWS Bedrock](labs/routing/configure-routing-aws-bedrock.md) _(AWS Bedrock)_
- [Routing to AWS Bedrock via API Keys](labs/routing/configure-routing-aws-bedrock-apikey.md) _(AWS Bedrock)_
- [AWS Bedrock with IRSA](labs/routing/configure-routing-aws-bedrock-irsa.md) _(AWS Bedrock / EKS)_
- [AWS Bedrock Titan Embeddings](labs/routing/configure-routing-aws-bedrock-titan-embeddings.md) _(AWS Bedrock)_
- [Routing to Anthropic](labs/routing/configure-routing-anthropic.md) _(Anthropic)_
- [Routing to Azure OpenAI](labs/routing/configure-routing-azure-openai.md) _(Azure OpenAI)_
- [Routing to Google Vertex AI](labs/routing/configure-routing-vertexai.md) _(Google Vertex AI)_
- [Routing to Google Vertex AI via Service Account](labs/routing/configure-routing-vertexai-service-account.md) _(Google Vertex AI)_
- [OpenAI Embeddings](labs/routing/configure-openai-embeddings.md) _(OpenAI)_
- [OpenAI Batch API](labs/routing/configure-openai-batches.md) _(OpenAI)_
- [OpenAI Streaming](labs/routing/openai-streaming.md) _(OpenAI)_
- [OpenAI Audio (TTS & STT)](labs/routing/openai-audio.md) _(OpenAI)_
- [OpenAI Video Generation (Sora)](labs/routing/openai-video.md) _(OpenAI)_
- [Direct Response](labs/routing/direct-response.md)
- [Timeouts and Retries](labs/routing/timeouts-and-retries.md)
- [LLM Failover](labs/routing/llm-failover.md)
- [Advanced LLM Failover Patterns](labs/routing/llm-failover-advanced.md)

---

## Inference

- [Inference Routing with vLLM](labs/inference/configure-inference-routing-with-vllm.md) _(in-cluster vLLM + Gateway API Inference Extension)_

---

## Security

- [Virtual Keys](labs/security/virtual-keys.md) _(see also: Rate Limiting)_
- [JWT Auth with RBAC](labs/security/jwt-auth-with-rbac.md)
- [JWT Auth Through a Corporate Proxy (Tunnel)](labs/security/jwt-auth-through-corporate-proxy-okta.md) _(Okta)_
- [JWT Auth Through a Corporate Proxy (Tunnel)](labs/security/jwt-auth-through-corporate-proxy-entra.md) _(Entra)_
- [TLS Termination](labs/security/tls-termination.md)
- [Frontend mTLS](labs/security/frontend-mtls.md)
- [SNI Matching](labs/security/sni-matching.md)
- [LLM BYO gRPC External Authorization (ext-authz)](labs/security/llm-byo-grpc-ext-authz.md)
- [OPA Authorization](labs/security/opa-authorization.md)
- [Web Application Firewall (WAF) for Agentic Traffic](labs/security/WAF.md) _(see also: Guardrails, MCP)_
- [MCP Eager OAuth with Auth0](labs/mcp/mcp-eager-auth-auth0.md) _(see also: MCP)_
- [MCP Eager OAuth with Okta](labs/mcp/mcp-eager-auth-okta.md) _(see also: MCP)_
- [MCP Pre-Issuance Entitlement Gating with Auth0](labs/mcp/mcp-eager-auth-auth0-pre-issuance-authz.md) _(see also: MCP, Identity & Delegation)_
- [Figma MCP with Auth0 + Token-Exchange Elicitation](labs/mcp/figma-mcp-auth0/README.md) _(see also: MCP, Identity & Delegation)_
- [Figma MCP with Microsoft Entra ID + Token-Exchange Elicitation](labs/mcp/figma-mcp-entra/README.md) _(see also: MCP, Identity & Delegation)_

---

## Rate Limiting

- [Request-Based Rate Limiting](labs/rate-limiting/request-based-rate-limiting.md)
- [Local Token-Based Rate Limiting](labs/rate-limiting/local-token-rate-limiting.md)
- [Global Token-Based Rate Limiting](labs/rate-limiting/global-token-rate-limiting.md)
- [Virtual Keys](labs/security/virtual-keys.md) _(see also: Security)_
- [MCP Tool Rate Limiting](labs/mcp/mcp-tool-rate-limiting.md) _(see also: MCP)_

---

## Guardrails

- [Built-in Guardrails](labs/guardrails/builtin-guardrails.md)
- [External Moderation (OpenAI)](labs/guardrails/external-moderation-guardrails.md)
- [Advanced Guardrails Webhook](labs/guardrails/advanced-guardrails-webhook.md)

---

## Transformations

- [Prompt Enrichment](labs/transformations/prompt-enrichment.md)
- [Request/Response Transformations](labs/transformations/transformations.md)
- [Body-Based Routing](labs/routing/configure-body-based-routing.md) _(see also: Routing)_

---

## MCP (Model Context Protocol)

- [In-Cluster MCP](labs/mcp/in-cluster-mcp.md)
- [Remote MCP](labs/mcp/remote-mcp.md)
- [Dynamic MCP](labs/mcp/dynamic-mcp.md)
- [OpenAPI to MCP — External API](labs/mcp/openapi-to-mcp-external-api.md)
- [OpenAPI to MCP — In-Cluster Deployment](labs/mcp/openapi-to-mcp-in-cluster.md)
- [MCP Tool Federation](labs/mcp/mcp-tool-federation.md)
- [Composable MCP — Tool Aggregation & Orchestration](labs/mcp/composable-mcp.md) — one tool call fans out to distinct MCP + HTTP backends and merges the responses (vs. federation, which routes each call)
- [MCP Tool Mode — Search](labs/mcp/mcp-tool-mode-search.md)
- [MCP Tool Mode — Code](labs/mcp/mcp-tool-mode-code.md)
- [MCP Tool Rate Limiting](labs/mcp/mcp-tool-rate-limiting.md) _(see also: Rate Limiting)_
- [MCP BYO gRPC External Authorization (ext-authz)](labs/mcp/mcp-byo-grpc-ext-authz.md) _(see also: Security)_
- [MCP Eager OAuth with Auth0](labs/mcp/mcp-eager-auth-auth0.md) _(see also: Security, Identity & Delegation)_
- [MCP Eager OAuth with Okta](labs/mcp/mcp-eager-auth-okta.md) _(see also: Security, Identity & Delegation)_
- [MCP Pre-Issuance Entitlement Gating with Auth0](labs/mcp/mcp-eager-auth-auth0-pre-issuance-authz.md) _(see also: Security, Identity & Delegation)_
- [Figma MCP with Auth0 + Token-Exchange Elicitation](labs/mcp/figma-mcp-auth0/README.md) — OpenAPI→MCP + eager OAuth (Auth0) + per-user Figma OAuth via elicitation _(see also: Security, Identity & Delegation)_
- [Figma MCP with Microsoft Entra ID + Token-Exchange Elicitation](labs/mcp/figma-mcp-entra/README.md) — Entra front-door variant of the Auth0 lab _(see also: Security, Identity & Delegation)_
- [CrewAI Agent with MCP and OBO Auth](labs/mcp/obo-crewai-agent-with-mcp.md) _(see also: Identity & Delegation)_

---

## Agent Frameworks

- [CrewAI](labs/agent-frameworks/crewai-with-agentgateway.md)
- [LangChain](labs/agent-frameworks/langchain-with-agentgateway.md)

---

## Agent Harnesses

- [Claude Code](labs/agent-harnesses/claude-code.md)
- [Claude Desktop](labs/agent-harnesses/claude-desktop.md)
- [Claude Code as MCP Client with Eager OAuth (Auth0)](labs/mcp/mcp-eager-auth-auth0.md#step-10--test-with-claude-code)
- [Claude Code as MCP Client with Eager OAuth (Okta)](labs/mcp/mcp-eager-auth-okta.md#step-10--test-with-claude-code)
- [Claude Code → Figma MCP with Auth0 + Elicitation](labs/mcp/figma-mcp-auth0/README.md#step-7--connect-claude-code)
- [Claude Code → Figma MCP with Microsoft Entra ID + Elicitation](labs/mcp/figma-mcp-entra/README.md#step-7--connect-claude-code)

---

## Identity & Delegation

- [OBO Token Exchange Fundamentals](labs/identity-delegation/obo-token-exchange-fundamentals.md)
- [CrewAI Agent with MCP and OBO Auth](labs/mcp/obo-crewai-agent-with-mcp.md) _(see also: MCP)_
- [Microsoft Entra ID OBO](labs/identity-delegation/msft-entra-obo.md)
- [MCP Eager OAuth with Auth0](labs/mcp/mcp-eager-auth-auth0.md) _(see also: MCP)_
- [MCP Eager OAuth with Okta](labs/mcp/mcp-eager-auth-okta.md) _(see also: MCP)_
- [MCP Pre-Issuance Entitlement Gating with Auth0](labs/mcp/mcp-eager-auth-auth0-pre-issuance-authz.md) _(see also: MCP, Security)_
- [Figma MCP with Auth0 + Token-Exchange Elicitation](labs/mcp/figma-mcp-auth0/README.md) — per-user credential forwarding to a vendor-provided IdP (Figma) via elicitation _(see also: MCP, Security)_
- [Figma MCP with Microsoft Entra ID + Token-Exchange Elicitation](labs/mcp/figma-mcp-entra/README.md) — Entra front-door + Figma elicitation (why not OBO for a vendor-provided IdP) _(see also: MCP, Security)_

---

## Evaluations

- [Evaluate OpenAI Model Performance](labs/evaluations/evaluate-openai-model-performance.md)

---

## Load Testing

- [LLM Load Testing with k6](labs/load-testing/llm-load-testing-k6.md)
- [MCP Load Testing with k6](labs/load-testing/mcp-load-testing-k6.md)

---

## Observability

- [LLM Cost Tracking with Virtual Keys](labs/observability/llm-cost-tracking-with-virtual-keys.md) _(see also: Security, Rate Limiting)_
- [Production Observability, Alerting, and Scaling](labs/observability/production-observability-alerting-and-scaling.md)

---

## Upgrades & Lifecycle

> Strategies for upgrading Enterprise Agentgateway without dropping traffic.

- [Migration Guide: v2026.5.x → v2026.7.x](labs/upgrades/migrate-v2026.5.x-to-v2026.7.x.md) — version-to-version deltas (image registry consolidation, Kubernetes floor, imagePullSecrets consolidation), prerequisites, and the exact upgrade commands
- [In-Place Rolling Upgrades — Validate Zero Downtime](labs/upgrades/in-place-rolling-upgrades.md)
- [Blue/Green Upgrades Across Namespaces](labs/upgrades/blue-green-namespaces.md)
- [Multi-Cluster Upgrades](labs/upgrades/multi-cluster-upgrades.md) — upgrade a whole cluster while a peer serves the same global LLM over an ambient multicluster mesh

---

## Platform Engineering

- [MCP Endpoints, Delegated: Self-Service Within Guardrails](labs/platform-engineering/platform-and-developer-helm-charts-mcp.md) — MCP servers are team workloads, so teams self-serve their endpoints; a platform chart owns the gateway, cost tiers, security, and URL space, and teams structurally cannot escape their tier, their prefix, or weaken a control _(see also: Rate Limiting, Security, MCP)_
- [LLM Access, Centralized: The Platform as Provider](labs/platform-engineering/centralized-llm-ops-helm-chart.md) — LLM backends are a vendor relationship, so the platform runs LLM consumption as an internal product: a model-alias catalog, per-team API keys and token budgets, one chart, no self-service
- [Networking Architecture: Every Connection in a Single-Cluster Install](labs/platform-engineering/networking-architecture.md) — how the controller, proxies, extension services, and observability stack communicate: every port, protocol, and initiator, with a connection reference table for firewall and NetworkPolicy planning

---

# Use Cases
- Support Kubernetes Gateway API
- Install Enterprise Agentgateway
- Configure agentgateway for LLM, MCP, and A2A consumption
- Unified access point for consumption of LLMs
    - LLM Providers supported in this repo:
        - OpenAI
        - AWS Bedrock (IAM credentials, API keys, and EKS IRSA)
        - Anthropic (Claude)
        - Azure OpenAI
        - Google Vertex AI (user auth and GCP service account)
    - OpenAI Embeddings support
    - AWS Bedrock Titan embeddings support
    - OpenAI Batches API support (asynchronous batch processing)
    - Streaming responses support for real-time token generation
    - OpenAI Audio API support (Text-to-Speech and Speech-to-Text)
    - OpenAI Video Generation support (Sora)
    - Claude Code CLI integration with full observability
    - Claude Desktop integration as an MCP client
    - CrewAI multi-agent workflow integration
    - LangChain multi-agent pipeline integration
- Identity & Delegation
    - OBO (On-Behalf-Of) token exchange fundamentals (impersonation + delegation)
    - CrewAI agent with MCP tools secured by OBO delegation
    - Microsoft Entra ID On-Behalf-Of (OBO) token exchange
- LLM API Key Management
    - API Key masking in logs
    - Virtual keys — per-user API keys with independent token budgets and budget isolation
- Token-based metrics from LLM
- LLM request/response metadata in Traces
- Traffic Routing patterns (path, host, header, query parameter, request body)
- Inference routing to in-cluster LLMs via the Gateway API Inference Extension (`InferencePool` + `llm-d` Endpoint Picker)
- Model Evaluations
- Security & Access Control
    - Control access with org-specific API-key
    - Control access with JWT authentication
    - JWT-based RBAC (Role-Based Access Control)
    - Frontend TLS termination
    - Frontend mTLS with client certificate validation
    - SNI (Server Name Indication) matching for multi-domain HTTPS
    - OPA authorization with custom Rego policies (ext-auth)
    - BYO gRPC external authorization (ext-authz) for LLM and MCP routes
    - Tunnel a backend connection (e.g. JWKS fetch) through a corporate forward proxy via `BackendTunnel` (`HTTPS_PROXY`-style CONNECT)
- Prompt Guard & Content Moderation
    - Comprehensive built-in Prompt Guard (prompt injection, jailbreak, PII, secrets, harmful content, encoding evasion, and more)
    - External moderation guardrails (OpenAI moderation API)
    - Advanced Webhook Prompt Guard
- Prompt Enrichment
- Rate Limiting
    - Rate Limit on a per-request basis
    - Local token-based rate limiting
    - Global token-based rate limiting
- Request/Response Transformations
    - Response transformations
    - Header enrichment for observability
- MCP (Model Context Protocol)
    - Route to in-cluster MCP servers
    - Route to external/remote MCP servers through AgentGateway
    - Dynamic MCP backends via label selectors (scale targets without editing the backend)
    - Expose existing REST APIs as MCP tools from an OpenAPI spec (external public APIs and in-cluster services)
    - Federate multiple MCP servers behind one backend (tool-name prefixing, FailOpen, per-persona tool filtering)
    - Composite MCP tools that fan out to multiple MCP + HTTP backends and merge the responses — aggregation, sequential orchestration, and structured output
    - MCP tool modes — Search (`get_tool` / `invoke_tool` meta-tools) and Code (`run_code` in a sandboxed JS runtime)
    - Secure MCP servers with JWT auth
    - BYO gRPC external authorization (ext-authz) for MCP routes
    - Eager OAuth with a pre-registered upstream IdP (Auth0 and Okta) — gateway acts as the OAuth Authorization Server visible to MCP clients
    - Pre-issuance entitlement gating — gRPC ext_authz hook gates OAuth token issuance per user, redirects denied users to a configurable URL
    - Two-layer OAuth for a real SaaS API (Figma) — eager OAuth front door (Auth0 or Microsoft Entra ID) plus per-user downstream credential forwarding to a vendor-provided IdP via token-exchange elicitation
    - Tool-level access control
    - Per-tool rate limiting for MCP traffic
    - Integration with Claude Code CLI
- Direct Response / Health Checks
    - Configure fixed responses without backend calls
- Timeouts and Retries
    - Request timeout configuration
    - Retry policies on specific error codes (503, etc.)
    - Observing how timeouts and retries interact together
- LLM Failover
    - Priority group failover between LLM providers
    - Health-based routing across multiple backends
    - Failover on rate limit errors (429)
    - Intra-priority-group failover with per-provider eviction and P2C load balancing
    - 5XX server-error failover via a CEL `unhealthyCondition`
- Load Testing with k6
    - Performance testing with k6 load generator
    - LLM and MCP traffic load testing
    - Ramping and constant load patterns
    - Integration with Grafana and Prometheus metrics
- Observability & Cost Management
    - Per-user / per-key LLM cost tracking and chargeback via access logs and PromQL
    - Production observability, alerting, and autoscaling guidance
- Platform Engineering
    - Platform/developer separation of concerns via two Helm charts
    - Platform team owns the gateway, cost tiers, security baseline (JWT/WAF), observability, and URL space
    - App teams self-serve LLM/MCP endpoints under a delegated path prefix without being able to set traffic policies
    - Structural governance via route delegation (label + namespace + prefix contract) and a strict developer `values.schema.json`
    - Assign and re-tier teams with a one-line platform values change; enable JWT gateway-wide without changing any team release


## Validated on
- Kubernetes 1.29.4 - 1.33.3
- Enterprise Agentgateway v2026.7.0


## User Stories / Acceptance Criteria

As a platform operator, I want the AI Gateway to apply granular token quotas and rate limits to requests based on either an API key or a user/group identified in a JSON Web Token (JWT), so that I can control costs, ensure fair resource usage, and have the necessary metrics and logs to enable real-time monitoring and accurate chargeback.

---

This section is a comprehensive list of all the functionality and data requirements.

#### Flexible Identification
- The AI Gateway must be able to authenticate requests using either a static API key or by validating a JWT.
- The gateway can be configured to identify the request source using the API key itself, or by extracting specific `user_id` and `group_id` claims from the JWT payload.

#### Dynamic Quotas and Rate Limiting
- The platform operator can define and apply token quotas and rate limits to individual API keys, specific users, or entire user groups.
- When a limit is reached, the gateway must enforce it by preventing further requests and returning an appropriate error response (e.g., `429 Too Many Requests`).

#### Granular Token Usage Tracking
- The gateway must track and log the number of prompt and completion tokens for every request.
- Each log record must be tagged with the relevant identifier from the request (either the `api_key_id` or the `user_id` and `group_id` from the JWT).

#### Comprehensive Logging for Troubleshooting & Auditing
- The gateway must generate structured, machine-readable logs for every request.
- These logs must include all relevant data points: a unique `request_id`, timestamp, `http_status_code`, `total_tokens`, and the specific identifier of the request source.
- The log format should be designed for easy ingestion into a centralized logging platform for long-term storage and detailed queries.

#### Metrics for Real-time Monitoring & Analysis
- The gateway must expose a `/metrics` endpoint that provides real-time, Prometheus-compatible metrics.
- The metrics must include dimensions that correspond to the request identifiers (`api_key_id`, `user_id`, `group_id`) and key usage data (`tokens_consumed_total`).
- This enables the operator to create real-time dashboards and configure automated alerts (e.g., *"Alert me if the Marketing group's token usage exceeds 80% of their monthly quota"*).

#### Data for Chargeback & Reporting
- The combined logs and metrics must provide a complete and auditable data set that can be used to generate reports for cost attribution.
- The operator can easily query or export usage data aggregated by `api_key_id`, `user_id`, or `group_id` over any given time period.

#### Management Mechanisms
- The platform operator can manually set, adjust, and reset quotas for any user, group, or API key.
- The system can also be configured to perform automated, recurring quota resets (e.g., at the beginning of each calendar month).

---

### Why This is Important

This functionality is crucial for managing an enterprise-scale AI Gateway and directly addresses critical business needs:

- **Financial Control:** By setting and enforcing token quotas, the organization can prevent unexpected cost overruns and maintain predictable spending on AI services.
- **Operational Excellence:** Real-time metrics and detailed logs provide the necessary visibility to monitor system health, troubleshoot issues quickly, and ensure the gateway is performing as expected.
- **Organizational Governance:** The ability to track and attribute costs to specific teams or departments facilitates an accurate chargeback model, making business units accountable for their resource consumption and promoting efficient usage.
- **Fair Access:** Quotas and rate limits prevent a small number of users or applications from monopolizing resources and ensure that the AI services remain available and performant for all teams.
