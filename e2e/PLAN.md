# E2E test suite plan

Goal: every lab in this repo has an automated test that fails when the lab stops
working. The labs ship to customers, so the failure mode that matters is **doc
drift** — a version bump changes a CRD field, a chart default, or a response
shape, and the lab silently breaks while a hand-written test keeps passing.

## The core decision: the lab markdown *is* the test

The harness parses a lab `.md`, lifts its ```bash blocks, and executes them in
one shell — the same commands, in the same order, that a reader copy-pastes. A
sidecar spec under `e2e/specs/` only decides **which sections run** and **what to
assert** about their output. Lab files are never modified.

Consequences worth stating up front:

- A lab whose YAML breaks on a new release fails its test. That is the point.
- Rename a lab heading and its spec errors with `DRIFT: ... has no section '...'`
  rather than silently skipping. `--lint` catches this with no cluster.
- Assertions live next to the lab, not inside it, so the public-facing docs stay
  clean (per the repo's house style).
- Where a lab verifies by hand (MCP Inspector, Grafana in a browser, jwt.io), the
  spec adds a `probe:` — a harness-authored curl block that drives the same
  protocol steps. Probes are the only non-doc commands in the suite.

### Why not the alternatives

| Approach | Rejected because |
|---|---|
| Hand-written `test.sh` per lab (the field-installer shape) | Duplicates every manifest. The lab and its test drift apart independently, so a broken lab can still show green — the exact failure this suite exists to catch. |
| Directives inline in the lab markdown | Most robust to reordering, but injects test scaffolding into 82 customer-facing files. |

## Status

Harness complete and verified live on `cluster1` (v2026.7.0).
**Phases 1 and 2 done bar the 6 expensive t0 labs. Phase 3 in progress: 14 of 29
t1-key labs implemented.**

Current: **400 assertions passing, 0 failing** in a full `run-e2e.sh` (774s)
across 32 specs, plus `--install-base` (20 assertions) which installs and
verifies 001 and 002 by replaying their own markdown.

| Spec | Assertions | Shape it proves |
|---|---|---|
| `_base/001-install-enterprise-agentgateway` | 14 | Multi-block install, `wait:`, Gateway Programmed, extension services |
| `_base/002-set-up-ui-and-monitoring-tools` | 6 | Helm + Grafana/Prometheus/Solo UI probes; foreground `port-forward` replaced |
| `routing/direct-response` | 3 | Smallest possible lab; negative probe |
| `routing/configure-mock-openai-server` | 7 | Deploy + wait + LLM call + metrics + access logs |
| `routing/configure-routing-openai` | 7 | Credential resolution against a real provider |
| `routing/timeouts-and-retries` | 13 | Temporary baseline mutation restored by cleanup; logfmt access-log assertions |
| `security/sni-matching` | 13 | TLS/SNI with cert-subject assertions; expected-failure block via `allow_failure` |
| `mcp/in-cluster-mcp` | 17 | static→dynamic backend, session stickiness, JWT Strict, claim RBAC, tool RBAC — a lab that is ~80% MCP Inspector, driven entirely over curl |
| `mcp/mcp-tool-rate-limiting` | 11 | Per-tool `entRateLimit` 429s, with burst retry around the fixed-minute window |

Non-running specs (present so they appear in `--list` and the summary):
`mcp/mcp-eager-auth-keycloak` and `security/sni-matching`'s former exclusion are
resolved; `installation/image-list` is `t5-manual`.

### Phase 2 additions (verified live)

| Spec | Assertions | Notes |
|---|---|---|
| `routing/timeouts-and-retries` | 13 | logfmt retry assertions; temporary baseline mutation restored |
| `security/sni-matching` | 13 | promoted from `excluded` after fixing the lab |
| `mcp/mcp-tool-rate-limiting` | 11 | per-tool 429 with burst retry around the fixed-minute window |
| `mcp/mcp-byo-grpc-ext-authz` | 11 | route-scoped ext-authz + a probe proving other paths stay ungated |
| `mcp/remote-mcp` | 16 | external TLS MCP server; `claude mcp add` deliberately never run |
| `mcp/openapi-to-mcp-in-cluster` | 12 | OpenAPI→MCP tool synthesis against a Stripe mock |
| `mcp/openapi-to-mcp-external-api` | 10 | same against the public Open-Meteo API |
| `mcp/mcp-tool-mode-search` | 20 | 71.4% catalog reduction on 12 tools, 99.8% on the 50-tool stub |
| `mcp/mcp-tool-mode-code` | 18 | sandboxed composition, 60s timeout abort, typed-API RBAC filtering |
| `mcp/mcp-tool-federation` | 30 | four backends multiplexed, FailOpen, four personas |
| `observability/production-observability-alerting-and-scaling` | 9 | applies the documented PDB snippet, proves the operator manages it, reverts |
| `installation/system-requirements` | 6 | probe-only: parses the lab's own support matrix and checks the live cluster |

### Phase 3 additions (verified live)

| Spec | Assertions | Notes |
|---|---|---|
| `guardrails/builtin-guardrails` | 31 | 8 request guards by status *and* message, 2 response guards by mask token; a probe proving blocked traffic is never billed |
| `routing/configure-openai-embeddings` | 31 | `Completions` vs `Passthrough` telemetry asserted per log line; path rewrites still resolve to the priced route |
| `routing/routing-match-types` | 27 | path/header/query match types; negative probes proving each is restrictive, not a passthrough |
| `security/virtual-keys` | 18 | per-user, tiered and per-tenant token budgets; opens by resetting `ext-cache` so it is re-runnable |
| `observability/llm-cost-management` | 16 | catalog overlay reaches the proxy (nonzero `agw.ai.usage.cost.total`), budget CRD compiles to a managed `RateLimitConfig`, Block vs Audit |
| `transformations/transformations` | 12 | CEL response headers; asserts the served model is the dated build, not the alias |
| `routing/openai-streaming` | 11 | SSE chunk shape including the gateway-injected usage chunk |
| `transformations/prompt-enrichment` | 8 | prepended system message proven twice: forwarded prompt in the log, and more input tokens billed for a byte-identical body |
| `rate-limiting/local-token-rate-limiting` | 6 | per-replica counter; burst sized for both replicas |
| `routing/openai-audio` | 17 | TTS → Whisper round trip; `cd $E2E_WORKDIR` keeps generated audio out of the repo |
| `routing/configure-body-based-routing` | 13 | `PreRouting` CEL lifts `model` out of the body; each of the three rules proven by which backend answered |
| `guardrails/external-moderation-guardrails` | 7 | OpenAI moderation endpoint as the classifier, plus a benign prompt that must still pass |
| `security/frontend-mtls` | 17 | `AllowValidOnly` refuses at the handshake; a probe adds the rogue-CA case the lab never tests |
| `security/tls-termination` | 15 | HTTPS listener appended by JSON patch; asserts HTTP/2 via ALPN and that :8080 keeps serving |
| `guardrails/advanced-guardrails-webhook` | 32 | LLM-classifier webhook: reject, mask, context-aware pass, and a ConfigMap edit flipping the same prompt from allowed to rejected |
| `security/opa-authorization` | 23 | one Rego `AuthConfig` shared by an LLM route and an MCP route; probe proves the sharing rather than assuming it |
| `security/byo-opa-grpc-ext-authz` | 20 | BYO OPA over gRPC across three parts, ending with a body-aware per-tool deny |
| `security/llm-byo-grpc-ext-authz` | 16 | route-scoped ext-authz; probe proves a sibling route on the same gateway stays ungated |
| `evaluations/evaluate-openai-model-performance` | 15 | promptfoo matrix routed through `OPENAI_BASE_URL`; asserts `0 errors` so a broken provider entry cannot hide behind judged failures |
| `routing/llm-failover` | 11 | 429 evicts priority group 1; asserts the health policy's CEL is what did it |
| `rate-limiting/global-rate-limiting` | 29 | all seven descriptor shapes: REQUEST vs TOKEN, header, multi-header, JWT claim, tier, IP, mixed windows |
| `routing/llm-failover-advanced` | 24 | intra-group ejection, 5XX eviction, and P2C + per-provider eviction + inter-group failover |
| `security/WAF` | 23 | ModSecurity rules across request and response phases, layered with promptGuard |
| `agent-frameworks/langchain-with-agentgateway` | 16 | proves the SDK dialled the gateway, and that both chains' calls were proxied |
| `agent-frameworks/crewai-with-agentgateway` | 14 | same, plus a probe guarding the `tracing=False` line that keeps prompts off app.crewai.com |

### Remaining phase 2 (6 labs)

`mcp/composable-mcp` (29 bash blocks, ~1100 lines) ·
`identity-delegation/obo-token-exchange-fundamentals` (33 blocks, in-cluster
Keycloak) · `platform-engineering/platform-and-developer-helm-charts-mcp`
(36 blocks) · `inference/configure-inference-routing-with-vllm` (downloads
Qwen2.5-0.5B, may not be viable on kind) · `load-testing/mcp-load-testing-k6` ·
`load-testing/llm-load-testing-k6`

`routing/routing-match-types` was listed t0 in the table below but every curl in
it authenticates to the real OpenAI API, so it is now `t1-key`. With
`mcp/mcp-tool-federation` (FRED) also t1-key, `mcp/mcp-eager-auth-keycloak`
excluded and `installation/image-list` t5-manual, the live tiers are t0 20 and
t1-key 29 — see `HANDOFF.md` §4 for the reconciled table. `--list` is the
authority; the per-lab table below is the original classification.

### Shared helpers earned along the way

MCP labs kept re-implementing the same protocol plumbing, so `lib.sh` now exports
`e2e_mcp_init` / `e2e_mcp_rpc` / `e2e_mcp_tool` / `e2e_mcp_tools` /
`e2e_mcp_status` / `e2e_mcp_expect`, plus `e2e_pf` / `e2e_pf_stop` for
port-forwards. Migrating `in-cluster-mcp` onto them cut its spec down and took
its runtime from 77s to 51s, because `e2e_mcp_expect` polls instead of sleeping.

### Baseline integrity guard

The runner fingerprints the shared Gateway (`listeners[*].port` +
`infrastructure.parametersRef.name`) before and after every lab. A lab that
damages the baseline is named immediately and the Gateway is restored so the run
continues, instead of the damage surfacing as an unrelated failure several labs
later. This exists because a full `kubectl apply` of the shared Gateway silently
drops `parametersRef`, which detaches the params and freezes proxy replicas —
exactly the bug `sni-matching` had.

## Architecture

```
e2e/
  run-e2e.sh          runner: discovery, tiering, credentials, timeouts, summary
  lib/
    labdoc.py         markdown parser + spec resolver -> emits a bash driver
    lib.sh            driver runtime: assertions, port-forward helpers, cleanup
    creds.sh          env -> shell rc -> prompt -> skip
  specs/
    _base/            001 and 002, run by --install-base
    <mirrors labs/>   one .yaml per lab
  .work/              per-run block scripts + captured output (gitignored)
  .env.local          prompted keys, chmod 600 (gitignored)
```

**Execution model.** `labdoc.py` writes each selected block to its own file and
generates a driver that `source`s them **in one shell**. Sharing the shell is
what makes replay work: labs `export GATEWAY_IP` in one block and use it three
blocks later, and MCP labs thread `SESSION`/`MCP_SESSION_ID` the same way.
Assertions run inline, so output is checked while its variables are still live.
Cleanup is an `EXIT` trap, so a failing lab still restores the baseline.

**Deliberately no `set -e`, `-u`, or `-o pipefail`** in the driver. Lab blocks are
written for an interactive shell. `pipefail` in particular breaks `cmd | grep -q`:
grep exits on first match, the producer takes SIGPIPE, and the pipeline reports
failure despite matching. It only bites on large outputs (a 128KB `/metrics`
dump), so it reads as a flake. This cost two real debugging cycles while building
the slice — see `lib.sh` for the note.

## Spec format

```yaml
lab: labs/routing/direct-response.md
description: directResponse policy returns a fixed body without a backend
tier: t0
requires: []          # env vars; the lab skips if any is unresolvable
timeout: 180

steps:
  - section: "Create HTTPRoute and Direct Response Policy"
    run: all                      # all | none | [1,3] (1-based, within section)
    allow_failure: false          # tolerate a nonzero exit
    wait: "--for=condition=Available deploy/x -n ns --timeout=300s"

  - section: "curl our agentgateway endpoint"
    retry: {attempts: 8, delay: 3, until: "HTTP/[0-9.]+ 200"}
    assert:
      - status: 200
      - contains: "Status: Healthy"

  - probe: "unmatched path is not served by this route"   # harness-authored
    script: |
      curl -s -o /dev/null -w '%{http_code}' "http://$GATEWAY_IP:8080/not-health"
    assert:
      - contains: "404"

  - section: "Observability"
    run: none
    reason: browser step          # surfaced as SKIP, never silently dropped

cleanup:
  section: "Cleanup"              # the lab's own Cleanup section
```

Assertions: `status`, `contains`, `not_contains`, `matches`, `not_matches`, `rc`.
`retry` exists because route and policy programming is asynchronous — a pod can
be Ready before the proxy's endpoint table refreshes.

Two hazards every spec author hits, both already handled in the slice:
- **Expected-output blocks are fenced ```bash** in many labs, so `run: all` would
  execute a Kubernetes table as a command. Use explicit `run:` ordinals.
- **Foreground `port-forward` blocks never return.** Use `run: none` plus a probe
  built on the `e2e_pf` / `e2e_pf_stop` helpers.

## Credentials

Per your call, no mock substitution — labs run against their real provider or
they skip. Resolution order per variable, in `creds.sh`:

1. exported in the environment
2. `e2e/.env.local` (cached from an earlier prompt, `chmod 600`, gitignored)
3. `~/.zshrc`, `~/.bashrc`, `~/.zprofile`, `~/.bash_profile`, `~/.env` — only the
   matching `export VAR=` line is evaluated in a subshell, never the whole rc file
4. interactive prompt (TTY only, silent input), cached for later runs
5. unresolved → the lab is reported `SKIP — missing: VAR`, never a silent pass

The runner resolves every key the run needs **up front**, so prompts arrive in one
batch rather than interrupting a 40-minute run. `--no-prompt` makes it CI-safe.

Already present in your `~/.zshrc`: `OPENAI_API_KEY`, `SOLO_TRIAL_LICENSE_KEY`,
all six `AUTH0_*`, all seven `OKTA_*`, four `ENTRA_*`, `FIGMA_*`, `GEMINI_API_KEY`,
`NVIDIA_API_KEY`. Missing for full coverage: `CLAUDE_API_KEY`, `AWS_*`,
`AZURE_OPENAI_API_KEY`, `BEDROCK_API_KEY`, GCP/Vertex credentials, plus the
`ENTRA_CLIENT_ID`/`ENTRA_CLIENT_SECRET`/`ENTRA_API_SCOPE` set the Entra labs want.

## Tiers

| Tier | Meaning | Labs |
|---|---|---|
| `t0` | Self-contained: single cluster, 001+002 baseline, no credentials | 24 |
| `t1-key` | Needs an LLM provider key | 27 |
| `t2-idp` | Needs external IdP (Auth0 / Okta / Entra) | 7 |
| `t3-cloud` | Needs AWS / Azure / GCP, incl. IRSA + workload identity | 9 |
| `t4-infra` | Needs OpenShift, multi-cluster, or an air-gap registry | 5 |
| `t5-manual` | Interactive or reference-only; `--lint` coverage only | 6 |
| `excluded` | Destructive to the baseline — excluded per your call | 4 |

`t5-manual` and `excluded` labs still get a spec so they appear in `--list` and
the summary's `excluded` count. Coverage gaps are always printed, never implied.

## Per-lab classification (all 82)

### t0 — no credentials (24)

| Lab | Notes |
|---|---|
| `routing/direct-response` | ✅ implemented |
| `routing/configure-mock-openai-server` | ✅ implemented |
| `routing/timeouts-and-retries` | vLLM sim; slow-streaming sim flags |
| `routing/routing-match-types` | reclassify to t0 if the mock backend suffices |
| `security/sni-matching` | self-signed certs in-lab |
| `mcp/in-cluster-mcp` | ✅ implemented |
| `mcp/composable-mcp` | uses `lib/jwt` local signing keys |
| `mcp/mcp-tool-federation` | uses `lib/jwt` |
| `mcp/mcp-tool-mode-search` | threads `UPSTREAM_SID`/`SESSION` across blocks |
| `mcp/mcp-tool-mode-code` | same |
| `mcp/mcp-tool-rate-limiting` | needs ~10s settle after RateLimitConfig |
| `mcp/mcp-byo-grpc-ext-authz` | in-cluster gRPC authz server |
| `mcp/openapi-to-mcp-in-cluster` | |
| `mcp/openapi-to-mcp-external-api` | needs egress to a public API |
| `mcp/remote-mcp` | needs egress |
| `mcp/mcp-eager-auth-keycloak` | Keycloak is in-cluster (`lib/keycloak`) — no SaaS |
| `identity-delegation/obo-token-exchange-fundamentals` | in-cluster Keycloak + vLLM sim; 33 blocks |
| `inference/configure-inference-routing-with-vllm` | installs the IGW chart |
| `observability/production-observability-alerting-and-scaling` | asserts on Prometheus rules |
| `platform-engineering/platform-and-developer-helm-charts-mcp` | 36 blocks; local charts |
| `load-testing/mcp-load-testing-k6` | k6 present in the test environment |
| `load-testing/llm-load-testing-k6` | k6 + vLLM sim |
| `installation/system-requirements` | assert-only: version/CRD preconditions |
| `installation/image-list` | assert-only: every listed image tag resolves |

### t1-key — LLM provider key (27)

`OPENAI_API_KEY` unless noted.

`routing/configure-routing-openai` ✅ · `routing/configure-openai-embeddings` ·
`routing/openai-streaming` · `routing/openai-audio` ·
`routing/configure-openai-batches` · `routing/configure-body-based-routing` ·
`routing/llm-failover` · `routing/llm-failover-advanced` ·
`routing/configure-routing-anthropic` (`CLAUDE_API_KEY`, absent today) ·
`guardrails/builtin-guardrails` · `guardrails/advanced-guardrails-webhook` ·
`guardrails/external-moderation-guardrails` (real OpenAI moderation endpoint) ·
`transformations/transformations` · `transformations/prompt-enrichment` ·
`rate-limiting/local-token-rate-limiting` · `rate-limiting/global-rate-limiting`
(also `lib/jwt`; 48 blocks — the largest lab) · `security/virtual-keys` ·
`security/WAF` · `security/tls-termination` · `security/frontend-mtls` ·
`security/opa-authorization` · `security/byo-opa-grpc-ext-authz` ·
`security/llm-byo-grpc-ext-authz` · `observability/llm-cost-management` ·
`agent-frameworks/langchain-with-agentgateway` ·
`agent-frameworks/crewai-with-agentgateway`

Notes carried over from prior work on this repo:
- `evaluations/evaluate-openai-model-performance` and
  `platform-engineering/centralized-llm-ops-helm-chart` are also t1-key. The
  latter's Step 6 is known broken and deferred — its spec should assert Steps 1–5
  and 7 and mark Step 6 `run: none` with the reason, so the gap stays visible.
- CrewAI 1.x POSTs first-run prompts to `app.crewai.com` unless the code passes
  `Crew(tracing=False)`. The spec must not silently ship prompts off-box.
- `gpt-5.6-terra` rejects `temperature: 0`; `gpt-5.4-nano`/`mini` return dated
  response IDs. The OpenAI spec asserts on the ID shape to prove the request
  actually reached the provider.

### t2-idp — external IdP (7)

`mcp/mcp-eager-auth-auth0` · `mcp/mcp-eager-auth-auth0-pre-issuance-authz` ·
`mcp/mcp-eager-auth-okta` · `security/jwt-auth-through-corporate-proxy-okta` ·
`security/jwt-auth-through-corporate-proxy-entra` · `security/jwt-auth-with-rbac` ·
`identity-delegation/msft-entra-obo`

Auth0 and Okta client credentials are already in your shell. Two known blockers:
the Okta app cannot mint `client_credentials` tokens, so the two Okta JWT labs
need a supplied `VALID_TOKEN`; and the Auth0 eager-OAuth issuer only binds :7777
when `tokenExchange.enabled=true`. Both should be encoded as `requires:` entries
so they skip cleanly rather than fail confusingly.

### t3-cloud — cloud provider (9)

`routing/configure-routing-aws-bedrock` · `...-bedrock-apikey` ·
`...-bedrock-titan-embeddings` · `...-bedrock-irsa` (EKS) ·
`routing/configure-routing-azure-openai` · `...-azure-openai-workload-identity`
(AKS) · `routing/configure-routing-vertexai` · `...-vertexai-service-account` ·
`agent-harnesses/claude-code` (`CLAUDE_API_KEY` / OAuth token)

The three identity-federation labs (IRSA, AKS workload identity, Vertex SA) can
never run on kind. Give them specs that validate manifest rendering and CRD
acceptance only, and say so in the `reason:`.

### t4-infra — special infrastructure (5)

`installation/openshift/001-set-up-enterprise-agentgateway-ocp` ·
`installation/openshift/002-set-up-monitoring-tools-ocp` ·
`installation/airgap/001-airgap` · `upgrades/multi-cluster-upgrades` ·
`platform-engineering/networking-architecture` (reference; lint-only)

### t5-manual — interactive / reference (6)

`agent-harnesses/claude-desktop` · `agent-harnesses/claude-desktop-sso-entra` ·
`mcp/figma-mcp-auth0/README` · `mcp/figma-mcp-entra/README` ·
`mcp/obo-crewai-agent-with-mcp` (Keycloak + OpenAI + agent loop; promote to
t1-key if worth the runtime) · `guardrails` browser-verified subsections

The Figma labs need a browser OAuth consent round trip. Both were validated by
hand previously; `--lint` keeps their structure honest.

### excluded — destructive to the baseline (4)

Per your call these are excluded rather than sequenced with a baseline reset:

`upgrades/in-place-rolling-upgrades` · `upgrades/blue-green-namespaces` ·
`upgrades/migrate-v2026.5.x-to-v2026.7.x` ·
`platform-engineering/centralized-llm-ops-helm-chart` (reinstalls the gateway via
the LLM-ops chart; if you want it covered, it belongs in a dedicated run after
`--install-base` on a throwaway cluster)

Each still gets a spec with `tier: excluded` and a `reason:`, so `--list` shows
them and the summary counts them. `--install-base` makes the throwaway-cluster
path cheap if you later want them: `vind-up && ./e2e/run-e2e.sh --install-base`.

## Rollout

| Phase | Scope | Outcome |
|---|---|---|
| 1 ✅ | Harness + `--install-base` + 4 labs | Done, 34+20 assertions green |
| 2 | Remaining 20 t0 labs | Credential-free CI gate; the MCP tool-mode and Keycloak labs are the long poles |
| 3 | 27 t1-key labs | Full coverage with `OPENAI_API_KEY` alone (except the Anthropic lab) |
| 4 | 7 t2-idp labs | Needs the Okta `VALID_TOKEN` and Entra client secret questions resolved |
| 5 | t3/t4/t5 specs as manifest-validation or lint-only | Every workshop lab appears in `--list` with an explicit tier and reason |

Suggested order within a phase: cheapest-to-verify first, so a regression in the
shared baseline surfaces before a 15-minute lab burns time.

## Running it

```bash
./e2e/run-e2e.sh --lint                      # no cluster: specs vs labs, uncovered labs
./e2e/run-e2e.sh --list                      # coverage table with tiers
./e2e/run-e2e.sh --install-base --only-base  # stand up 001 + 002 on a fresh cluster
./e2e/run-e2e.sh --tier t0                   # credential-free labs
./e2e/run-e2e.sh mcp/in-cluster-mcp -v       # one lab, streaming block output
./e2e/run-e2e.sh --no-prompt                 # CI: never prompt, skip on missing keys
```

Requires bash ≥ 4 (macOS `/bin/bash` is 3.2 — the shebang picks up homebrew
bash), `python3` with PyYAML, `kubectl`, `helm`, `jq`, and `k6` for the
load-testing labs. macOS ships no `timeout`/`gtimeout`, so the
runner implements its own watchdog and runs each driver in its own process group
so leaked `port-forward` children die with it.

## Lab bugs found and fixed

Policy: fix the lab, add a regression assertion, keep the note here. All of these
were found by running the labs, and all are fixed in `labs/` (uncommitted).

1. **`security/sni-matching` destroyed the baseline.** It applied a Gateway named
   `agentgateway-proxy` — the same name as 001's — with only HTTPS:443 listeners
   and no `parametersRef`, then its Cleanup deleted a Gateway named
   `agentgateway` (wrong name), so nothing was restored. Now creates a separate
   `agentgateway-sni` Gateway, with a note explaining why, and Cleanup deletes the
   right object. This moved the lab from `excluded` to a passing t0 lab, and the
   spec carries a regression probe asserting the 001 Gateway is untouched.
2. **`mcp/in-cluster-mcp` and `mcp/mcp-tool-rate-limiting` were not re-runnable.**
   Both `kubectl create namespace mcp` but neither Cleanup deleted it, so a second
   run failed `AlreadyExists`. Both Cleanups now delete the namespace; verified by
   running `in-cluster-mcp` twice back to back.
3. **`routing/timeouts-and-retries` documented the wrong log format.** It showed
   the access log as JSON (`{"retry": 3, ...}`); the gateway emits logfmt
   (`http.status=504 retry.attempt=3 error="request timeout" reason=Timeout
   duration=102ms`). A reader grepping for `"retry"` finds nothing. Fixed to the
   real format. The lab's *numbers* were right and are now asserted: 3 retries in
   the 100ms budget, 7 in the 2s budget.
4. **`mcp/mcp-tool-rate-limiting` claimed a limit that does not exist.** It said
   `echo` "has its own independent counter (10/min)", but the `RateLimitConfig`
   defines only `get-env: 3/min`; `echo` matches no descriptor and is never
   counted. Reworded, and the spec asserts both halves (429 for `get-env`, never
   429 for `echo`).
5. **The `/metrics` one-liner port-forwards to one of two replicas.** 001 sets
   `replicas: 2`, and the one-liner targets `deployment/agentgateway-proxy`, so it
   scrapes whichever pod the selector picks.

   Measured on this cluster: after **one** request the counter is present on one
   pod and **absent on the other** (1 vs 0); after ~31 requests both have it
   (18 vs 14). So the gap is real but only for the first request or two — which is
   exactly where the labs sit, since the metrics block follows a single example
   `curl`. Roughly a coin flip.

   That only *matters* where the lab filters for a specific metric. In the 8 labs
   whose block ends in `| grep -E 'agentgateway_mcp_requests_total|…'`, scraping
   the wrong pod returns nothing at all, and the prose then promises metrics the
   reader cannot see — a confusing dead end. Those 8 now loop over every proxy pod.
   The other 16 simply `curl` the whole endpoint: the reader gets ~128KB always
   containing `agentgateway_*` families, nobody hand-scans it for one counter, and
   the prose only claims the endpoint is scrapeable, which is always true. Those 16
   were left untouched.

   Worth recording how this was found: a metrics assertion failure was initially
   blamed on this replica split, and a caveat was added to all 24 labs on that
   inference. The real cause was the SIGPIPE/`pipefail` bug below. The replica
   behaviour was only measured afterwards, which is what narrowed the fix from 24
   labs to 8. Verify before editing 24 public files, not after.
6. **15 expected-output blocks were fenced as ```bash** across 001, 002, the
   air-gap lab, both OpenShift labs, and `WAF.md`. They rendered with bash syntax
   highlighting despite being kubectl tables, and forced every spec to list block
   ordinals to avoid executing a table as a command. Converted to plain fences;
   the base specs are now `run: all`. `WAF.md`'s curl blocks that merely *start*
   with a `#` comment were deliberately left alone.

7. **Both tool-mode labs' verification commands could never work.** Eight curl
   pipelines ended in `| python3 -m json.tool`, but the gateway answers MCP over
   `text/event-stream`, so the body arrives as `data: {...}`. Every one of them
   failed with `Expecting value: line 1 column 1 (char 0)` — a reader following
   the lab got an error instead of the pretty-printed JSON it describes. Fixed by
   inserting `sed 's/^data: //'` before the parse. The three non-MCP labs that
   pipe genuine JSON into `json.tool` were left alone.
8. **`mcp/mcp-tool-federation`'s JWT policy was invalid YAML.** The heredoc line
   carried 16 literal spaces *and* `sed 's/^/                /'` prepended 16
   more, so the block scalar's first line was indented 32 while the rest were 16.
   `kubectl` rejected it with `line 18: did not find expected key`, meaning Step 6
   onward could not be completed at all. Fixed so `sed` supplies all the
   indentation; verified the result parses and yields the right JWKS `kid`.
9. **`mcp/mcp-tool-federation` leaked its namespace.** It created `mcp` but never
   deleted it. Cleanup now does, and its bare `kubectl create namespace` was
   switched to the idempotent `--dry-run=client | apply` form the rest of the repo
   already uses (also applied to `in-cluster-mcp` and `mcp-tool-rate-limiting`).

10. **`routing/openai-streaming` documented the opposite of the truth.** Its Note
    told the reader streaming responses carry no `usage` object. The gateway sets
    `stream_options.include_usage` so it can record token metrics, and forwards
    the resulting final chunk — confirmed by calling OpenAI directly with the
    same body, which returns no such chunk. Rewrote the Note, added the chunk to
    the expected output, and asserted its exact shape.
11. **`routing/configure-openai-embeddings` showed JSON access logs and a claim
    that never held.** Two ~65-line expected-output blocks were pretty-printed
    JSON carrying `request.body`/`response.body`/`rq.headers.*`; the gateway
    emits logfmt (same class of bug as #3). The prose also said
    `gen_ai.operation.name` switches to `embeddings` for the embeddings endpoint
    — that line has no `gen_ai.*` fields at all, because the route is
    `Passthrough`. Replaced with real captured output plus the actual
    `Completions` vs `Passthrough` telemetry distinction.
12. **`rate-limiting/local-token-rate-limiting` promised a coin flip.** Local
    limits are per-replica counters and `001` runs two replicas, so "you should
    be rate limited on the second request" only held when both requests hit the
    same pod. Same shape as #5: a per-replica behaviour documented as if there
    were one proxy. Now a 6-request burst with the counter model explained.
13. **Duplicate section headings in three labs.** `configure-openai-embeddings`
    (`View access logs` / `View Access Logs`), `prompt-enrichment` and
    `local-token-rate-limiting` (two `curl openai` each). Heading matching is
    case-insensitive, so neither copy is addressable and the parser raises
    `has 2 sections named`. This is the drift detector doing its job on a real
    documentation defect, not a harness limitation — renamed the specific one in
    each pair.
14. **`transformations/transformations` had a no-op rename artifact.** "this may
    differ from the requested model, e.g. `gpt-5.4-nano` → `gpt-5.4-nano`", left
    over from 0.13.1. Fixed to the dated ID its own expected output showed.

15. **`security/tls-termination` destroyed the baseline, then mis-restored it.**
    Exactly bug #1 again, in a second lab: it applied a full Gateway manifest
    named `agentgateway-proxy` carrying only an HTTPS `443` listener, dropping
    the HTTP `8080` listener every other lab routes through. Its "Restore the
    default Gateway from lab 001" step then re-applied the Gateway **without
    `infrastructure.parametersRef`**, so a reader who followed the lab to the end
    was left with the baseline permanently detached from `agentgateway-config` —
    frozen proxy replicas, and no logging, model catalog, or STS config. Now a
    JSON patch appends the listener and Cleanup removes it, so nothing 001 set is
    ever restated.
16. **`security/frontend-mtls` had the identical bug**, plus `spec.tls.frontend`.
    Same fix: one JSON patch adding `/spec/tls` and appending the listener, and a
    Cleanup patch removing both.

    **Why patch the shared Gateway rather than use a separate one** (the
    `sni-matching` fix in #1): a second Gateway gets its own proxy Deployment,
    whose pods are labelled `app.kubernetes.io/name=<gateway-name>` — *not*
    `agentgateway-proxy`. Both labs' Observability sections, and every other
    lab's, select on `-l app.kubernetes.io/name=agentgateway-proxy`, so a
    dedicated Gateway would have left the reader's "View Access Logs" step
    showing nothing about the HTTPS request they just made. Measured on a live
    second Gateway before choosing. `sni-matching` keeps its dedicated Gateway
    because SNI checks resolve against a distinct LoadBalancer address and it has
    no access-log step to break.

17. **`evaluations/evaluate-openai-model-performance` could not work at all.** The
    0.11.4 reorg moved the promptfoo configs to `labs/evaluations/`, but the
    lab's two `promptfoo eval -c evaluations/...` commands — the entire point of
    the lab — still pointed at the old root-level path, as did its "other
    evaluation examples in `/evaluations`" pointer. That commit's changelog says
    it updated relative links; these were command arguments, not markdown links,
    so they were missed. Paths corrected, and the spec asserts the configs exist
    at the paths the lab names.
18. **All four promptfoo eval configs errored on `gpt-5.6-terra`.** The provider
    id `openai:gpt-5.6-terra` (no `:chat:`) makes promptfoo use the **Responses**
    API, so the request reaches the gateway's `Completions` route without a
    `messages` field and the gateway answers `503 processing failed: failed to
    parse request: missing field \`messages\``. Every one of that provider's tests
    errored — 5 of 30 in the coding eval — while the sibling entries already
    pinned `openai:chat:`. Now pinned in all four configs; the spec asserts
    `0 errors`, so a provider that cannot be called at all is a failure rather
    than something hidden among judged test failures.
19. **`security/llm-byo-grpc-ext-authz` promised a header that never appears.** It
    told the reader an allowed request comes back "along with the
    `x-ext-authz-check-result: allowed` header injected by the ext-authz server".
    Allowed responses carry no such header: on a deny the ext-authz server
    generates the whole response (which is why the `denied` variant *is* visible),
    but on an allow the gateway forwards the original request and returns the
    provider's own response. Reworded to point at the server's decision log, which
    is where the allow verdict actually is.
20. **`security/byo-opa-grpc-ext-authz` rendered OPA's log field with a space.**
    Three places said to look for `"result": false`; OPA's decision log is compact
    JSON and emits `"result":false`. A reader grepping the documented string finds
    nothing. Same class as #3 and #11 — a log format written the way a human would
    pretty-print it rather than the way the process emits it.
21. **`routing/llm-failover` documented a three-request pattern with one curl.**
    The prose promises "Request 1: 429 ... Request 2: 200 ... Request 3: 200" but
    gave a single `curl -v`, leaving the reader to run it three times and mentally
    diff. Replaced with the repo's `for i in 1 2 3` burst form. Its Cleanup also
    restored `replicas: 2` and issued a `rollout restart` without waiting, so the
    next lab could start against churning pods — added the matching
    `rollout status` and a ready-replica check.
22. **`routing/openai-video` removed entirely.** The lab's own note said the Sora
    API is deprecated and shuts down 2026-09-24, so it was withdrawn rather than
    covered: the lab file, its README bullet, and its `llm-track.md` row are gone,
    and the track's Use Case 7 blurb no longer claims video coverage. The
    historical CHANGELOG entries that introduced it were left as the record they
    are.

23. **`security/WAF`'s defense-in-depth section could not be applied.** Use Case D
    used an unquoted `<<EOF` heredoc while its `promptGuard` regex contained
    `\\s`. The shell collapses `\\` to `\`, so kubectl received `"...\s+..."` —
    an invalid escape in a YAML double-quoted scalar — and failed with
    `error converting YAML to JSON: found unknown escape character`. The whole
    WAF-plus-guardrail layering section was unrunnable. Fixed by quoting the
    heredoc (`<<'EOF'`), which is what `builtin-guardrails` already does for its
    regex-heavy policy. Worth knowing generally: **any lab heredoc containing a
    backslash escape must be quoted.**
24. **`routing/llm-failover-advanced` repeated three known defects.** Patterns 1
    and 2 each documented a three-request failover sequence but gave a single
    `curl` (as `llm-failover` did, #21), and each showed its "Expected log output"
    as pretty-printed JSON when the gateway emits logfmt (as #3 and #11 did).
    Notably **Pattern 3 in the same lab was already correct** — a loop and real
    logfmt — so patterns 1 and 2 were brought in line with their own neighbour.
    Its Cleanup also restarted the proxy without waiting; same fix as #21.
25. **`rate-limiting/global-rate-limiting` had three sections named `curl
    openai`.** The largest lab in the repo (48 blocks) was unaddressable by any
    spec — same defect as #13, three-way. Renamed to describe what each one does
    (`Get the gateway address and curl openai`, `curl openai until the request
    limit trips`, `curl openai until the token limit trips`). Two of them, plus
    the JWT User A step, also documented multi-request outcomes with one `curl`
    and a "repeat until you receive a 429" instruction; converted to bursts, which
    the tier, IP, and mixed-window sections of the same lab already used.

Not lab bugs — correctly handled in the harness instead: MCP Inspector and
browser verification steps, foreground `port-forward` blocks, the Keycloak lab's
OAuth consent flow, `remote-mcp`'s `claude mcp add/remove` (which would rewrite
the operator's real Claude Code config), `mcp-tool-federation`'s
`<your-fred-key>` placeholders, and 002's illustrative `GRAFANA_ADMIN_PASSWORD`
export (which would set the password to the literal `your-secure-password`).

## Order-dependent flakiness (found only by running the whole suite)

Two steps passed in isolation but failed in a full sequential run — the reason to
run the suite end to end rather than lab by lab. Both are environmental, not
logic errors, and both now retry:

- `openapi-to-mcp-external-api`'s weather call returned
  `upstream call failed: Connect: deadline has elapsed` — a cold upstream
  connection to `api.open-meteo.com` exceeding the gateway's connect deadline.
- `mcp-tool-mode-code`'s "Hit the timeout" step produced **zero bytes** and a
  nonzero exit: the 60s `codeMode.timeout` races the HTTPRoute's own request
  timeout, and when the route wins the connection is dropped instead of returning
  the MCP error result the lab documents. Worth a closer look — if the route
  timeout is genuinely shorter, the lab's expected output is only reliable by
  luck.

A third case was self-inflicted and worth recording as a lesson. Fixing the
"namespace not cleaned up" bug by adding `kubectl delete namespace mcp` to three
Cleanups looked right lab-by-lab, and each lab still passed alone. In a full
sequential run it broke the *next* lab: `mcp` is shared by six labs, so the
deletion left the namespace `Terminating` while the following lab deployed into
it, and `mcp-server-everything` never became Ready. The deletions were reverted —
the idempotent `create ... --dry-run | apply` already makes those labs
re-runnable, and a shared namespace should outlive any single lab. Lab-private
namespaces (`stripe-mcp`, `composable-mcp`) are still deleted by their own labs.

## Harness gaps found while doing phase 2

- **Indented fences were invisible.** The parser only matched fences at column 0,
  so ```bash blocks nested inside numbered lists were silently skipped. That hid
  10 blocks across three labs — including entire `Verify with curl` sections in
  both `openapi-to-mcp` labs, which are the only automated verification those labs
  have. The parser now matches indented fences and dedents the body. Silent
  under-coverage is the worst failure mode for this suite, so this was a real bug.
- **`printf | grep -q` plus `pipefail`** reported false failures on large outputs
  (SIGPIPE). `pipefail` is now off everywhere, matching a reader's shell.
- **Cleanup block numbering collided with step blocks**, overwriting captured
  output. Block files now use a monotonic counter.
- **`wait:` fired after a step's first block**, which is usually
  `kubectl create namespace`. It now fires after the last block of the step.

## Harness gaps found while doing phase 3

- **`curl`'s progress meter could swallow a status line.** The meter is written to
  stderr with `\r` and no trailing newline, so on a large or slow response the
  headers arrive glued to it: `... --:--:--     0HTTP/1.1 200 OK`.
  `assert_status` anchored on `^HTTP/`, so the same lab passed or failed by
  response size and timing — a false FAIL that reads exactly like a flake. It now
  breaks the meter off before matching, keeping the anchor. Found on the
  embeddings lab (~30KB body); it was latent for every large-response lab.
- **Counters in `ext-cache` outlive a run.** Budget state persists for the whole
  window, so a budget lab run twice inside an hour starts exhausted and its
  isolation assertions fail for the wrong reason. Specs for budget labs open with
  the lab's own documented `rollout restart ext-cache`.
- **Drivers run from the repo root, so a lab that writes files writes them
  there.** `openai-audio` is the only one today (`speech.mp3`, `speech-nova.mp3`,
  `speech.wav`). Its spec opens with a probe that `cd`s to `$E2E_WORKDIR`;
  because blocks are sourced into one shell, that carries through every later
  block and the lab's relative paths still work untouched. Reach for the same
  pattern for any future lab that produces artifacts — a stray binary in a
  customer-facing repo is exactly the kind of thing that gets committed by
  accident. (This lab's own Cleanup does `rm -f` the files, so it is belt and
  braces.)
