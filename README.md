# Gloo Gateway V2 + Agentgateway Workshop

# Labs
- [001-set-up-gloo-gateway-controller.md](001-set-up-gloo-gateway-controller.md)
- [002-configure-agentgateway-with-tracing.md](002-configure-agentgateway-with-tracing.md)
- [003-configure-basic-routing-to-openai.md](003-configure-basic-routing-to-openai.md)
- [004a-path-per-model-routing-example.md](004a-path-per-model-routing-example.md)
- [004b-fixed-path-header-matching-routing-example.md](004b-fixed-path-header-matching-routing-example.md)
- [004c-fixed-path-queryparameter-matching-routing-example.md](004c-fixed-path-queryparameter-matching-routing-example.md)
- [005-evaluate-openai-model-performance.md](005-evaluate-openai-model-performance.md)
- [006-configure-routing-to-aws-bedrock.md](006-configure-routing-to-aws-bedrock.md)
- [007-api-key-masking.md](007-api-key-masking.md)
- [008-jwt-auth.md](008-jwt-auth.md)
- [009-configure-basic-routing-to-anthropic.md](009-configure-basic-routing-to-anthropic.md)
- [010-enrich-prompts.md](010-enrich-prompts.md)
- [011-advanced-guardrails-webhook.md](011-advanced-guardrails-webhook.md)

# Use Cases
- Support Kubernetes Gateway API
- Install Gloo Gateway
- Configure agentgateway for LLM, MCP, and A2A consumption
- Unified access point for consumption of LLMs
    - In this repo:
        - OpenAI
        - AWS Bedrock
        - Claude
- LLM API Key Management
- Token-based metrics from LLM
- LLM request/response metadata in Traces
- Traffic Routing patterns (path, host, queryparameter)
- AWS Bedrock provider support
- Model Evaluations
- Control access with org-specific API-key
- Control access with JWT
- Basic Prompt Guard - string, regex, builtin
- Prompt Enrichment
- Advanced Webhook Prompt Guard
- Rate Limit on a per-request basis
- Rate Limit on a per-token basis

# WIP (999)
- 999-basic-guardrails.md
- 999-request-based-rate-limiting.md
    - The platform operator can configure rate limits based on per-token or per-request
    - The platform operator can define and apply token quotas and rate limits to individual API keys, specific users, or entire user groups.
    - When a limit is reached, the gateway must enforce it by preventing further requests and returning an appropriate error response (e.g., 429 Too Many Requests).
- Rate limit on token (basic counter)
- Rate limit on token + header
- External Moderation
- LLM Failover

## Validated on
- Kubernetes 1.29.4 - 1.33.3
- Gloo Gateway 2.0.0-rc.1



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
