# LLM Cost Management

## Pre-requisites
Complete the setup in `001` and `002` first. Cost Management runs on the Solo UI and OTEL collector that `002` installs, so `002` is required here.
- A valid OpenAI API key, exported as `OPENAI_API_KEY`.

## Lab Objectives
- Issue per-user API keys (virtual keys) and route them to OpenAI through an `EnterpriseAgentgatewayBackend`
- Enable the Cost Management section of the Solo UI
- Review the dimensions that map each request to a user and group
- Layer your own model cost catalog over the gateway's base catalog to control per-request USD pricing
- Enforce layered per-user and per-group spend/token budgets with `EnterpriseAgentgatewayBudget`
- View spend, budgets, and the model cost catalog in the Cost Management dashboard

## About Cost Management

Lab `002` gives you raw token-usage metrics in Grafana and access logs. Cost Management turns them into spend. Total spend, input versus output tokens, and request counts sit at the top of the dashboard; below them, every panel pivots between provider, model, group, user, and virtual key, and any view exports to CSV. Answering "what are we spending on LLMs, and who is spending it" no longer means hand-writing PromQL.

This lab configures each piece behind that dashboard:

| Piece | Question it answers |
| --- | --- |
| Virtual keys | Who is calling? |
| Dimensions | Where do they sit in the org? |
| Model cost catalog | What does a token cost? |
| Budgets | What happens when they spend too much? |

The gateway computes spend from its OpenTelemetry spans, and the dimensions, catalog, and budgets are ConfigMaps and CRDs. You manage them through GitOps and export the underlying spans to your own observability stack.

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

Create one Secret per user, each labeled `app: llm-virtual-keys`. The auth policy in the next section discovers keys by that label instead of by a single Secret name, so you onboard a new user by adding another labeled Secret, with no edit to a central Secret or the policy.

Callers authenticate to the gateway with a virtual key that you revoke or re-scope per user, and the gateway attaches the upstream OpenAI credential on the way out, so the real provider key stays with the platform team. Each request then arrives with an identity that spend can be attributed to.

Each entry stores the API key plus the metadata both Cost Management and rate limiting read: `user_id` for token-budget CEL expressions (the client doesn't supply it), and `id`/`user`/`group`, which the next section maps to the `virtualKey`, `user`, and `group` dimensions.

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
- Each `coalesce` tells the gateway where to look on the request, JWT claim first and virtual key metadata second. The gateway resolves these values per request and keeps no table of groups or cost centers, so a new group flows through as soon as it appears in a claim or in key metadata. You edit a dimension when the value moves to a different claim or header.

Once you enable Cost Management below, the Solo UI renders this same ConfigMap under **Dimensions** as **Scopes & Attributes**, where you reorder scopes and add attributes. To define your own dimension, such as a `costCenter` resolved from `coalesce(jwt.costCenter, request.headers["x-cost-center"])`, add it to `budgetDimensions.config.attributes` in your Helm values. See the [budget dimensions docs](https://docs.solo.io/agentgateway/latest/llm/cost-controls/budget-limits/#custom-dimensions).

> **Note:** A dimension that resolves to an empty string is unset for that request. Budgets that name it don't match, and the dashboard groups that spend under **Unattributed**. `virtualKey` resolves from `apiKey.id` alone, with no fallback, so set `id` on every key.

## Configure API key authentication

Create an `EnterpriseAgentgatewayPolicy` that requires API key authentication for all gateway traffic. `secretSelector` discovers every Secret in the namespace carrying the `app: llm-virtual-keys` label and unions their entries into the valid-key set. `mode: Strict` rejects any request that does not present a recognized key.

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

## Enable Cost Management in the Solo UI

Layer the `cost-management` feature flag onto the existing `management` release from `002` with `--reuse-values`, rather than re-specifying every value from that install:

```bash
export AGW_UI_VERSION=0.5.5

helm upgrade -i management oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management \
--namespace agentgateway-system \
--version "$AGW_UI_VERSION" \
--reuse-values \
--set products.agentgateway.features.cost-management=true
```

To also make the dashboard read-only (no budget/dimension edits from the UI), add `--set products.agentgateway.features.cost-management-writes=false` to the command above.

Check that the UI rolled out:

```bash
kubectl rollout status deploy/solo-enterprise-ui -n agentgateway-system
```

## Configure a model cost catalog

The gateway ships a base catalog: the controller creates an `agentgateway-proxy-model-catalog` ConfigMap alongside each Gateway, covering OpenAI, Anthropic, and Gemini models known at release time. The gateway prices those models with no configuration from you. A model the base catalog doesn't know, whether a mock, a self-hosted model, or one released after the gateway, still contributes token and request volume but `$0.00` of spend.

You layer your own catalog on top of that base as an overlay. An overlay does two jobs, and the one below does both:

- **Add a model the base catalog doesn't price.** This release's base catalog stops short of the `gpt-5.6` family, so `gpt-5.6-luna` traffic prices at `$0.00` until you supply rates for it.
- **Override a model it does price.** Public list prices are the wrong number for an organization on a negotiated contract. The base prices `gpt-5.5` at list, `$5.00` input and `$30.00` output per 1M tokens; the overlay restates it at a contracted 50% of list.

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

> **Note:** An overlay entry **replaces** the base entry for that model outright rather than merging into it, so an override has to restate every field it wants to keep. The base `gpt-5.5` entry carries a `cacheRead` rate and a `tiers` block that reprices requests over a 272,000-token context. An override setting only `input` and `output` would silently drop both, leaving cached reads and long-context requests billed at the plain rates. The entry above restates them at the same 50% discount. The three `gpt-5.6` entries have no base entry to replace, so they only need their own rates.

`001` created the `agentgateway-config` `EnterpriseAgentgatewayParameters` and attached it to the Gateway via `spec.infrastructure.parametersRef`. Point the Gateway at your catalog by adding `modelCatalog` there with a **merge patch** rather than `kubectl apply`: a full `apply` without the fields `001` set (like `logging`) would strip them, because `kubectl apply` computes a three-way diff against the last-applied config.

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type merge -p '{"spec":{"modelCatalog":{"sources":[{"configMap":{"name":"llm-model-costs","key":"catalog.json"}}]}}}'
```

Confirm the existing fields (e.g. `logging`) are still present alongside the new `modelCatalog` block:

```bash
kubectl get enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system -o yaml
```

Allow up to two minutes before checking rates. The catalog reaches the gateway as a mounted ConfigMap, so edits to `llm-model-costs` take effect on that delay, and the gateway prices requests you send in the meantime at the previous rates.

## Enforce layered budgets

The dashboard reports spend after the fact. To cap it, declare spend and token limits with the `EnterpriseAgentgatewayBudget` CRD. It sits a level above a hand-authored `RateLimitConfig`, purpose-built for USD/token budgets, and the Cost Management dashboard's **Budgets** tab reads it. The controller compiles each entry into a controller-managed `RateLimitConfig` for you, named `agw-budget-<budget-name>-<hash>`.

Each entry's `subject` scopes it to resolved dimensions: `model`, `provider`, `virtualKey`, `user`, and `group` by default, the same dimensions Cost Management attributes spend by. Entries at different levels compose. The budget below combines a group-wide guardrail that logs overages with a per-user default that blocks them.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBudget
metadata:
  name: team-budgets
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
  - name: any-user-daily-tokens
    subject:
      user: "*"
    limit:
      unit: Tokens
      amount: 100000
    window:
      unit: Day
    onBudgetExceeded: Block
  - name: alice-daily-tokens
    subject:
      user: alice
    limit:
      unit: Tokens
      amount: 500000
    window:
      unit: Day
    onBudgetExceeded: Block
EOF
```

- **`onBudgetExceeded`** decides what happens at the limit. `Block` rejects further requests with `429`, and `Audit` records the overage and forwards the request. Auditing the group budget while blocking individuals caps one runaway caller and leaves the rest of the group serving traffic.
- **A `"*"` subject value creates a separate allowance per value.** `user: "*"` gives each distinct user their own 100,000 tokens per day, so a new user gets the default the first time they call.
- **An exact entry takes precedence over the wildcard** on the same dimension. `alice-daily-tokens` raises alice to 500,000 tokens and suppresses the default for her alone. Entries on other dimensions still stack, so bob's requests debit both the per-user default and the engineering group budget.

Windows roll rather than align to the calendar: `Day` covers a rolling 24 hours and `Month` a rolling 30 days.

Add `entBudgetEnforcement` to the existing `api-key-auth` policy with a merge patch, so the gateway discovers and enforces `EnterpriseAgentgatewayBudget` resources in the same namespace:

```bash
kubectl patch enterpriseagentgatewaypolicy api-key-auth -n agentgateway-system \
  --type merge -p '{"spec":{"traffic":{"entBudgetEnforcement":{"discovery":{"namespaces":{"from":"Same"}}}}}}'
```

Confirm the controller compiled a `RateLimitConfig` from the budget:

```bash
kubectl get ratelimitconfig -n agentgateway-system
```

Expected output: a resource named `agw-budget-team-budgets-<hash>`.

See the [Budget Limits docs](https://docs.solo.io/agentgateway/latest/llm/cost-controls/budget-limits/) for the full CRD reference: custom subject dimensions, `discovery` scoping across namespaces, and the up-to-64-entries-per-resource limit.

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

Expected output: all 8 requests `HTTP 200`. Alice's requests run on `gpt-5.6-luna` and bob's on the pricier `gpt-5.6-terra`, so the dashboard's model pivot has two models to separate. The budgets above are generous enough that this traffic won't exhaust any of them.

## View the Cost Management dashboard

Port-forward the Solo UI:

```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
```

Open [http://localhost:4000/age/](http://localhost:4000/age/) and select **Cost Management** from the menu.

- **Dashboard**: a summary row of total spend, input versus output tokens, and request count with an average cost per request, followed by paired **Spend by** and **Spend Over Time by** panels. Each panel carries its own pivot. Switch one from **Provider** to **Group** to **User**, and the same traffic re-slices as `research` against `engineering`, then as alice against bob. The **Filters** row narrows every panel at once by scope, provider, model, or virtual key, and **Export CSV** downloads the current view.
  - Every request attributes to a user, group, and virtual key, because each key sets `id`, `user`, and `group`. Requests where a dimension resolves to nothing appear under **Unattributed**.
  - Model breakdowns list the model ID the provider returns on the response. The `gpt-5.6` family returns the same undated ID you priced, so `gpt-5.6-luna` and `gpt-5.6-terra` appear verbatim. Models that answer with a dated snapshot ID, such as `gpt-5.4-nano-2026-03-17`, show that snapshot instead, and the gateway resolves it back to the alias in your catalog.
- **Model Cost Catalog**: the header names the base catalog, lists your overlay under **Overlay catalogs are applied in this order**, and reports `1 model entry is overridden by overlay sources`. Search the table to see both halves of the overlay:
  - `gpt-5.6` returns the three added models with the input, output, cache-read, and cache-write rates you supplied. Before the overlay they had no row at all.
  - `gpt-5.5` shows `$2.50` input, `$15.00` output, and `$0.25` cache read instead of the list `$5.00`/`$30.00`/`$0.50`, tagged source `Override`.
- **Budgets**: the `team-budgets` `EnterpriseAgentgatewayBudget` from above, listed with `3` entries. Click the row to open its detail drawer, where each entry shows its subject, window, and usage against its limit. With the traffic you just sent, all three read `On track`:
  - `alice-daily-tokens`, subject `user alice`, e.g. `1,041 tokens of 500,000 tokens`
  - `any-user-daily-tokens`, subject `user *`, e.g. `42 tokens of 100,000 tokens`
  - `engineering-monthly-usd`, subject `group engineering`, `$0.00 of $50.00`

Treat spend as an estimate. The gateway multiplies token counts by the per-token prices in your catalog, so the totals won't reconcile line-for-line against your LLM provider's invoice.

## Cleanup

```bash
# Budget enforcement
kubectl delete enterpriseagentgatewaybudget -n agentgateway-system team-budgets --ignore-not-found

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

To disable the Cost Management feature:

```bash
helm upgrade -i management oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management \
--namespace agentgateway-system \
--version "$AGW_UI_VERSION" \
--reuse-values \
--set products.agentgateway.features.cost-management=false
```
