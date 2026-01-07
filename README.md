# Enterprise Agentgateway Workshop

# Labs
- [001-set-up-enterprise-agentgateway.md](001-set-up-enterprise-agentgateway.md)
- [002-set-up-monitoring-tools.md](002-set-up-monitoring-tools.md)
- [003-configure-mock-openai-server.md](003-configure-mock-openai-server.md)
- [004-configure-basic-routing-to-openai.md](004-configure-basic-routing-to-openai.md)
- [004a-path-per-model-routing-example.md](004a-path-per-model-routing-example.md)
- [004b-fixed-path-header-matching-routing-example.md](004b-fixed-path-header-matching-routing-example.md)
- [004c-fixed-path-queryparameter-matching-routing-example.md](004c-fixed-path-queryparameter-matching-routing-example.md)
- [005-evaluate-openai-model-performance.md](005-evaluate-openai-model-performance.md)
- [006-configure-routing-to-aws-bedrock.md](006-configure-routing-to-aws-bedrock.md)
- [006a-configure-routing-to-aws-bedrock-apikey.md](006a-configure-routing-to-aws-bedrock-apikey.md)
- [007-api-key-masking.md](007-api-key-masking.md)
- [008-jwt-auth-with-rbac.md](008-jwt-auth-with-rbac.md)
- [009-configure-basic-routing-to-anthropic.md](009-configure-basic-routing-to-anthropic.md)
- [010-enrich-prompts.md](010-enrich-prompts.md)
- [011-basic-guardrails.md](011-basic-guardrails.md)
- [012-external-moderation-openai-guardrails.md](012-external-moderation-openai-guardrails.md)
- [013-advanced-guardrails-webhook.md](013-advanced-guardrails-webhook.md)
- [014-request-based-rate-limiting.md](014-request-based-rate-limiting.md)
- [015-local-token-based-rate-limiting.md](015-local-token-based-rate-limiting.md)
- [016-global-token-based-rate-limiting.md](016-global-token-based-rate-limiting.md)
- [017-transformations.md](017-transformations.md)
- [018-mcp.md](018-mcp.md)
- [019-configure-direct-response.md](019-configure-direct-response.md)
- [020-configure-basic-routing-to-azureopenai.md](020-configure-basic-routing-to-azureopenai.md)
- [021-configure-basic-routing-to-vertexai.md](021-configure-basic-routing-to-vertexai.md)
- [022-configure-openai-embeddings.md](022-configure-openai-embeddings.md)

# Use Cases
- Support Kubernetes Gateway API
- Install Enterprise Agentgateway
- Configure agentgateway for LLM, MCP, and A2A consumption
- Unified access point for consumption of LLMs
    - LLM Providers supported in this repo:
        - OpenAI
        - AWS Bedrock (IAM credentials and API keys)
        - Anthropic (Claude)
        - Azure OpenAI
        - Google Vertex AI
    - OpenAI Embeddings support
- LLM API Key Management
    - API Key masking in logs
- Token-based metrics from LLM
- LLM request/response metadata in Traces
- Traffic Routing patterns (path, host, header, queryparameter)
- Model Evaluations
- Security & Access Control
    - Control access with org-specific API-key
    - Control access with JWT authentication
    - JWT-based RBAC (Role-Based Access Control)
- Prompt Guard & Content Moderation
    - Basic Prompt Guard (string, regex, builtin)
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
    - Route to MCP servers
    - Secure MCP servers with JWT auth
    - Tool-level access control
- Direct Response / Health Checks
    - Configure fixed responses without backend calls

## WIP / to-do / Known Issues (999)
- LLM Failover

## Validated on
- Kubernetes 1.29.4 - 1.33.3
- Enterprise Agentgateway 2.1.0-beta2


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
