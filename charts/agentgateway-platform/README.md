# agentgateway-platform

Installed once by the platform team. It owns the `Gateway`, the proxy fleet
shape, the observability pipeline, the security baseline (JWT, WAF), the
**URL space**, and the **cost tiers**. It also onboards application teams —
each entry in `teams` creates that team's path prefix, delegation label, and
tier policy.

## What it renders

- `Gateway` — the shared entry point, plus its `EnterpriseAgentgatewayParameters`
  (replicas, resources, service type, pod disruption budget, graceful drain)
- `EnterpriseAgentgatewayPolicy` for access logging and tracing, attached to
  the `Gateway`
- `EnterpriseAgentgatewayPolicy` and `EnterpriseAgentgatewayBackend` for JWT
  authentication, when `security.jwt.enabled` is set
- `WAFPolicy` and its attaching `EnterpriseAgentgatewayPolicy`, when
  `security.waf.enabled` is set
- `PodMonitor`, when `observability.metrics.enabled` is set
- Per team in `teams`: a parent `HTTPRoute` at that team's path prefix,
  delegating to child routes in that team's namespace, plus an
  `EnterpriseAgentgatewayPolicy` carrying that team's tier (retry, timeout)
- Per team whose tier sets `rateLimit.toolCallsPerMinute`: a
  `RateLimitConfig` and an attaching `EnterpriseAgentgatewayPolicy`
  (`entRateLimit`) that budget MCP `tools/call` requests on the team's
  parent route — a global counter, so it holds across proxy replicas.
  Other MCP operations (`initialize`, `tools/list`) are not counted

## The contract with agentgateway-developer

Onboarding a team here is what lets that team install
[`agentgateway-developer`](../agentgateway-developer/README.md) and have its
endpoints actually receive traffic. The two charts meet at this contract:

| Contract element   | Set by                         | Value for `team-alpha` |
|---------------------|---------------------------------|-------------------------|
| Delegation label    | platform (rendered here)        | `team: team-alpha`      |
| Path prefix         | platform (at onboarding)        | `/teams/team-alpha`     |
| Namespace           | platform (at onboarding)        | `team-alpha`            |
| Cost tier           | platform (at onboarding)        | e.g. `gold`             |

The parent route delegates only to child routes that carry the `team=<name>`
label **and** live in the assigned namespace — both halves are required.

## Install

```bash
helm install agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml
```

Minimal `platform-values.yaml`:

```yaml
gateway:
  name: agw-platform
tiers:
  gold:
    rateLimit:
      toolCallsPerMinute: 300
    retry:
      attempts: 3
      backoff: 500ms
      codes:
        - 429
        - 502
        - 503
        - 504
    timeouts:
      request: 120s
  silver:
    rateLimit:
      toolCallsPerMinute: 60
    timeouts:
      request: 60s
teams:
  - name: team-alpha
    namespace: team-alpha
    tier: gold
```

## Secrets by reference only

The chart never takes secret material inline. JWT JWKS material is either a
remote endpoint (`security.jwt.jwks.host`/`port`/`path`) or a `secretRef`-style
reference resolved by the gateway; TLS for the HTTPS listener is a
pre-provisioned `kubernetes.io/tls` Secret named by
`gateway.listeners.https.tls.secretRef`. The chart only ever points at
secrets that already exist in the cluster — it does not create or accept
raw key material as a value.

## Learn more

See [`labs/platform-engineering/platform-and-developer-helm-charts-mcp.md`](../../labs/platform-engineering/platform-and-developer-helm-charts-mcp.md)
for a full walkthrough: onboarding a team, self-serving an MCP endpoint,
budgeting tool calls by tier, proving teams cannot escape their tier or
prefix (including a cross-namespace hijack attempt), re-tiering with a
one-line values change, and turning on JWT for every team at once.
