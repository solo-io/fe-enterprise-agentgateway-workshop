# LLM Cost Management

## Pre-requisites
Complete the setup in `001` and `002` first. Cost Management runs on the Solo UI and OTEL collector that `002` installs, so `002` is required here.
- A valid OpenAI API key, exported as `OPENAI_API_KEY`.

## Lab Objectives
- Issue per-user API keys (virtual keys) and route them to OpenAI through an `EnterpriseAgentgatewayBackend`
- Enable the Cost Management section of the Solo UI
- Layer your own model cost catalog over the gateway's base catalog to control per-request USD pricing
- Attribute spend to users and groups via virtual key metadata
- Enforce per-user and per-group spend/token budgets with `EnterpriseAgentgatewayBudget`
- View spend, budgets, and the model cost catalog in the Cost Management dashboard

## About Cost Management

Lab `002` gives you raw token-usage metrics in Grafana and access logs. Cost Management reads the gateway's tracing spans and reports spend by provider, model, group, user, or virtual key, alongside budget usage against your configured limits and the model cost catalog itself. You filter any view and export it as CSV. Answering "what are we spending on LLMs, and who is spending it" no longer means hand-writing PromQL.

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

Each entry stores the API key plus the metadata both Cost Management and rate limiting read: `user_id` for token-budget CEL expressions (the client doesn't supply it), and `user`/`group` for Cost Management's spend attribution. Cost Management resolves `user` from `apiKey.user`, then `jwt.sub`, then `jwt.email`; it resolves `group` from `jwt.group`, then `apiKey.group`.

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
        "user_id": "bob",
        "user": "bob",
        "group": "engineering"
      }
    }
EOF
```

> **Note:** The `user`/`group` resolution order lives in the `agentgateway-enterprise-budget-dimensions` ConfigMap in the control-plane namespace, and you can customize it. If spend shows up as **Unattributed** in the dashboard, compare the field names on your key metadata against that ConfigMap's hierarchy before assuming the request itself is misconfigured.

> **Tip:** For tiered budgets, multi-tenant `(tenant_id, user_id)` scoping, or a deeper walkthrough of virtual-key mechanics, see the [virtual-keys lab](../security/virtual-keys.md). This lab sets up only the minimum Cost Management needs.

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
  -d '{"model": "gpt-5.4-nano", "messages": [{"role": "user", "content": "Hello!"}]}'

curl -s -o /dev/null -w "bob: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-bob-xyz789uvw012" \
  -d '{"model": "gpt-5.4-nano", "messages": [{"role": "user", "content": "Hello!"}]}'

curl -s -o /dev/null -w "invalid: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-invalid-key" \
  -d '{"model": "gpt-5.4-nano", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Expected output: alice and bob both `HTTP 200`, the invalid key `HTTP 401`.

## Enable Cost Management in the Solo UI

Layer the `cost-management` feature flag onto the existing `management` release from `002` with `--reuse-values`, rather than re-specifying every value from that install:

```bash
export AGW_UI_VERSION=0.5.1

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

The gateway ships a base catalog: the controller creates an `agentgateway-proxy-model-catalog` ConfigMap alongside each Gateway, covering current OpenAI, Anthropic, and Gemini models. The gateway prices those models with no configuration from you. A model the base catalog doesn't know, such as a mock or self-hosted model, still contributes token and request volume but `$0.00` of spend.

You layer your own catalog on top of that base as an overlay, to correct a rate or to price a model the base doesn't cover. Create one that pins `gpt-5.4-nano` to the pricing used elsewhere in this workshop ($0.20 per 1M input tokens, $1.25 per 1M output tokens):

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
            "gpt-5.4-nano": {
              "rates": { "input": "0.20", "output": "1.25" }
            }
          }
        }
      }
    }
EOF
```

> **Note:** An overlay entry replaces the base entry for that model outright rather than merging into it. The base `gpt-5.4-nano` entry also carries a cache-read rate, which the overlay above drops because it sets only `input` and `output`. When you override a model that has cache pricing, restate its `cacheRead`/`cacheWrite` rates too.

> **Tip:** For a broader catalog covering many providers/models at once, generate one with the `agctl` CLI instead of hand-writing it: `agctl costs import --pretty --providers openai,anthropic --out ./catalog.json`, then `kubectl create configmap llm-model-costs --from-file=catalog.json=./catalog.json -n agentgateway-system --dry-run=client -o yaml | kubectl apply -f -`.

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

## Enforce per-user and per-group budgets

Declare spend/token limits with the `EnterpriseAgentgatewayBudget` CRD. It sits a level above a hand-authored `RateLimitConfig`, purpose-built for USD/token budgets, and the Cost Management dashboard's **Budgets** tab reads it. The controller compiles each entry into a controller-managed `RateLimitConfig` for you, named `agw-budget-<budget-name>-<hash>`.

Each budget entry's `subject` scopes it to one or more resolved dimensions: `model`, `provider`, `virtualKey`, `user`, and `group` are available by default, the same dimensions Cost Management attributes spend by. Give alice a token budget and bob a USD budget to see both limit types:

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBudget
metadata:
  name: team-budgets
  namespace: agentgateway-system
spec:
  budgets:
  - name: alice-daily-tokens
    subject:
      user: alice
    limit:
      unit: Tokens
      amount: 100000
    window:
      unit: Day
    onBudgetExceeded: Block
  - name: bob-daily-usd
    subject:
      user: bob
    limit:
      unit: USD
      amount: 5
    window:
      unit: Day
    onBudgetExceeded: Audit
EOF
```

`onBudgetExceeded` controls the gateway's response once a caller reaches a limit. Alice's budget uses `Block`, so the gateway rejects further requests with `429`. Bob's uses `Audit`: the gateway records the overage and forwards the request anyway. Pick `Audit` for a team you want to monitor without cutting off.

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
    -d '{"model": "gpt-5.4-nano", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}'
done

for i in {1..3}; do
  curl -s -o /dev/null -w "bob request $i: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-bob-xyz789uvw012" \
    -d '{"model": "gpt-5.4-nano", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}'
done
```

Expected output: all 8 requests `HTTP 200`. The budgets above are generous enough that this traffic won't exhaust either one.

## View the Cost Management dashboard

Port-forward the Solo UI:

```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
```

Open [http://localhost:4000/age/](http://localhost:4000/age/) and select **Cost Management** from the menu.

- **Spend**: time-series spend, filterable and groupable by provider, model, group, user, or virtual key. Alice and bob's requests appear broken out both by `user` and by `group`. Model breakdowns list the dated model ID that OpenAI returns, `gpt-5.4-nano-2026-03-17`, rather than the `gpt-5.4-nano` alias you priced; the gateway resolves the dated ID back to your catalog entry. Export the current view as CSV.
- **Model Cost Catalog**: confirm `gpt-5.4-nano` shows the `$0.20`/`$1.25` per-1M-token rates from the ConfigMap you created above, with source `Override`, meaning your overlay took precedence over the base catalog entry. The other rows show source `Base`.
- **Budgets**: the `team-budgets` `EnterpriseAgentgatewayBudget` from above. Click the row to open its detail drawer, where each entry shows live usage against its limit (e.g. `506 tokens of 100,000 tokens · On track` for alice, `$0.00 of $5.00 · On track` for bob).

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
