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
- Control access with JWT
- Control access with org-specific API-key
- Basic Prompt Guard - string, regex, builtin
- Rate Limit on a per-request basis
- Advanced Webhook Prompt Guard

# WIP (999)
- Rate limit on token (basic counter)
- Rate limit on token + header
- External Moderation
- LLM Failover

## Validated on
- Kubernetes 1.29.4 - 1.33.3
- Gloo Gateway 2.0.0-rc.1