# agentgateway-llm-ops

Two install modes share one chart. **Infra mode** (installed once by the LLM
operations team) owns the `Gateway`, the catalog of AI-model aliases (OpenAI,
Anthropic, Bedrock, etc.), and — per grant — a team's API key and token
budget. **Grant-only mode** lets a team install just its own key and budget
into the same namespace as the infra release, without touching the Gateway
or catalog.

## What it renders

Infra mode (`gateway` and `modelCatalog` set) additionally renders: the
`Gateway` + its `EnterpriseAgentgatewayParameters` (replicas, service type,
graceful drain), an access-log `EnterpriseAgentgatewayPolicy` on the
`Gateway`, and per catalog alias an `EnterpriseAgentgatewayBackend` (model
config, credentials), an `HTTPRoute` at `/llm/<alias>`, and an API-key-auth
`EnterpriseAgentgatewayPolicy` whose `secretSelector` discovers Secrets
labeled `llm-ops.agentgateway.solo.io/alias-<alias>: granted`.

Both modes render, per grant in `grants`: a `Secret llm-key-<team>` carrying
that team's API key (entry id = team name; inline JSON; labeled by all
granted aliases so auth policies find it), and a `RateLimitConfig
llm-budget-<team>` defining that team's tokens-per-minute budget. The
`entRateLimit` policy that attaches those budgets to routes is **not**
per-team: the chart renders one `EnterpriseAgentgatewayPolicy` per **alias**
the release's grants touch, named
`llm-budget-<alias>-<sha256(releaseName)[:8]>`, with a single `targetRef`
(that alias's `HTTPRoute llm-<alias>`) and a `rateLimitConfigRefs` list
naming every one of this release's teams granted that alias. Budgets stay
independent per team — each ref resolves against its own team's
`RateLimitConfig` descriptor — only the attachment point is shared. The
8-hex release-name hash keeps policy names unique across releases granting
the same alias (plain concatenation is ambiguous: release `grant-team-beta`
+ alias `chat-mock` would collide with release `grant-team-beta-chat` +
alias `mock`); the owning release remains queryable via the standard
`app.kubernetes.io/instance` label. Policies target routes, never the
`Gateway`, so grant-only releases need no reference to the Gateway at all.

### Design note: why one policy per alias, not per team

The chart originally rendered one `entRateLimit` policy per team, and a team
granted several aliases got a single policy object with a multi-route
`targetRefs` list. Live testing on a 2-replica proxy fleet (Enterprise
Agentgateway v2026.6.3) showed that when two teams shared an alias, the two
independently-attaching policies on that shared route were resolved
inconsistently **per replica**: one replica permanently enforced team A's
policy for every request on the route, the other permanently enforced team
B's, regardless of which team's key authenticated the request. One team's
budget therefore silently never applied to whichever share of its traffic
landed on the "wrong" replica — no error, warning, or log line pointed at
it. Regrouping so that every rendered policy targets exactly one route (per
alias, refs listing the granted teams) eliminated the inconsistency in every
retest, including the cross-release case where a grant-only release and the
infra release each attach their own single-target policy to the same shared
alias route.

## Grant-only mode

Set only `grants` — `gateway` explicitly `null`, `modelCatalog` unset:

```yaml
gateway: null

grants:
  - team: team-gamma
    key: gamma-secret-key
    aliases:
      - chat-real
    tokensPerMinute: 2000
```

`gateway: null` is required, not optional: a values file with only `grants:`
makes helm merge the chart's default `gateway` block back in, rendering a
second Gateway/parameters/access-log set that fails with `AlreadyExists`
next to an existing infra release.

This release can't see `modelCatalog`, so an unmatched alias is not an error
— its label is inert until an infra release with that alias lands in the
same namespace. Install as a second, team-scoped release there:

```bash
helm install llm-team-gamma charts/agentgateway-llm-ops \
  -n agentgateway-system --values grant-gamma-values.yaml
```

A team already granted elsewhere in the same namespace surfaces as a Helm
ownership conflict at install — its per-team `Secret llm-key-<team>` and
`RateLimitConfig llm-budget-<team>` names collide with the release that
already grants it (the per-alias budget policies never collide across
releases; their names carry a release-name hash). Offboard with
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
    model: gpt-4o
    auth:
      secretRef: openai-secret
grants:
  - team: team-alpha
    key: alpha-secret-key
    aliases:
      - chat-real
    tokensPerMinute: 10000
```

| Field                                | In                | Purpose                          |
|--------------------------------------|-------------------|-----------------------------------|
| `alias` / `provider` / `model`       | `modelCatalog[*]` | Route segment `/llm/<alias>`; vendor (openai, anthropic, bedrock, vertexai, azureopenai); model ID |
| `auth.secretRef`                     | `modelCatalog[*]` | Existing Secret with vendor credentials |
| `host` / `port` / `apiPath`          | `modelCatalog[*]` | Optional self-hosted/mock endpoint override |
| `team` / `key`                      | `grants[*]`       | Team identifier (API key entry id); API key value (min 8 chars, workshop-grade) |
| `aliases` / `tokensPerMinute`        | `grants[*]`       | Aliases this team can access and is budgeted for; per-team tokens-per-minute limit |

Keys are inline in `grants[*].key` — fine for workshops, but **production
deployments should source API keys from an external secret manager** (e.g.
External Secrets Operator) instead of passing them as chart values.

```bash
helm install agw-llm-ops charts/agentgateway-llm-ops \
  -n agentgateway-system --values llm-ops-values.yaml
```

## Learn more

Full walkthrough: [`labs/platform-engineering/centralized-llm-ops-helm-chart.md`](../../labs/platform-engineering/centralized-llm-ops-helm-chart.md).
