# Gloo Gateway V2 + Agentgateway Workshop

# Objectives
- Install Gloo Gateway V2 controller
- Install Jaeger
- Configure agentgateway proxy with tracing enabled
- Configure basic routing to OpenAI LLM Provider Backend
- Validate request/response tracing information in the Jaeger UI
- Configure path-per-model Routing Example
- Configure Fixed Path + Header Matching Routing Example
- Configure Fixed Path + Query Parameter Matching Routing Example
- Evaluate model performance using PromptFoo
- Configure routing to AWS Bedrock LLM Provider Backend
- Configure and validate org-specific API-Key auth
- Configure and validate JWT Auth

# Use Cases
- Unified access point for consumption of LLMs
- Authenticate with API keys
- Token-based metrics from LLM
- LLM request/response metadata in Traces
- Traffic Routing patterns
- AWS Bedrock provider support
- Model Evaluations
- Control access with JWT
- Control access with org-specific API-key

# WIP (999)
- Basic Prompt Guard - string, regex, builtin
- Rate limit on token (basic counter)
- Rate limit on token + header
- Advanced Webhook Prompt Guard
- External Moderation
- LLM Failover

## Validated on
- Kubernetes 1.33.3
- Gloo Gateway 2.0.0-beta3