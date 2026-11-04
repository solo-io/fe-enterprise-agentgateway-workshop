# agentgateway-llm-ops

Two install modes share one chart. **Infra mode** (installed once by the LLM
operations team) owns the `Gateway`, the catalog of AI-model aliases (OpenAI and
Anthropic), the token budget tiers, and any team grants kept in the shared
values file. **Grant-only mode** lets the platform install one team's API key
as its own release, in the same namespace as the infra release, without
touching the Gateway, the catalog or the tiers.

## What it renders

Infra mode (`gateway` and `modelCatalog` set) renders: the `Gateway` and its
`EnterpriseAgentgatewayParameters` (replicas, service type, graceful drain),
an access-log `EnterpriseAgentgatewayPolicy` on the `Gateway`, and per catalog
alias an `AgentgatewayModel` (provider, pinned model, credentials), an
`HTTPRoute` at `/llm/<alias>`, and an API-key-auth
`EnterpriseAgentgatewayPolicy` whose `secretSelector` discovers Secrets
labeled `llm-ops.agentgateway.solo.io/alias-<alias>: granted`.

When `budgetTiers` is set, infra mode also renders one `RateLimitConfig
llm-budget-tiers` that defines every tier, and one `EnterpriseAgentgatewayPolicy
llm-<alias>-budget` per catalog alias that attaches it to that alias's route.

Both modes render, per grant in `grants`, a `Secret llm-key-<team>` carrying
that team's API key (entry id = team name, inline JSON, labeled by all
granted aliases so the auth policies find it). The key's metadata records the
team as `user_id` and its tier as `budget_tier`.

### Design note: why tiers live in the infra release

A route enforces only one `entRateLimit` policy. When two policies target the
same `HTTPRoute`, both report `Accepted` and `Attached`, but the proxy applies
only one of them, with no error or log line. An earlier version of this chart
gave each release its own budget policy, so a grant-only release and the infra
release granting the same alias left one team's budget silently unenforced.

The chart therefore keeps exactly one budget policy per alias route, owned by
the infra release, and moves the per-team decision into the request: the
`RateLimitConfig` reads `budget_tier` and `user_id` from the authenticated
key, so each team gets its own counter at its tier's limit. A grant-only
release carries only a key, so it can change a team's tier without attaching
anything to a route.

A key whose `budget_tier` is missing or names no defined tier gets the first
tier's limit, so a typo cannot remove a team's budget. List the smallest tier
first.

## Grant-only mode

Set only `grants`: `gateway` explicitly `null`, `modelCatalog` unset:

```yaml
gateway: null

grants:
  - team: team-gamma
    key: gamma-secret-key
    aliases:
      - chat-real
    budgetTier: large
```

`gateway: null` is required: a values file with only `grants:`
makes helm merge the chart's default `gateway` block back in, rendering a
second Gateway/parameters/access-log set that fails with `AlreadyExists`
next to an existing infra release.

This release can't see `modelCatalog` or `budgetTiers`, so an unmatched alias
or tier is not a render error. An unmatched alias label is inert until an
infra release with that alias lands in the same namespace, and an unmatched
tier gets the first tier's limit. Install as a second, team-scoped release
there:

```bash
helm install llm-team-gamma charts/agentgateway-llm-ops \
  -n agentgateway-system --values grant-gamma-values.yaml
```

A team already granted elsewhere in the same namespace surfaces as a Helm
ownership conflict at install, because its `Secret llm-key-<team>` name
collides with the release that already grants it. Offboard with
`helm uninstall <release-name>`.

## The values contract

Minimal `llm-ops-values.yaml` (infra mode; for grant-only, drop `modelCatalog`
and set `gateway: null` instead, as above):

```yaml
gateway:
  name: agw-llm-ops
modelCatalog:
  - alias: chat-real
    provider: openai
    model: gpt-5.6-terra
    auth:
      secretRef: openai-secret
budgetTiers:
  - name: standard
    tokensPerMinute: 10000
grants:
  - team: team-alpha
    key: alpha-secret-key
    aliases:
      - chat-real
    budgetTier: standard
```

| Field                                | In                | Purpose                          |
|--------------------------------------|-------------------|-----------------------------------|
| `alias` / `provider` / `model`       | `modelCatalog[*]` | Route segment `/llm/<alias>`; vendor (openai, anthropic); model ID the alias always sends |
| `auth.secretRef`                     | `modelCatalog[*]` | Existing Secret with vendor credentials |
| `baseURL`                            | `modelCatalog[*]` | Optional self-hosted/mock endpoint, as the base path (`http://mock-svc:8000/v1`) |
| `team` / `key`                      | `grants[*]`       | Team identifier (API key entry id); API key value (min 8 chars, workshop-grade) |
| `name` / `tokensPerMinute`           | `budgetTiers[*]`  | Tier name; tokens per minute each team on the tier may use. The first tier is the fallback |
| `aliases` / `budgetTier`             | `grants[*]`       | Aliases this team can access; the tier that sets its budget |

Keys are inline in `grants[*].key`: fine for workshops, but **production
deployments should source API keys from an external secret manager** (e.g.
External Secrets Operator) instead of passing them as chart values.

```bash
helm install agw-llm-ops charts/agentgateway-llm-ops \
  -n agentgateway-system --values llm-ops-values.yaml
```

## Learn more

Full walkthrough: [`labs/platform-engineering/centralized-llm-ops-helm-chart.md`](../../labs/platform-engineering/centralized-llm-ops-helm-chart.md).
