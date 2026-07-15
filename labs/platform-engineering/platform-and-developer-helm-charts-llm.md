# Platform and Developer Helm Charts: Separation of Concerns

Give every application team direct access to the raw `HTTPRoute`, `EnterpriseAgentgatewayBackend`, and `EnterpriseAgentgatewayPolicy` CRDs and any team can grant itself a bigger rate limit, disable JWT on its own route, change the access-log format, or claim a URL prefix that belongs to someone else. Security, observability, and cost control then depend on someone catching each mistake in review.

This lab uses two Helm charts to split the gateway into two personas:

- **`agentgateway-platform`** is owned by the platform team. It owns the `Gateway`, the proxy fleet shape, the observability pipeline, the security baseline (JWT, WAF), the **URL space**, and the **cost tiers**. It also onboards application teams.
- **`agentgateway-developer`** is owned by an application team. A team uses it to self-serve LLM and MCP endpoints under the path prefix the platform assigned them. Its `values.schema.json` has **no field** for rate limits, auth, WAF, or logging, so a team cannot express a traffic policy. This lab exercises the LLM path; for the MCP path, see [Self-Service MCP Endpoints](platform-and-developer-helm-charts-mcp.md).

Enforcement is **structural**. A team ships endpoints all day, while its tier, its prefix, and the platform's controls stay out of reach: the chart it installs has no vocabulary for them.

> This lab requires Enterprise Agentgateway **v2026.6.3** or later (the version installed in `001`).

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- **Helm 3** installed.
- Run every command **from the workshop root** (the directory that contains `charts/`). Chart paths below are repo-root-relative.
- The two charts referenced here live at `charts/agentgateway-platform` and `charts/agentgateway-developer`.

> **Note:** The platform chart installs its **own** `Gateway` named `agw-platform`, alongside the `agentgateway-proxy` gateway from `001`. The two coexist; this lab never modifies `001`.

## Lab Objectives
- Install the platform chart and stand up a platform-owned `Gateway`, tiers, and observability baseline
- Onboard two application teams at two different cost tiers (`gold` and `silver`)
- Let developers self-serve LLM endpoints with the developer chart, configuring **only** what they own
- Prove teams cannot escape their tier, their URL prefix, or the platform's controls
- Re-tier a team with a **one-line** platform values change and a `helm upgrade`
- Turn on JWT authentication for **every** team at once, without any team changing its release

---

## Overview

### Two personas, one contract

```
                        PLATFORM TEAM  (owns charts/agentgateway-platform)
                                      │
               Gateway: agw-platform  +  proxy fleet  +  access logs
               +  security baseline (JWT / WAF)  +  cost tiers
                                      │
     ┌─────────────────────────────────┴─────────────────────────────────┐
     │  parent HTTPRoute: team-team-alpha                                  │  parent HTTPRoute: team-team-beta
     │  matches  /teams/team-alpha                                         │  matches  /teams/team-beta
     │  tier policy: GOLD  (rateLimit + retry + timeout)                   │  tier policy: SILVER (rateLimit + timeout)
     │  delegates to child routes labeled team=team-alpha in ns team-alpha │  delegates to team=team-beta in ns team-beta
     └─────────────────────────────────┬─────────────────────────────────┘
                                      │  delegation (Gateway API)
     ┌─────────────────────────────────┴─────────────────────────────────┐
     │  APP TEAM team-alpha (owns charts/agentgateway-developer)           │  APP TEAM team-beta
     │  namespace: team-alpha                                              │  namespace: team-beta
     │  child HTTPRoute: team-alpha-chat   label team=team-alpha           │  child HTTPRoute: team-beta-chat  label team=team-beta
     │  matches  /teams/team-alpha/chat                                    │  matches  /teams/team-beta/chat
     │  backend: team-alpha-chat (LLM)                                     │  backend: team-beta-chat (LLM)
     └─────────────────────────────────────────────────────────────────────┘
```

The platform chart names each team's parent route `team-<team>`, so onboarding a team called `team-alpha` yields a parent route `team-team-alpha`. The two charts meet at an explicit **contract**:

| Contract element | Set by | Value for `team-alpha` |
|---|---|---|
| Path prefix | platform (at onboarding) | `/teams/team-alpha` |
| Delegation label | platform ↔ developer | `team: team-alpha` |
| Namespace | platform (at onboarding) | `team-alpha` |
| Cost tier | platform (at onboarding) | `gold` |

The platform chart's parent route for a team delegates **only** to child routes that (a) carry the label `team=<name>` **and** (b) live in that team's namespace. The developer chart stamps that label on every route it creates and prefixes every path with `/teams/<team>`. The platform attaches everything else (tiers, auth, logging) to the **parent** route, and every child the team adds inherits it.

### Why the developer chart cannot express a policy

The developer chart's `values.schema.json` uses `"additionalProperties": false` and exposes only three top-level keys: `global`, `team`, and `endpoints`. There is no `tiers` key, no `rateLimit`, no `auth` beyond a per-endpoint `secretRef`/`passthrough` for the upstream provider, and no WAF or logging. A team that tries to add one gets a schema error at `helm` time, before anything reaches the cluster (you will see this in [Step 4](#step-4-what-teams-cannot-do)).

---

## Step 1: The platform team installs the platform chart

The platform team owns a single values file. It defines the gateway, the tier catalog, and the roster of onboarded teams. Create `platform-values.yaml`:

```yaml
gateway:
  name: agw-platform
tiers:
  gold:
    rateLimit:
      # -- Input-token budget per proxy replica, per minute
      tokensPerMinute: 1000
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
      tokensPerMinute: 5
    timeouts:
      request: 60s
teams:
  - name: team-alpha
    namespace: team-alpha
    tier: gold
  - name: team-beta
    namespace: team-beta
    tier: silver
```

> **Note on the tiny budgets:** `gold` at `1000` and `silver` at `5` input tokens/minute are set small so you can trip a `429` in a couple of requests. Production tiers would be far higher (the chart's own defaults are `100000` and `10000`). What matters about a tier is who sets the number: the platform assigns it, and the team never chooses it. `gold` also carries a retry policy while `silver` does not, because a tier bundles cost *and* resilience, all platform-owned.

Everything the platform owns but did not override comes from the chart's `values.yaml` defaults:

| Platform-owned concern | Default | Rendered as |
|---|---|---|
| Proxy replicas | `2` | `EnterpriseAgentgatewayParameters` |
| Pod disruption budget | `minAvailable: 1` | `EnterpriseAgentgatewayParameters` |
| Graceful drain | `10s` min / `60s` max | `EnterpriseAgentgatewayParameters` |
| Access logging | enabled, with `llm.streaming` + `llm.cached_tokens` attributes | `EnterpriseAgentgatewayPolicy` on the Gateway |
| JWT | off by default | enabled in [Step 6](#step-6-the-platform-enables-jwt-for-everyone) |
| WAF | off by default | available via `security.waf.enabled=true` (not covered in this lab) |

Install the chart into `agentgateway-system` (run from the workshop root):

```bash
helm install agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml
```

Verify the platform resources were created and accepted:

```bash
kubectl get gateway,httproute,enterpriseagentgatewaypolicy,enterpriseagentgatewayparameters \
  -n agentgateway-system -l app.kubernetes.io/part-of=agentgateway-platform
```

Expected output:

```
NAME                                             CLASS                     ADDRESS          PROGRAMMED   AGE
gateway.gateway.networking.k8s.io/agw-platform   enterprise-agentgateway   172.18.255.249   True         3s

NAME                                                       HOSTNAMES   AGE
httproute.gateway.networking.k8s.io/team-team-alpha                    3s
httproute.gateway.networking.k8s.io/team-team-beta                     3s

NAME                                                                                       ACCEPTED   ATTACHED   AGE
enterpriseagentgatewaypolicy.enterpriseagentgateway.solo.io/agw-platform-access-log        True       True       3s
enterpriseagentgatewaypolicy.enterpriseagentgateway.solo.io/team-team-alpha-tier           True       True       3s
enterpriseagentgatewaypolicy.enterpriseagentgateway.solo.io/team-team-beta-tier            True       True       3s

NAME                                                                                       AGE
enterpriseagentgatewayparameters.enterpriseagentgateway.solo.io/agw-platform-config        3s
```

Both teams are now onboarded: each has a parent route (`team-team-alpha`, `team-team-beta`) and a tier policy (`team-team-alpha-tier`, `team-team-beta-tier`). No team endpoints exist yet, so the parent routes have nothing to delegate to.

Wait for the proxy fleet to roll out:

```bash
kubectl rollout status -n agentgateway-system deploy/agw-platform --timeout=180s
```

Expected output:

```
deployment "agw-platform" successfully rolled out
```

Capture the gateway address for the rest of the lab:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agw-platform -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo "GATEWAY_IP=${GATEWAY_IP}"
```

---

## Step 2: Developers self-serve their endpoints

Now the application teams take over. Each team runs its own model backend and declares its endpoints with the developer chart. Nothing a team does here touches security, rate limits, or logging.

### Deploy each team's model backend

Each team namespace runs the mock OpenAI server from the [Mock OpenAI Server lab](../routing/configure-mock-openai-server.md): the same simulator, deployed into the **team's** namespace (`team-alpha`, `team-beta`) instead of `agentgateway-system`. Create the namespaces and deploy one mock per team:

```bash
for NS in team-alpha team-beta; do
  kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o
  namespace: ${NS}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-4o
  template:
    metadata:
      labels:
        app: mock-gpt-4o
    spec:
      containers:
        - name: vllm-sim
          image: ghcr.io/llm-d/llm-d-inference-sim:latest
          imagePullPolicy: IfNotPresent
          args:
            - --model
            - mock-gpt-4o
            - --port
            - "8000"
            - --max-loras
            - "2"
            - --lora-modules
            - '{"name": "food-review-1"}'
          ports:
            - containerPort: 8000
              name: http
              protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpt-4o-svc
  namespace: ${NS}
  labels:
    app: mock-gpt-4o
spec:
  selector:
    app: mock-gpt-4o
  ports:
    - name: http
      protocol: TCP
      port: 8000
      targetPort: 8000
  type: ClusterIP
EOF
done

kubectl rollout status -n team-alpha deploy/mock-gpt-4o --timeout=180s
kubectl rollout status -n team-beta  deploy/mock-gpt-4o --timeout=180s
```

Expected output:

```
deployment "mock-gpt-4o" successfully rolled out
deployment "mock-gpt-4o" successfully rolled out
```

### Team alpha declares its endpoint

Team alpha creates `team-alpha-values.yaml`. It names its team (the label + prefix contract), then declares one LLM endpoint pointing at its in-namespace mock via an OpenAI-compatible host override:

```yaml
team: team-alpha
endpoints:
  - name: chat
    type: llm
    provider: openai
    model: mock-gpt-4o
    path: /chat
    host: mock-gpt-4o-svc.team-alpha.svc.cluster.local
    port: 8000
    apiPath: /v1/chat/completions
    auth:
      passthrough: true
```

Install the developer chart as the team's own release, in the team's namespace:

```bash
helm install team-alpha charts/agentgateway-developer \
  -n team-alpha \
  --values team-alpha-values.yaml
```

Verify the child route and backend, and note the delegation label:

```bash
kubectl get httproute,enterpriseagentgatewaybackend -n team-alpha
kubectl get httproute team-alpha-chat -n team-alpha -o jsonpath='{.metadata.labels}{"\n"}'
```

Expected output:

```
NAME                                                  HOSTNAMES   AGE
httproute.gateway.networking.k8s.io/team-alpha-chat               4s

NAME                                                                           ACCEPTED   AGE
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/team-alpha-chat   True       4s

{"app.kubernetes.io/instance":"team-alpha","app.kubernetes.io/managed-by":"Helm","team":"team-alpha"}
```

The chart stamped `team: team-alpha` on the route, and that label is what makes the platform's parent route pick this child up. The child route matches `/teams/team-alpha/chat` because the developer chart prepended the platform-owned `/teams/team-alpha` prefix to the team's `/chat`; the team typed only `/chat`.

### Team beta declares its endpoint

Team beta repeats this with its own values (`team-beta-values.yaml`); only the team name and host change:

```yaml
team: team-beta
endpoints:
  - name: chat
    type: llm
    provider: openai
    model: mock-gpt-4o
    path: /chat
    host: mock-gpt-4o-svc.team-beta.svc.cluster.local
    port: 8000
    apiPath: /v1/chat/completions
    auth:
      passthrough: true
```

```bash
helm install team-beta charts/agentgateway-developer \
  -n team-beta \
  --values team-beta-values.yaml
```

### Call the endpoints

Give the proxy a few seconds to program the new routes, then call team alpha's endpoint:

```bash
sleep 10
curl -s -o /dev/null -w "team-alpha chat: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-alpha/chat" \
  -H "content-type: application/json" \
  -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"Whats your favorite poem?"}]}'
```

Expected output:

```
team-alpha chat: 200
```

The request flowed `Gateway agw-platform → parent route team-team-alpha (/teams/team-alpha) → delegates to child team-alpha-chat (/teams/team-alpha/chat) → backend`.

> **What team alpha did NOT configure.** The team set a team name and one endpoint. It did **not** set a rate limit, a JWT policy, a WAF rule, an access-log format, or a timeout. The platform attaches all of those to the parent route, and this child inherits them. The team could not have set them if it wanted to; the chart has no field for them.

---

## Step 3: Cost tiers in action

Team alpha is `gold`; team beta is `silver`. Neither team chose this; the platform assigned it in its `teams` list. The difference is enforced entirely by the platform's tier policies on the parent routes.

Each proxy replica enforces the tier budget as a **local** token limit, independently of the other replicas. The test prompt below costs about 10 input tokens (the gateway's access log reports `gen_ai.usage.input_tokens=10` for this request). The limiter checks admission *before* it debits the request's cost, so `silver`'s 5-token/minute budget still admits the first request, which then overdraws the budget by 10 tokens. The second request finds the budget negative and gets a `429`. `gold` (1000 tokens/minute) has plenty of headroom and sustains the same traffic.

> **Note on timing and replicas:** Local token-limit enforcement can lag a few seconds after install, so wait about 12 seconds before the counted requests. Because the budget is per replica and the proxy runs 2 replicas, which `silver` request draws the first `429` depends on which replica served each one. If `beta-2` below still returns `200`, send one more request and it will be `429`.

```bash
sleep 12

echo "== team-alpha (gold) =="
for i in 1 2; do
  curl -s -o /dev/null -w "alpha-$i: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-alpha/chat" \
    -H "content-type: application/json" \
    -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"Whats your favorite poem?"}]}'
done

echo "== team-beta (silver) =="
for i in 1 2; do
  curl -s -o /dev/null -w "beta-$i: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-beta/chat" \
    -H "content-type: application/json" \
    -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"Whats your favorite poem?"}]}'
done
```

Expected output:

```
== team-alpha (gold) ==
alpha-1: 200
alpha-2: 200
== team-beta (silver) ==
beta-1: 200
beta-2: 429
```

Both teams sent identical traffic to identically-shaped endpoints; the platform-assigned tier made the difference.

---

## Step 4: What teams cannot do

Try the escapes a team might attempt. Each one fails inside the tooling or the routing layer, with no human review involved.

### It cannot grant itself a bigger budget

Suppose team alpha tries to redefine its tier through its own chart by adding a `tiers` block. The developer chart's schema rejects it before anything reaches the cluster:

```bash
helm template team-alpha charts/agentgateway-developer \
  -n team-alpha \
  --values team-alpha-values.yaml \
  --set tiers.gold.rateLimit.tokensPerMinute=999999
```

Expected output:

```
Error: values don't meet the specifications of the schema(s) in the following chart(s):
agentgateway-developer:
- at '': additional properties 'tiers' not allowed
```

Any traffic knob smuggled onto an endpoint fails the same way, for example an endpoint-level `rateLimit`. Add one to `team-alpha-values.yaml` and try to install it:

```bash
cat > team-alpha-cheat.yaml <<'EOF'
team: team-alpha
endpoints:
  - name: chat
    type: llm
    provider: openai
    model: mock-gpt-4o
    path: /chat
    auth:
      passthrough: true
    rateLimit:
      tokensPerMinute: 1000000
EOF

helm install team-alpha-cheat charts/agentgateway-developer -n team-alpha --values team-alpha-cheat.yaml --dry-run=client
```

Expected output:

```
Error: INSTALLATION FAILED: values don't meet the specifications of the schema(s) in the following chart(s):
agentgateway-developer:
- at '/endpoints/0': additional properties 'rateLimit' not allowed
```

Tiers and traffic policies exist only in the platform chart; the developer chart has no vocabulary to express a bigger budget. Remove the file:

```bash
rm -f team-alpha-cheat.yaml
```

### It cannot claim another team's path

Suppose someone in team beta tries to serve traffic under team alpha's prefix by creating a correctly-labeled route in the **wrong** namespace:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: hijack
  namespace: team-beta
  labels:
    team: team-alpha
spec:
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /teams/team-alpha/hijack
      backendRefs:
        - name: team-beta-chat
          group: enterpriseagentgateway.solo.io
          kind: EnterpriseAgentgatewayBackend
EOF

sleep 5
curl -s -o /dev/null -w "cross-namespace hijack: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-alpha/hijack" \
  -H "content-type: application/json" \
  -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"hi"}]}'
```

Expected output:

```
cross-namespace hijack: 404
```

The route exists, carries the right label, and matches the path, but the `team-team-alpha` parent route delegates **only** to children in namespace `team-alpha`, so it never selects the beta-namespaced route. Clean up the attempt:

```bash
kubectl delete httproute hijack -n team-beta --ignore-not-found
```

> **Both halves of the contract are required.** Delegation demands the `team=<name>` label **and** the team's own namespace. A hand-rolled route in the correct namespace that omits the label stays invisible too: the parent route's label selector never matches it, so it also returns `404`. Routes created through the developer chart satisfy both halves, and those inherit the platform's tier and security policies.

### Optional RBAC hardening

The developer chart already makes traffic policies un-representable, which covers the common case. For defense-in-depth against a team hand-writing raw CRDs, a platform team can withhold Kubernetes RBAC `create`/`update` permission on `EnterpriseAgentgatewayPolicy` (and, if desired, `WAFPolicy`) in the team namespaces, granting only `HTTPRoute` and `EnterpriseAgentgatewayBackend`. Then a developer cannot author a policy at all, in or out of the chart. This RBAC lives in your cluster's access model, not in these charts.

---

## Step 5: The platform re-tiers a team

A tier change is a one-line edit in the platform's values file; no team release changes. Move team alpha from `gold` to `silver` in `platform-values.yaml`:

```yaml
teams:
  - name: team-alpha
    namespace: team-alpha
    tier: silver
  - name: team-beta
    namespace: team-beta
    tier: silver
```

Apply it:

```bash
helm upgrade agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml
```

Confirm alpha's tier policy now carries the `silver` label:

```bash
kubectl get enterpriseagentgatewaypolicy team-team-alpha-tier -n agentgateway-system \
  -o jsonpath='{.metadata.labels.agentgateway\.solo\.io/tier}{"\n"}'
```

Expected output:

```
silver
```

Give the new budget a few seconds to propagate, then send the same pair of requests to team alpha:

```bash
sleep 8
for i in 1 2; do
  curl -s -o /dev/null -w "alpha-silver-$i: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-alpha/chat" \
    -H "content-type: application/json" \
    -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"Whats your favorite poem?"}]}'
done
```

Expected output:

```
alpha-silver-1: 200
alpha-silver-2: 429
```

Team alpha now behaves like team beta, and it never touched its own release. Flip it back to `gold` (restore the `tier: gold` line for team-alpha in `platform-values.yaml`) and upgrade again:

```bash
helm upgrade agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml
```

---

## Step 6: The platform enables JWT for everyone

The platform turns on JWT authentication for the whole gateway with a single upgrade. The JWT policy targets the `Gateway`, so it protects every team's endpoint at once, and again no team release changes.

This step reuses the inline JWKS and the static `DEV_TOKEN_1` from the [JWT Auth with RBAC lab](../security/jwt-auth-with-rbac.md). Save that lab's `keys` block into a file named `jwks.json`:

```bash
cat > jwks.json <<'EOF'
{
  "keys": [
    {
      "kty": "RSA",
      "kid": "solo-public-key-001",
      "n": "vlmc5pb-jYaOq75Y4r91AC2iuS9B0sm6sxzRm3oOG7nIt2F1hHd4AKll2jd6BZg437qvsLdREnbnVrr8kU0drmJNPHL-xbsTz_cQa95GuKb6AI6osAaUAEL3dPjuoqkGNRe1sAJyOi48qtcbV0kPWcwFmCV0-OiqliCms12jrd1PSI_LYiNc3GcutpxY6BiHkbxxNeIuWDxE-i_Obq8EhhGkwha1KVUvLHV-EwD4M_AY8BegGsX-sjoChXOxyueu_ReqWV227I-FTKwMnjwWW0BQkeI6g1w1WqADmtKZ2sLamwGUJgWt4ZgIyhQ-iQfeN1WN2iupTWa5JAsw--CQJw",
      "e": "AQAB",
      "use": "sig",
      "alg": "RS256"
    }
  ]
}
EOF
```

> **Demo-only; do not use outside this workshop.** `solo-public-key-001` and the token below are a public demo keypair shared across this workshop's JWT labs.

Enable JWT on the platform release, passing the JWKS with `--set-file`:

```bash
helm upgrade agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml \
  --set security.jwt.enabled=true \
  --set security.jwt.issuer=solo.io \
  --set-file security.jwt.jwks.inline=jwks.json
```

Confirm the platform created a gateway-scoped JWT policy:

```bash
kubectl get enterpriseagentgatewaypolicy agw-platform-jwt -n agentgateway-system
```

Expected output:

```
NAME               ACCEPTED   ATTACHED   AGE
agw-platform-jwt   True       True       2s
```

Export the demo token and give the policy a few seconds to program:

```bash
export DEV_TOKEN_1="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InNvbG8tcHVibGljLWtleS0wMDEifQ.eyJpc3MiOiJzb2xvLmlvIiwib3JnIjoic29sby5pbyIsInN1YiI6InVzZXItaWQiLCJ0ZWFtIjoidGVhbS1pZCIsImV4cCI6MjA3OTU1NjEwNCwibGxtcyI6eyJvcGVuYWkiOlsiZ3B0LTRvIl19fQ.e49g9XE6yrttR9gQAPpT_qcWVKe-bO6A7yJarMDCMCh8PhYs67br00wT6v0Wt8QXMMN09dd8UUEjTunhXqdkF5oeRMXiyVjpTPY4CJeoF1LfKhgebVkJeX8kLhqBYbMXp3cxr2GAmc3gkNfS2XnL2j-bowtVzwNqVI5D8L0heCpYO96xsci37pFP8jz6r5pRNZ597AT5bnYaeu7dHO0a5VGJqiClSyX9lwgVCXaK03zD1EthwPoq34a7MwtGy2mFS_pD1MTnPK86QfW10LCHxtahzGHSQ4jfiL-zp13s8MyDgTkbtanCk_dxURIyynwX54QJC_o5X7ooDc3dxbd8Cw"
sleep 8
```

Call team alpha's endpoint **without** a token; the gateway now rejects it:

```bash
curl -s -o /dev/null -w "no-token:   %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-alpha/chat" \
  -H "content-type: application/json" \
  -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"Whats your favorite poem?"}]}'
```

Expected output:

```
no-token:   401
```

Call it again **with** the token, and it succeeds:

```bash
curl -s -o /dev/null -w "with-token: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-alpha/chat" \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $DEV_TOKEN_1" \
  -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"Whats your favorite poem?"}]}'
```

Expected output:

```
with-token: 200
```

Team beta is protected too, even though its release was never touched:

```bash
curl -s -o /dev/null -w "beta no-token: %{http_code}\n" "http://${GATEWAY_IP}:8080/teams/team-beta/chat" \
  -H "content-type: application/json" \
  -d '{"model":"mock-gpt-4o","messages":[{"role":"user","content":"Whats your favorite poem?"}]}'
```

Expected output:

```
beta no-token: 401
```

Turning JWT back off is symmetric: remove the `--set` flags and upgrade with the base values file:

```bash
helm upgrade agw-platform charts/agentgateway-platform \
  -n agentgateway-system \
  --values platform-values.yaml
```

Verify the JWT policy is gone:

```bash
kubectl get enterpriseagentgatewaypolicy agw-platform-jwt -n agentgateway-system
```

Expected output:

```
Error from server (NotFound): enterpriseagentgatewaypolicies.enterpriseagentgateway.solo.io "agw-platform-jwt" not found
```

One platform-side change protected every team's endpoint, then unprotected them, and no developer was involved.

---

## Observability

Access logging is on because the platform chart enabled it (`observability.accessLog`), so every team request is logged from the shared gateway with the platform-defined attributes. View the proxy's logs:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agw-platform --prefix --tail 20
```

Each LLM request shows its route, status, token usage, and the platform-added `llm.streaming` attribute, for example:

```
...route=team-beta/team-beta-chat ... http.status=200 protocol=llm gen_ai.usage.input_tokens=10 gen_ai.usage.output_tokens=40 llm.streaming=false
...route=team-beta/team-beta-chat ... http.status=429 protocol=llm error="rate limit exceeded" reason=RateLimit
...route=team-alpha/team-alpha-chat ... http.status=401 protocol=http error="authentication failure: no bearer token found" reason=JwtAuth
```

To enable Prometheus scraping, the platform team sets `observability.metrics.enabled=true` (requires the prometheus-operator `PodMonitor` CRD), which renders a `PodMonitor` for the proxy. For dashboards and traces, use the Grafana stack from `002`.

---

## Cleanup

Uninstall the two developer releases, then the platform release, then delete the team namespaces (the `001` `agentgateway-proxy` gateway is untouched):

```bash
helm uninstall team-alpha -n team-alpha
helm uninstall team-beta -n team-beta
helm uninstall agw-platform -n agentgateway-system
kubectl delete namespace team-alpha team-beta --ignore-not-found
```

Remove the local files you created:

```bash
rm -f platform-values.yaml team-alpha-values.yaml team-beta-values.yaml team-alpha-cheat.yaml jwks.json
```

Confirm the platform gateway is gone and the `001` gateway survives:

```bash
kubectl get gateway agw-platform -n agentgateway-system
kubectl get gateway agentgateway-proxy -n agentgateway-system
```

Expected output:

```
Error from server (NotFound): gateways.gateway.networking.k8s.io "agw-platform" not found
NAME                 CLASS                     ADDRESS          PROGRAMMED   AGE
agentgateway-proxy   enterprise-agentgateway   172.18.255.254   True         28h
```
