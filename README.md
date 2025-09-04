# Gloo Gateway V2 + Agentgateway Workshop

# Objectives
- Install Gloo Gateway V2 controller
- Install Jaeger
- Configure agentgateway proxy with tracing enabled
- Configure basic routing to OpenAI LLM Backend
- Validate request/response tracing information in the Jaeger UI
- Configure path-per-model Routing Example
- Configure Fixed Path + Header Matching Routing Example
- Configure Fixed Path + Query Parameter Matching Routing Example

# Use Cases
- Unified access point for consumption of LLMs
- Authenticate with API keys
- Token-based metrics from LLM
- LLM request/response metadata in Traces
- Traffic Routing patterns

# WIP (999)
- Basic Prompt Guard - string, regex, builtin
- AWS Bedrock Provider support
- Rate limit on token (basic counter)
- Rate limit on token + header
- Advanced Webhook Prompt Guard
- External Moderation
- Control access with JWT
- Control access with API-key
- LLM Failover


## Validated on
- Kubernetes 1.33.3
- Gloo Gateway 2.0.0-beta2