# LLM Cost Management

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`. `002` is not optional here: its Solo UI and OTEL collector are what Cost Management runs in.
- A valid OpenAI API key, exported as `OPENAI_API_KEY`.

## Lab Objectives
- Issue per-user API keys (virtual keys) and route them to OpenAI through an `EnterpriseAgentgatewayBackend`
- Enable the Cost Management section of the Solo UI
- Configure a model cost catalog so the gateway computes realized USD spend per request
- Attribute spend to users and groups via virtual key metadata
- Enforce per-user and per-group spend/token budgets with `EnterpriseAgentgatewayBudget`
- View spend, budgets, and the model cost catalog in the Cost Management dashboard

## About Cost Management

Lab `002` gives you raw token-usage metrics in Grafana and access logs. Cost Management turns that same telemetry into one place that answers "what are we spending on LLMs, and who is spending it": spend broken down by provider, model, group, user, or virtual key, budget usage against configured limits, and the model cost catalog itself, all filterable with CSV export. It's built from the gateway's tracing spans, not ad hoc PromQL, so you don't hand-write queries to see per-user cost.

## Set up the OpenAI backend

Create the OpenAI credential secret. The gateway uses it to authenticate upstream; callers never see it.

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

The backend should show `ACCEPTED   True`. The route should print `Accepted=True` and `ResolvedRefs=True`.

## Create per-user virtual keys

Create one Secret per user, each labeled `app: llm-virtual-keys`. The auth policy in the next section discovers keys by that label instead of by a single Secret name, so you onboard a new user by adding another labeled Secret, with no edit to a central Secret or the policy.

Each entry stores the API key plus the metadata both Cost Management and rate limiting read: `user_id` for token-budget CEL expressions (the client never supplies it), and `user`/`group` for Cost Management's spend attribution. Cost Management resolves `user` from `apiKey.user`, then `jwt.sub`, then `jwt.email`; it resolves `group` from `jwt.group`, then `apiKey.group`.

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

> **Note:** The `user`/`group` resolution order lives in the `agentgateway-enterprise-budget-dimensions` ConfigMap in the control-plane namespace, and you can customize it. If spend shows up as **Unattributed** in the dashboard, check that the field name on your key metadata matches what that ConfigMap's hierarchy looks up before assuming the request itself is misconfigured.

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
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}]}'

curl -s -o /dev/null -w "bob: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-bob-xyz789uvw012" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}]}'

curl -s -o /dev/null -w "invalid: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer sk-invalid-key" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}]}'
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

Check that the management pods rolled out:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/instance=management
```

## Configure a model cost catalog

Without a cost catalog, the dashboard shows token/request volume but spend stays at `$0.00`. Create a catalog with the same `gpt-4o-mini` pricing used elsewhere in this workshop ($0.15 per 1M input tokens, $0.60 per 1M output tokens):

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
            "gpt-4o-mini": {
              "rates": { "input": "0.15", "output": "0.60" }
            }
          }
        }
      }
    }
EOF
```

> **Tip:** For a broader catalog covering many providers/models at once, generate one instead of hand-writing it: `agctl costs import --pretty --providers openai,anthropic --out ./catalog.json`, then `kubectl create configmap llm-model-costs --from-file=catalog.json=./catalog.json -n agentgateway-system --dry-run=client -o yaml | kubectl apply -f -`.

The catalog is wired to the Gateway through the `agentgateway-config` `EnterpriseAgentgatewayParameters` that `001` created and attached via `spec.infrastructure.parametersRef`. Add `modelCatalog` to it with a **merge patch** rather than `kubectl apply`: a full `apply` without the fields `001` set (like `logging`) would strip them, because `kubectl apply` computes a three-way diff against the last-applied config.

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type merge -p '{"spec":{"modelCatalog":{"sources":[{"configMap":{"name":"llm-model-costs","key":"catalog.json"}}]}}}'
```

Confirm the existing fields (e.g. `logging`) are still present alongside the new `modelCatalog` block:

```bash
kubectl get enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system -o yaml
```

## Enforce per-user and per-group budgets

Declare spend/token limits with the `EnterpriseAgentgatewayBudget` CRD. It's a higher-level resource than a hand-authored `RateLimitConfig`, purpose-built for USD/token budgets, and it's what the Cost Management dashboard's **Budgets** tab reads. The controller compiles each entry into a controller-managed `RateLimitConfig` for you, named `agw-budget-<budget-name>-<hash>`.

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

`onBudgetExceeded` controls what happens once a limit is hit. Alice's budget uses `Block`, which rejects further requests with `429`. Bob's budget uses `Audit`, which logs the overage but still lets requests through. Use `Audit` for a team you want to monitor without cutting off.

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
    -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}'
done

for i in {1..3}; do
  curl -s -o /dev/null -w "bob request $i: HTTP %{http_code}\n" "$GATEWAY_IP:8080/openai" \
    -H "content-type: application/json" \
    -H "Authorization: Bearer sk-bob-xyz789uvw012" \
    -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Whats your favorite poem?"}]}'
done
```

Expected output: all 8 requests `HTTP 200`. The budgets above are generous enough that this traffic won't exhaust either one.

## View the Cost Management dashboard

Port-forward the Solo UI:

```bash
kubectl port-forward -n agentgateway-system svc/solo-enterprise-ui 4000:80
```

Open [http://localhost:4000/age/](http://localhost:4000/age/) and select **Cost Management** from the menu.

- **Spend**: time-series spend, filterable and groupable by provider, model, group, user, or virtual key. Alice and bob's requests should appear broken out by `user` (and by `group`, once a few more requests have flowed through). Export the current view as CSV.
- **Model Cost Catalog**: confirm `gpt-4o-mini` shows the `$0.15`/`$0.60` per-1M-token rates from the ConfigMap you created above.
- **Budgets**: the `team-budgets` `EnterpriseAgentgatewayBudget` from above, with live usage against each entry (e.g. `44 tokens of 100,000 tokens · On track` for alice).

Spend is an estimate computed from token counts and the per-token prices in your catalog, not a billing-grade reconciliation against your LLM provider's invoice.

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
