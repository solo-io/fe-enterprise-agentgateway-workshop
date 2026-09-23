# LLM Cost Management

## Pre-requisites
- Complete the setup in `001` and `002` first. Cost Management runs on the Solo UI and OTEL collector that `002` installs.
- A valid OpenAI API key, exported as `OPENAI_API_KEY`.

## Lab Objectives
- Issue per-user API keys (virtual keys) and route them to OpenAI through an `EnterpriseAgentgatewayBackend`
- Enable the Cost Management section of the Solo UI
- Review the dimensions that map each request to a user and group
- Layer your own model cost catalog over the gateway's base catalog to control per-request USD pricing
- Enforce layered per-user and per-group spend/token budgets with `EnterpriseAgentgatewayBudget`
- Exhaust a token budget and watch the gateway reject the over-budget caller with `429`
- View spend, budgets, and the model cost catalog in the Cost Management dashboard

## About Cost Management

Lab `002` gives you raw token-usage metrics in Grafana and access logs. Cost Management turns them into spend: a dashboard that pivots by provider, model, group, user, and virtual key, and answers "what are we spending on LLMs, and who is spending it" without hand-written PromQL.

This lab configures each piece behind that dashboard:

| Piece | Question it answers |
| --- | --- |
| Virtual keys | Who is calling? |
| Dimensions | Where do they sit in the org? |
| Model cost catalog | What does a token cost? |
| Budgets | What happens when they spend too much? |

The gateway computes spend from its OpenTelemetry spans. The dimensions, catalog, and budgets are ConfigMaps and CRDs, so you can manage them through GitOps.

## Set up the OpenAI backend

Create the OpenAI credential secret. The gateway uses it to authenticate upstream, and callers don't see it.

```bash
kubectl create secret generic openai-secret -n agentgateway-system \
  --from-literal="Authorization=Bearer $OPENAI_API_KEY" \
  --dry-run=client -oyaml | kubectl apply -f -
```

Create the backend and route:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai-all-models
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
EOF
```

Verify both resources are accepted:

```bash
kubectl get enterpriseagentgatewaybackend openai-all-models -n agentgateway-system
kubectl get httproute openai -n agentgateway-system \
  -o jsonpath='{range .status.parents[*].conditions[*]}{.type}={.status}{"\n"}{end}'
```

The backend shows `ACCEPTED   True`, and the route prints `Accepted=True` and `ResolvedRefs=True`.

## Create per-user virtual keys

Callers authenticate to the gateway with a per-user virtual key, and the gateway attaches the real OpenAI credential on the way out. Each request arrives with an identity that spend can be attributed to, and the provider key stays with the platform team.

Create one Secret per user, each labeled `app: llm-virtual-keys`. The auth policy in the next section discovers keys by that label, so onboarding a new user means adding another labeled Secret. Each entry stores the API key plus metadata: `user_id` for token-budget CEL expressions, and `id`/`user`/`group`, which the next section maps to the `virtualKey`, `user`, and `group` dimensions.

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: alice-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  alice: |
    {
      "key": "sk-alice-abc123def456",
      "metadata": {
        "id": "vk-alice-001",
        "user_id": "alice",
        "user": "alice",
        "group": "research"
      }
    }
---
apiVersion: v1
kind: Secret
metadata:
  name: bob-key
  namespace: agentgateway-system
  labels:
    app: llm-virtual-keys
type: Opaque
stringData:
  bob: |
    {
      "key": "sk-bob-xyz789uvw012",
      "metadata": {
        "id": "vk-bob-001",
        "user_id": "bob",
        "user": "bob",
        "group": "engineering"
      }
    }
EOF
```

> **Tip:** For tiered budgets, multi-tenant `(tenant_id, user_id)` scoping, or a deeper walkthrough of virtual-key mechanics, see the [virtual-keys lab](../security/virtual-keys.md). This lab sets up only the minimum Cost Management needs.

## Review the attribution dimensions

A dimension maps request context to a name your organization already uses, such as a group or a user. Each one is a CEL expression that the proxy evaluates on every request, and they live in the `agentgateway-enterprise-budget-dimensions` ConfigMap that the install creates.

```bash
kubectl get configmap agentgateway-enterprise-budget-dimensions -n agentgateway-system \
  -o jsonpath='{.data.dimensions\.yaml}'
```

Output:

```yaml
attributes:
- displayName: Virtual Key
  expression: apiKey.id
  id: virtualKey
hierarchy:
- displayName: Group
  expression: coalesce(jwt.group, apiKey.group)
  id: group
- displayName: User
  expression: coalesce(apiKey.user, apiKey.name, apiKey.owner, jwt.sub, jwt.email,
    basicAuth.username, source.identity.namespace + "/" + source.identity.serviceAccount,
    source.subjectCn)
  id: user
```

- **`hierarchy`** holds ordered scopes, here `group` above `user`. The order sets the roll-up in the dashboard: spend per group, drilled into per user.
- **`attributes`** holds flat, unordered tags such as `virtualKey`. The proxy also provides `model` and `provider`, which are built in and can't be redefined.
- Each `coalesce` tells the gateway where to look on the request, JWT claim first and virtual key metadata second. Values resolve per request, so a new group flows through as soon as it appears in a claim or in key metadata.

The Solo UI renders this same ConfigMap under **Dimensions** as **Scopes & Attributes**. To define your own dimension, such as a `costCenter` resolved from `coalesce(jwt.costCenter, request.headers["x-cost-center"])`, add it to `budgetDimensions.config.attributes` in your Helm values. See the [budget dimensions docs](https://docs.solo.io/agentgateway/latest/llm/cost-controls/budget-limits/#custom-dimensions).

> **Note:** A dimension that resolves to an empty string is unset for that request. Budgets that name it don't match, and the dashboard groups that spend under **Unattributed**. `virtualKey` resolves from `apiKey.id` alone, with no fallback, so set `id` on every key.

## Configure API key authentication

Create an `EnterpriseAgentgatewayPolicy` that requires API key authentication for all gateway traffic. `secretSelector` discovers every Secret in the namespace carrying the `app: llm-virtual-keys` label, and `mode: Strict` rejects any request that does not present a recognized key.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: api-key-auth
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway-proxy
  traffic:
    apiKeyAuthentication:
      mode: Strict
      secretSelector:
        matchLabels:
          app: llm-virtual-keys
EOF
```

Export the gateway IP and confirm alice/bob authenticate while an unknown key is rejected:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -s -o /dev/null -w "alice: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-alice-abc123def456" \
  -d '{"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "Hello!"}]}'

curl -s -o /dev/null -w "bob: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-bob-xyz789uvw012" \
  -d '{"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "Hello!"}]}'

curl -s -o /dev/null -w "invalid: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-invalid-key" \
  -d '{"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Expected output: alice and bob both `HTTP 200`, the invalid key `HTTP 401`.

The Cost Management dashboard in the Solo UI is enabled by the `products.agentgateway.features.cost-management` flag, which `002` already sets. Set `cost-management-writes: false` there if you want the dashboard read-only, with no budget or dimension edits from the UI.

## Configure a model cost catalog

The gateway ships a base catalog: the controller creates an `agentgateway-proxy-model-catalog` ConfigMap alongside each Gateway, covering OpenAI, Anthropic, and Gemini models known at release time, so those models are priced with no configuration from you. A model the base catalog doesn't know, whether a mock, a self-hosted model, or one released after the gateway, still contributes token and request volume but `$0.00` of spend.

You layer your own catalog on top of that base as an overlay. The one below does both overlay jobs:

- **Add a model the base catalog doesn't price.** This release's base catalog stops short of the `gpt-5.6` family, so `gpt-5.6-luna` traffic prices at `$0.00` until you supply rates for it.
- **Override a model it does price.** The base prices `gpt-5.5` at public list, `$5.00` input and `$30.00` output per 1M tokens; the overlay restates it at a contracted 50% of list.

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: llm-model-costs
  namespace: agentgateway-system
data:
  catalog.json: |
    {
      "providers": {
        "openai": {
          "models": {
            "gpt-5.6-luna": {
              "rates": { "input": "0.20", "output": "1.20", "cacheRead": "0.02", "cacheWrite": "0.25" }
            },
            "gpt-5.6-terra": {
              "rates": { "input": "2.00", "output": "12.00", "cacheRead": "0.20", "cacheWrite": "2.50" }
            },
            "gpt-5.6-sol": {
              "rates": { "input": "5.00", "output": "30.00", "cacheRead": "0.50", "cacheWrite": "6.25" }
            },
            "gpt-5.5": {
              "rates": { "input": "2.50", "output": "15.00", "cacheRead": "0.25" },
              "tiers": [
                {
                  "contextOver": 272000,
                  "rates": { "input": "5.00", "output": "22.50", "cacheRead": "0.50" }
                }
              ]
            }
          }
        }
      }
    }
EOF
```

> **Note:** An overlay entry **replaces** the base entry for that model rather than merging into it, so an override has to restate every field it wants to keep. The base `gpt-5.5` entry carries a `cacheRead` rate and a `tiers` block that reprices requests over a 272,000-token context; an override setting only `input` and `output` would silently drop both. The entry above restates them at the same 50% discount.

`001` created the `agentgateway-config` `EnterpriseAgentgatewayParameters` and attached it to the Gateway via `spec.infrastructure.parametersRef`. Point the Gateway at your catalog by adding `modelCatalog` there with a **merge patch** rather than `kubectl apply`: a full `apply` without the fields `001` set (like `logging`) would strip them.

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type merge -p '{"spec":{"modelCatalog":{"sources":[{"configMap":{"name":"llm-model-costs","key":"catalog.json"}}]}}}'
```

Confirm the existing fields (e.g. `logging`) are still present alongside the new `modelCatalog` block:

```bash
kubectl get enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system -o yaml
```

Allow up to two minutes before checking rates. The catalog reaches the gateway as a mounted ConfigMap, so edits to `llm-model-costs` take effect on that delay; requests sent in the meantime price at the previous rates.

## Enforce layered budgets

The dashboard reports spend after the fact. To cap it, declare spend and token limits with the `EnterpriseAgentgatewayBudget` CRD, which the dashboard's **Budgets** tab reads. The controller compiles each entry into a `RateLimitConfig` for you, named `agw-budget-<budget-name>-<hash>`.

Each entry's `subject` scopes it to the same dimensions Cost Management attributes spend by: `model`, `provider`, `virtualKey`, `user`, and `group` by default.

One resource can hold up to 64 entries, but budgets in practice are written by different people for different reasons, and each resource is a separate file to review and a separate row in the dashboard. Splitting them along ownership lines keeps each one small enough to reason about:

| Resource | Owner | Expresses |
| --- | --- | --- |
| `finance-guardrails` | Finance | Monthly USD ceiling per team, reported but never enforced |
| `platform-defaults` | Platform team | The daily token allowance every caller gets |
| `user-exceptions` | Platform team | Deliberate per-user departures from that default |
| `research-team-quota` | The research team | A cap that team sets on itself, in its own namespace |

Create the three platform-owned budgets:

```bash
kubectl apply -f - <<EOF
#--- Finance: report overages, never interrupt the team ---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBudget
metadata:
  name: finance-guardrails
  namespace: agentgateway-system
spec:
  budgets:
  - name: engineering-monthly-usd
    subject:
      group: engineering
    limit:
      unit: USD
      amount: 50
    window:
      unit: Month
    onBudgetExceeded: Audit
  - name: research-monthly-usd
    subject:
      group: research
    limit:
      unit: USD
      amount: 25
    window:
      unit: Month
    onBudgetExceeded: Audit
---
#--- Platform: the default every caller gets ---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBudget
metadata:
  name: platform-defaults
  namespace: agentgateway-system
spec:
  budgets:
  - name: any-user-daily-tokens
    subject:
      user: "*"
    limit:
      unit: Tokens
      amount: 100000
    window:
      unit: Day
    onBudgetExceeded: Block
---
#--- Platform: deliberate exceptions to that default ---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBudget
metadata:
  name: user-exceptions
  namespace: agentgateway-system
spec:
  budgets:
  #--- Raise the default for a power user ---
  - name: alice-daily-tokens
    subject:
      user: alice
    limit:
      unit: Tokens
      amount: 500000
    window:
      unit: Day
    onBudgetExceeded: Block
  #--- Tighten it for a service account on probation (small enough to trip below) ---
  - name: bob-daily-tokens
    subject:
      user: bob
    limit:
      unit: Tokens
      amount: 2000
    window:
      unit: Day
    onBudgetExceeded: Block
EOF
```

- `onBudgetExceeded` decides what happens at the limit. `Block` rejects further requests with `429`, and `Audit` records the overage and forwards the request. Auditing the team budgets while blocking individuals caps one runaway caller and leaves the rest of the team serving traffic.
- A `"*"` subject value creates a separate allowance per value. `user: "*"` gives each distinct user their own 100,000 tokens per day, so a new user gets the default the first time they call.
- An exact entry takes precedence over the wildcard on the same dimension. `alice-daily-tokens` raises alice to 500,000 tokens and `bob-daily-tokens` drops bob to 2,000; both suppress the default for that user alone. Entries on other dimensions still stack, so bob's requests debit both his own cap and the engineering group budget.
- Entries compose across resources, and the most restrictive one wins. Nothing about the split changes enforcement: a tight cap in one resource still overrides a generous one in another for the same subject. Separating them is an organizational choice, not a functional one.
- Token budgets are the practical way to demonstrate enforcement. `amount` is a whole number, so the smallest USD budget is `$1`: thousands of requests at these rates. A token cap trips in a handful.

Windows roll rather than align to the calendar: `Day` covers a rolling 24 hours and `Month` a rolling 30 days.

Add `entBudgetEnforcement` to the existing `api-key-auth` policy with a merge patch, so the gateway discovers and enforces `EnterpriseAgentgatewayBudget` resources in the same namespace:

```bash
kubectl patch enterpriseagentgatewaypolicy api-key-auth -n agentgateway-system \
  --type merge -p '{"spec":{"traffic":{"entBudgetEnforcement":{"discovery":{"namespaces":{"from":"Same"}}}}}}'
```

Confirm the controller compiled a `RateLimitConfig` from each budget:

```bash
kubectl get enterpriseagentgatewaybudget -n agentgateway-system
kubectl get ratelimitconfig -n agentgateway-system
```

Expected output: all three budgets `ACCEPTED True` and `ENFORCED True`, alongside one `agw-budget-<budget-name>-<hash>` per resource.

```
NAME                 ACCEPTED   ENFORCED   AGE
finance-guardrails   True       True       21s
platform-defaults    True       True       21s
user-exceptions      True       True       21s
```

## Delegate a budget to a team namespace

The budgets above are platform-owned and live beside the gateway. A team that runs its own namespace can own its cap too, keeping the limit in the same repo as the workloads it governs.

Create a namespace for the research team with a budget it sets on itself:

```bash
kubectl create namespace team-research

kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBudget
metadata:
  name: research-team-quota
  namespace: team-research
spec:
  budgets:
  - name: research-daily-tokens
    subject:
      group: research
    limit:
      unit: Tokens
      amount: 50000
    window:
      unit: Day
    onBudgetExceeded: Block
EOF
```

Check its status:

```bash
kubectl get enterpriseagentgatewaybudget -A
```

The new budget reports `ACCEPTED True` but `ENFORCED False`, and no `RateLimitConfig` is generated for it:

```
NAMESPACE             NAME                  ACCEPTED   ENFORCED   AGE
agentgateway-system   finance-guardrails    True       True       3m
agentgateway-system   platform-defaults     True       True       3m
agentgateway-system   user-exceptions       True       True       3m
team-research         research-team-quota   True       False      15s
```

The resource is valid; the gateway isn't looking in that namespace. `discovery.namespaces.from: Same` restricts enforcement to the policy's own namespace. Widen it to `All`:

```bash
kubectl patch enterpriseagentgatewaypolicy api-key-auth -n agentgateway-system \
  --type merge -p '{"spec":{"traffic":{"entBudgetEnforcement":{"discovery":{"namespaces":{"from":"All"}}}}}}'
```

Re-check, and `research-team-quota` flips to `ENFORCED True` with a `RateLimitConfig` created next to it in `team-research`:

```bash
kubectl get enterpriseagentgatewaybudget -A
kubectl get ratelimitconfig -n team-research
```

Delegation only ever tightens. Because the most restrictive entry wins, a team namespace can lower its own ceiling but cannot raise the platform default, so widening discovery does not hand teams a way to grant themselves more budget.

See the [Budget Limits docs](https://docs.solo.io/agentgateway/latest/llm/cost-controls/budget-limits/) for the full CRD reference: custom subject dimensions, `Selector`-based `discovery` scoping, and the up-to-64-entries-per-resource limit.

## Generate cost-attributed traffic

Send a few requests as each user:

```bash
for i in {1..5}; do
  curl -s -o /dev/null -w "alice request $i: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-alice-abc123def456" \
    -d '{"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}'
done

for i in {1..3}; do
  curl -s -o /dev/null -w "bob request $i: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-bob-xyz789uvw012" \
    -d '{"model": "gpt-5.6-terra", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}'
done
```

Expected output: all 8 requests `HTTP 200`. Alice's requests run on `gpt-5.6-luna` and bob's on the pricier `gpt-5.6-terra`, so the dashboard's model pivot has two models to separate. This traffic stays well inside alice's allowance and both team budgets.

## Watch a budget block

Bob's 2,000-token daily cap is small enough to exhaust deliberately. Send requests as bob until the gateway rejects one:

```bash
for i in $(seq 1 25); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-bob-xyz789uvw012" \
    -d '{"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}')
  echo "bob request $i: HTTP $code"
  [ "$code" = "429" ] && break
done
```

Expected output: a run of `HTTP 200` followed by a single `HTTP 429`, typically on the 18th or 19th request at roughly 105 tokens apiece. Once bob is over his cap, every further request from that key is rejected until the rolling 24-hour window moves.

Alice is unaffected: her own entry gives her a separate allowance, and the team budgets are set to `Audit`, so engineering keeps serving traffic while the one over-budget caller is cut off:

```bash
curl -s -o /dev/null -w "alice: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-alice-abc123def456" \
  -d '{"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Expected output: `alice: HTTP 200`.

## (Optional) Seed historical spend

The requests above prove attribution works, but they all land within a few minutes of each other, so the **Spend Over Time** panels draw a single spike and the 7d and 30d ranges sit nearly empty. To present the dashboard against a realistic history, seed ClickHouse with synthetic spend.

The script writes spans straight into the table behind Cost Management, so every pivot has something to separate. It seeds a month of traffic across four providers: the `gpt-5.6` family priced at the overlay rates you just configured, plus Anthropic, Gemini, and AWS Bedrock models at their catalog rates. The traffic is spread over five teams whose sizes deliberately differ:

| Group | Members |
| --- | --- |
| `engineering` | bob, dave, erin |
| `support` | ivan, judy, ken |
| `research` | alice, carol |
| `ml-platform` | frank, grace |
| `product` | heidi |

Alice and bob keep the virtual keys you issued them, so the synthetic history lines up with the live requests you sent. Run it from the workshop root:

```bash
./lib/observability/seed-cost-data.sh
```

Expected output:

```
Seeding 300000 synthetic spans across 30d into platformdb.agw_spans_typed ...
Done. Totals now in ClickHouse:
requests	292.52 thousand
tokens	8.71 billion
spend_usd	$31473.76
```

Volume and window are tunable, so you can seed a smaller sample or a longer history:

```bash
ROWS=50000 DAYS=7 ./lib/observability/seed-cost-data.sh
```

Re-running adds more spend on top of what is already there. To start over, run it with `TRUNCATE=true`, which clears the spans table and the cost rollups first, including the real requests and traces from earlier in this lab.

> **Note:** Seeding populates the **Dashboard** tab and its pivots. The **Budgets** tab reads live counters from the rate limiter rather than ClickHouse, so budget usage continues to reflect only the real requests you sent through the gateway.

## View the Cost Management dashboard

Port-forward the Solo UI:

```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
```

Open [http://localhost:4000/age/](http://localhost:4000/age/) and select **Cost Management** from the menu.

- **Dashboard**: a summary row of total spend, tokens, and request count, followed by paired **Spend by** and **Spend Over Time by** panels. Switch a panel's pivot from **Provider** to **Group** to **User**, and the same traffic re-slices as `research` against `engineering`, then as alice against bob. The **Filters** row narrows every panel at once, and **Export CSV** downloads the current view. If you ran the seeding step, select the **30d** range and the pivots fill out to four providers, five teams, and eleven users.
  - Every request attributes to a user, group, and virtual key, because each key sets `id`, `user`, and `group`. Requests where a dimension resolves to nothing appear under **Unattributed**.
  - Model breakdowns list the model ID the provider returns on the response. `gpt-5.6-luna` and `gpt-5.6-terra` appear verbatim; a model that answers with a dated snapshot ID, such as `gpt-5.4-nano-2026-03-17`, shows that snapshot, and the gateway resolves it back to the alias in your catalog for pricing.
- **Model Cost Catalog**: the header names the base catalog, lists your overlay under **Overlay catalogs are applied in this order**, and reports `1 model entry is overridden by overlay sources`. Search the table to see both halves of the overlay:
  - `gpt-5.6` returns the three added models with the rates you supplied.
  - `gpt-5.5` shows `$2.50` input, `$15.00` output, and `$0.25` cache read instead of the list `$5.00`/`$30.00`/`$0.50`, tagged source `Override`.
- **Budgets**: one row per `EnterpriseAgentgatewayBudget`, with a header counting `Within budget` against `Exceeding budget`: `4 Budgets`, `3 Within budget`, `1 Exceeding budget` after the steps above. Each row shows its **Scope**, so `research-team-quota` is visibly owned by `team-research` while the rest sit in `agentgateway-system`. Click a row to open its detail drawer, where each entry shows its subject, window, and usage against its limit:
  - `user-exceptions` is the one over budget: `bob-daily-tokens`, subject `user bob`, `2,000 tokens of 2,000 tokens`, flagged `Over budget` at `100%` after the blocking step. Its sibling `alice-daily-tokens` reads e.g. `694 tokens of 500,000 tokens`, `On track`
  - `platform-defaults`: `any-user-daily-tokens`, subject `user *`, e.g. `368 tokens of 100,000 tokens`, `On track`. Bob's traffic is absent here, because his exact entry takes precedence and debits his own cap instead of the shared default
  - `finance-guardrails`: `engineering-monthly-usd` and `research-monthly-usd`, e.g. `$0.01 of $50.00` and `$0.00 of $25.00`, both `On track`
  - `research-team-quota`: `research-daily-tokens`, subject `group research`, `On track`

  > **Note:** Budget usage comes from the gateway's live rate-limiter counters rather than ClickHouse, so the optional seeding step above does not move these bars; only real requests through the gateway do.

Treat spend as an estimate. The gateway multiplies token counts by the per-token prices in your catalog, so the totals won't reconcile line-for-line against your LLM provider's invoice.

## Cleanup

Remove the lab's resources:

```bash
# Budgets, and the delegated team namespace
kubectl delete enterpriseagentgatewaybudget -n agentgateway-system \
  finance-guardrails platform-defaults user-exceptions --ignore-not-found
kubectl delete namespace team-research --ignore-not-found

# Model cost catalog wiring (leaves other parameters fields, e.g. logging, untouched)
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type merge -p '{"spec":{"modelCatalog":null}}'
kubectl delete configmap -n agentgateway-system llm-model-costs --ignore-not-found

# API key authentication + budget enforcement policy, virtual keys
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system api-key-auth --ignore-not-found
kubectl delete secret -n agentgateway-system -l app=llm-virtual-keys --ignore-not-found

# OpenAI backend
kubectl delete httproute -n agentgateway-system openai --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret --ignore-not-found
```

Clear the synthetic spend only if you ran the optional seeding step. This also
drops the real traffic and traces recorded during this lab, along with anything
other labs have written to the same table:

```bash
TRUNCATE=true ROWS=0 ./lib/observability/seed-cost-data.sh
```

To disable the Cost Management feature:

```bash
helm upgrade -i management oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management \
--namespace agentgateway-system \
--version "$AGW_UI_VERSION" \
--reuse-values \
--set products.agentgateway.features.cost-management=false
```
