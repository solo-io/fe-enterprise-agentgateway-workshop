# MCP Track — Enterprise Agentgateway

## Introduction

The MCP Track focuses on deploying Enterprise Agentgateway as a secure, policy-enforced aggregation layer for [Model Context Protocol (MCP)](https://spec.modelcontextprotocol.io/) traffic. Where the LLM Track is about controlling model access, this track is about controlling **tool access** — the capabilities AI agents call to read data, trigger actions, and interact with your systems.

MCP is the emerging standard for how agents discover and invoke tools. Without a gateway in front of your MCP servers, every agent framework talks directly to every tool backend, with no central place to enforce auth, rate limits, or audit who called what. Enterprise Agentgateway solves this: it becomes the single entry point for all MCP traffic, federating multiple backends, translating REST APIs into MCP tools on the fly, and enforcing OAuth, RBAC, and per-tool quotas before any agent ever touches a tool.

---

## Goals & Expectations

After completing this track you should be able to:

- **Connect MCP servers** — route agent traffic to in-cluster and remote MCP backends through a single stable endpoint
- **Expose REST APIs as MCP tools** — wrap any OpenAPI-documented service as a tool set without writing MCP server code
- **Federate multiple backends** — aggregate tools from many servers behind one endpoint, with per-persona filtering and prefixed tool names
- **Specialize tool exposure** — use Search mode (`get_tool` / `invoke_tool` meta-tools) and Code mode (`run_code` in a sandboxed JS runtime)
- **Enforce OAuth at the gateway** — configure Eager OAuth with Auth0 or Okta so the gateway is the OAuth Authorization Server visible to MCP clients
- **Gate entitlements pre-issuance** — block unauthorized users from receiving tokens before they're ever issued
- **Apply per-tool rate limits** — prevent individual tools from being hammered by a single agent or user
- **Propagate user identity with OBO** — carry the end-user's identity through the agent chain so tools act on behalf of the real person
- **Connect real agent frameworks** — wire CrewAI, LangChain, Claude Code, and Claude Desktop to the gateway end-to-end
- **Load test and observe** — validate tool throughput and set up production-grade dashboards

---

## Prerequisites

| Requirement | Notes |
|---|---|
| LLM Track — Installation | Labs 001 and 002 must be complete (gateway + monitoring running) |
| `kubectl` CLI | Configured against your cluster |
| Solo.io Trial License Key | Already applied in the LLM Track install |
| LLM provider API key | Required for agent framework labs (OpenAI recommended) |
| Auth0 or Okta account | Required for OAuth labs only |
| Claude Code or Claude Desktop | Required for agent harness labs |

> If you haven't done the LLM Track yet, start with [001 — Install Enterprise Agentgateway](../001-install-enterprise-agentgateway.md) and [002 — Set Up UI and Monitoring Tools](../002-set-up-ui-and-monitoring-tools.md) before continuing here.

---

## Curriculum by Use Case

### Use Case 1 — MCP Server Connectivity

**Value:** Give agents a single, stable gateway endpoint to call regardless of where MCP tool servers physically run — in the cluster, in a remote data center, or dynamically scaled.

| Lab | What you'll learn |
|---|---|
| [In-Cluster MCP](../labs/mcp/in-cluster-mcp.md) | Route agent traffic to an MCP server running inside the Kubernetes cluster |
| [Remote MCP](../labs/mcp/remote-mcp.md) | Proxy agent traffic to an MCP server running outside the cluster |
| [Dynamic MCP](../labs/mcp/dynamic-mcp.md) | Use label selectors to automatically pick up new MCP backend pods — no backend edits needed at scale |

---

### Use Case 2 — Wrap Existing REST APIs as MCP Tools

**Value:** Instantly expose any documented REST API as an MCP tool set. Your agents get structured tool definitions; you get centralized auth and rate limiting — no MCP server code required.

| Lab | What you'll learn |
|---|---|
| [OpenAPI to MCP — External API](../labs/mcp/openapi-to-mcp-external-api.md) | Point the gateway at a public OpenAPI spec and expose its operations as MCP tools |
| [OpenAPI to MCP — In-Cluster Deployment](../labs/mcp/openapi-to-mcp-in-cluster.md) | Do the same for an in-cluster service, keeping all traffic inside the mesh |

---

### Use Case 3 — Tool Federation & Specialization

**Value:** Aggregate tools from multiple MCP servers behind one endpoint. Filter tool exposure per persona, prefix names to avoid collisions, and specialize how tools are presented to agents.

| Lab | What you'll learn |
|---|---|
| [MCP Tool Federation](../labs/mcp/mcp-tool-federation.md) | Merge multiple MCP backends behind one backend address with tool-name prefixing, FailOpen, and per-persona filtering |
| [MCP Tool Mode — Search](../labs/mcp/mcp-tool-mode-search.md) | Expose `get_tool` and `invoke_tool` meta-tools so agents can discover and call tools programmatically |
| [MCP Tool Mode — Code](../labs/mcp/mcp-tool-mode-code.md) | Expose a `run_code` tool that executes JavaScript in a sandboxed runtime on the gateway |

---

### Use Case 4 — MCP Security & Authorization

**Value:** Enforce zero-trust access at the tool level. Agents must present valid credentials before the gateway forwards any tool call — and you can gate access down to individual tool names.

| Lab | What you'll learn |
|---|---|
| [MCP BYO gRPC External Authorization](../labs/mcp/mcp-byo-grpc-ext-authz.md) | Integrate your own ext-authz gRPC service for custom tool-level policy |
| [MCP Eager OAuth with Auth0](../labs/mcp/mcp-eager-auth-auth0.md) | Configure the gateway as the OAuth Authorization Server for Auth0; agents get tokens directly from the gateway |
| [MCP Eager OAuth with Okta](../labs/mcp/mcp-eager-auth-okta.md) | Same pattern with Okta as the backing IdP |
| [MCP Pre-Issuance Entitlement Gating with Auth0](../labs/mcp/mcp-eager-auth-auth0-pre-issuance-authz.md) | Add a gRPC ext-authz hook that checks entitlements before issuing OAuth tokens — denied users are redirected before they get any credentials |
| [Web Application Firewall (WAF) for Agentic Traffic](../labs/security/WAF.md) | Attach a `WAFPolicy` to harden the HTTP surface and block tool-call payload abuse (command-exec, file-exfil signatures) and credential leakage — the deterministic layer beneath semantic guardrails |

---

### Use Case 5 — Per-Tool Rate Limiting

**Value:** Prevent tool abuse and enforce fair-use quotas per agent, user, or group — at the individual tool level, not just the connection level.

| Lab | What you'll learn |
|---|---|
| [MCP Tool Rate Limiting](../labs/mcp/mcp-tool-rate-limiting.md) | Configure per-tool rate limits and observe enforcement in Grafana |

---

### Use Case 6 — Identity Delegation (On-Behalf-Of)

**Value:** When an AI agent calls a tool, the tool should know *which human* initiated the request — not just which service account. OBO token exchange carries the end user's identity through the agent chain so you get real attribution, not service-level attribution.

| Lab | What you'll learn |
|---|---|
| [OBO Token Exchange Fundamentals](../labs/identity-delegation/obo-token-exchange-fundamentals.md) | Understand impersonation vs. delegation; walk through an OBO token exchange end-to-end |
| [Microsoft Entra ID OBO](../labs/identity-delegation/msft-entra-obo.md) | Configure OBO token exchange with Microsoft Entra ID (formerly Azure AD) |
| [CrewAI Agent with MCP and OBO Auth](../labs/mcp/obo-crewai-agent-with-mcp.md) | Wire a real CrewAI multi-agent workflow through the gateway with OBO delegation on every tool call |

---

### Use Case 7 — Agent Framework & Client Integrations

**Value:** Validate that real-world frameworks and developer tools connect cleanly through the gateway — and that policy enforcement works end-to-end from the client to the tool.

| Lab | What you'll learn |
|---|---|
| [CrewAI with Agentgateway](../labs/agent-frameworks/crewai-with-agentgateway.md) | Connect a CrewAI multi-agent workflow to LLM and MCP backends through the gateway |
| [LangChain with Agentgateway](../labs/agent-frameworks/langchain-with-agentgateway.md) | Connect a LangChain agent pipeline to the gateway |
| [Claude Code](../labs/agent-harnesses/claude-code.md) | Configure Claude Code CLI to use the gateway as its MCP server |
| [Claude Desktop](../labs/agent-harnesses/claude-desktop.md) | Configure Claude Desktop as an MCP client pointing at the gateway |
| [Claude Code with Eager OAuth (Auth0)](../labs/mcp/mcp-eager-auth-auth0.md#step-10--test-with-claude-code) | Use Claude Code as an OAuth-authenticated MCP client against the Auth0-secured gateway |
| [Claude Code with Eager OAuth (Okta)](../labs/mcp/mcp-eager-auth-okta.md#step-10--test-with-claude-code) | Same with Okta as the IdP |

---

### Use Case 8 — Load Testing & Production Observability

**Value:** Validate tool throughput, surface bottlenecks under realistic agent traffic, and configure dashboards and alerts before you're in a production incident.

| Lab | What you'll learn |
|---|---|
| [MCP Load Testing with k6](../labs/load-testing/mcp-load-testing-k6.md) | Generate realistic MCP tool-call traffic at ramping and constant load; observe in Grafana |
| [Production Observability, Alerting, and Scaling](../labs/observability/production-observability-alerting-and-scaling.md) | Configure Prometheus alerts, Grafana dashboards, and HPA rules for the gateway |

---

## Suggested Completion Order

Start with connectivity, then layer on security, then add identity and framework integrations:

```
Installation (LLM Track 001 + 002)
  → MCP Connectivity → Wrap REST APIs → Tool Federation
  → MCP Security → Per-Tool Rate Limiting
  → Identity Delegation (OBO)
  → Agent Framework Integrations
  → Load Testing & Observability
```

The Security (Use Case 4) and Identity Delegation (Use Case 6) sections have internal ordering dependencies noted at the top of each lab. Everything else within a section can be taken in any order.

---

## How MCP and LLM Tracks Relate

These tracks share the same gateway and monitoring stack. Labs in the MCP Track that involve authentication (JWT, OAuth) build on concepts from the LLM Track's Security section — but they are self-contained and include all necessary configuration steps. You do not need to complete the full LLM Track before starting the MCP Track; only the Installation labs are required.
