# Networking Architecture

How the components of a single-cluster Enterprise Agentgateway installation communicate over the network: every connection, its port, its protocol, and which side initiates it. Use this page to reason about firewall rules, NetworkPolicies, and what traffic to expect between namespaces.

## Overview

Enterprise Agentgateway is a single-cluster gateway with two layers:

- **Control plane**: the `enterprise-agentgateway` controller watches Kubernetes Gateway API resources and Enterprise Agentgateway CRs, translates them into proxy configuration, provisions proxy deployments for each `Gateway`, and serves configuration over xDS. It also writes status back to the resources it watches.
- **Data plane**: one or more agentgateway proxies. Each `Gateway` resource gets its own proxy deployment and LoadBalancer service. Proxies hold configuration in memory and route traffic to LLM providers, MCP servers, and in-cluster services.

A default installation also deploys **extension services** (external auth, rate limiter, WAF, and a Redis cache) that the proxy calls over gRPC only when a policy requires them, and an **observability stack** (OTLP telemetry collector, ClickHouse, Solo UI). Metrics endpoints are exposed for a bring-your-own monitoring stack; see [Telemetry flow](#telemetry-flow).

![Networking architecture: agents and clients enter through the gateway's load balancer, while in-cluster clients reach the proxy directly over its ClusterIP Service; proxies dial out to the controller for xDS config, call extension services over gRPC when policies attach, forward LLM and MCP traffic to external providers over TLS and to an in-cluster MCP server over StreamableHTTP, and export telemetry to the collector](../../images/platform-engineering/networking-architecture.png)

## Connection reference

Arrows in the diagram point away from the side that opens the connection. This table is the complete list: every network connection in the deployment.

| # | Source (initiator) | Destination | Port | Protocol | Purpose |
|---|--------------------|-------------|------|----------|---------|
| 1 | Agents & clients (Claude Code, MCP Inspector, curl, apps) | `agentgateway-proxy` LoadBalancer | `<user-defined>` | HTTP | LLM and MCP traffic entry point |
| 2 | In-cluster agents & clients | `agentgateway-proxy` ClusterIP Service | `<user-defined>` | HTTP | Direct in-cluster access (bypasses the load balancer) |
| 3 | `enterprise-agentgateway` controller | Kubernetes API server | 443 | TLS | Watch Gateway API + Enterprise CRs, provision proxies, write status |
| 4 | `agentgateway-proxy` (both replicas) | `enterprise-agentgateway` controller | 9978 | gRPC (xDS/ADS) | Proxy initiates a streaming connection; configuration flows back over it |
| 5 | `agentgateway-proxy` | `ext-auth` | 8083 | gRPC | External auth (only when an extAuth policy is attached) |
| 6 | `agentgateway-proxy` | `rate-limiter` | 8083 | gRPC | Rate limiting (only when an entRateLimit policy is attached) |
| 7 | `agentgateway-proxy` | `waf-server` | 8084 | gRPC | WAF inspection (only when a WAF policy is attached) |
| 8 | `ext-auth`, `rate-limiter` | `ext-cache` (Redis) | 6379 | TCP | Shared state: rate-limit counters, auth sessions |
| 9 | `agentgateway-proxy` | `api.openai.com` | 443 | TLS | LLM provider backend (`/openai` route) |
| 10 | `agentgateway-proxy` | `search.solo.io` | 443 | TLS | Remote MCP server, StreamableHTTP (`/mcp` route) |
| 11 | `agentgateway-proxy` | `internal-mcp-tool.mcp` | 80 | StreamableHTTP | In-cluster MCP server backend (optional, when deployed) |
| 12 | `agentgateway-proxy` | `solo-enterprise-telemetry-collector.solo-ui` | 4317 | gRPC (OTLP) | Trace export (configured by the `tracing` policy) |
| 13 | Telemetry collector | ClickHouse | 9000 | TCP | Trace and log storage |
| 14 | Solo UI | ClickHouse | 9000 | TCP | Reads traces and logs for display |

## Traffic flows

### Configuration flow

The controller watches the Kubernetes API server for Gateway API resources (`Gateway`, `HTTPRoute`) and Enterprise CRs (`EnterpriseAgentgatewayBackend`, `EnterpriseAgentgatewayPolicy`), translates them into agentgateway configuration, and serves the result over xDS. The direction matters for network policy: **proxies dial out to the controller on 9978** and hold a streaming gRPC connection; the controller never opens connections into the data plane. Configuration updates arrive as incremental deltas over that stream, and the controller reports acceptance back into each resource's `status`.

### Request flow

Clients reach a proxy through its Gateway's LoadBalancer on the port defined by the Gateway's listener. In-cluster clients skip the load balancer and call the proxy's ClusterIP Service directly on the same port, either its ClusterIP and port, or its Kubernetes DNS name. The proxy matches the request path to a route (`/openai` to the OpenAI backend, `/mcp` to the remote MCP backend) and applies any attached policies before forwarding. Policies that need an extension service (external auth, rate limiting, WAF) trigger a gRPC call to that service inline with the request; routes without such policies never touch the extension services. The proxy then opens a connection to the backend: a TLS connection to `api.openai.com:443` with the provider API key injected from the referenced Secret, a TLS connection to `search.solo.io:443` speaking StreamableHTTP for MCP, or a StreamableHTTP connection to the in-cluster `internal-mcp-tool` server in the `mcp` namespace.

### Telemetry flow

Three telemetry paths run concurrently with traffic. The `tracing` policy has the proxy export OTLP spans over gRPC to the telemetry collector in `solo-ui`, which writes them to ClickHouse; the Solo UI reads ClickHouse to display traces. Prometheus-compatible metrics endpoints are exposed for a bring-your-own monitoring stack (e.g. Datadog, Dynatrace): the proxy on 15020, the controller on 9092, and the extension services on 9091. Access logs go to the proxy's stdout (configured by the `access-logs` policy) and are readable with `kubectl logs`.
