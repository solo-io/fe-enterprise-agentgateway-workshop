# agentgateway-developer

Installed per application team, once the platform team has onboarded that
team in [`agentgateway-platform`](../agentgateway-platform/README.md). A team
uses it to self-serve MCP endpoints under the path prefix the platform
assigned it — configuring only what it owns.

Its `values.schema.json` deliberately has no field for rate limits, auth
policy, WAF, or logging. Those are platform-owned and attached to the
team's parent route, so every endpoint the team adds inherits them
automatically. A team cannot express a traffic policy here even if it tries.

> Looking for LLM endpoints? Those are served by the platform-owned
> [`agentgateway-llm-ops`](../agentgateway-llm-ops/README.md) chart instead.
> This chart is MCP-only.

## What it renders

- One `HTTPRoute` per entry in `endpoints`, labeled `team: <name>` and
  path-prefixed `/teams/<team><path>`
- One `EnterpriseAgentgatewayBackend` per endpoint — an MCP backend
  proxying the endpoint's `targets`
- No `HTTPRoute` ever sets `parentRefs` — routes attach to the gateway only
  through the platform's delegation, never directly

## The contract with agentgateway-platform

| Contract element   | Set by                          | Value for `team-alpha` |
|---------------------|-----------------------------------|-------------------------|
| Delegation label    | this chart (`team` value)         | `team: team-alpha`      |
| Path prefix         | this chart, from `team` value      | `/teams/team-alpha`     |
| Namespace           | wherever this release is installed | `team-alpha`            |
| Cost tier           | inherited from the platform — not settable here | e.g. `gold` |

## Install

```bash
helm install team-alpha charts/agentgateway-developer \
  -n team-alpha \
  --values team-alpha-values.yaml
```

Minimal `team-alpha-values.yaml`:

```yaml
team: team-alpha
endpoints:
  - name: tools
    type: mcp
    path: /mcp
    targets:
      - name: arxiv
        host: mcp-airxiv.team-alpha.svc.cluster.local
        port: 8080
        protocol: StreamableHTTP
```

Because `team` is mandatory with no default, bare `helm lint` or
`helm template` on this chart fails by design — pass `--set team=<name>`
(or a values file) to lint or render it.

## Learn more

See [`labs/platform-engineering/platform-and-developer-helm-charts-mcp.md`](../../labs/platform-engineering/platform-and-developer-helm-charts-mcp.md)
for a full walkthrough, including what happens when a team tries to smuggle
a traffic policy onto an endpoint or escape its assigned prefix.
