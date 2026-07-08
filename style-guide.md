# Lab Style Guide

This guide documents the conventions of this repo so that any author — human or agent — can create new labs that look and behave like the existing ones. It was produced by auditing the full lab corpus (~70 labs across `labs/`, the root install labs, `tracks/`, `lib/`, and the repo meta-files). Where the repo is internally inconsistent, this guide picks the dominant/best pattern and marks it **normative**.

**TL;DR:** clone the skeleton of `labs/routing/configure-routing-openai.md` for a simple lab, or `labs/mcp/mcp-eager-auth-auth0.md` / `labs/mcp/figma-mcp-auth0/` for a heavyweight runbook, then work through the [checklist](#checklist-when-adding-a-lab) at the bottom.

> **If you're a human:** don't try to follow this document by hand — its precision is aimed at agents. Your workflow is three steps: (1) clone the closest existing lab per the TL;DR above, (2) write your content and skim the [checklist](#checklist-when-adding-a-lab), (3) before committing, ask Claude to run `/lab-conform labs/<category>/<your-lab>.md` — it normalizes the mechanical conventions against this guide and flags anything that needs your judgment. You own the content; the agent owns the conformance.

---

## 1. Repo layout

| Path | Purpose |
|---|---|
| `001-install-enterprise-agentgateway.md`, `002-set-up-ui-and-monitoring-tools.md` | Foundational install labs. Top-level, everything depends on them. Only these live at the root. |
| `labs/<category>/<name>.md` | All other labs. Categories: `routing`, `security`, `rate-limiting`, `guardrails`, `transformations`, `mcp`, `inference`, `identity-delegation`, `observability`, `upgrades`, `load-testing`, `evaluations`, `agent-frameworks`, `agent-harnesses`, `installation` |
| `labs/<category>/<name>/` (subdirectory lab) | Only when the lab ships binary/large assets — screenshots, big OpenAPI specs, templated YAML bundles. The runbook is `README.md` inside the folder (see §4) |
| `lib/<concern>/` | Shared, reusable cross-lab assets (`jwt/`, `keycloak/`, `crewai/`, `langchain/`, `observability/`) — see §11 |
| `tracks/` | Curated learning paths (`llm-track.md`, `mcp-track.md`) mapping use cases → lab sequences |
| `images/` | Repo-level screenshots (Grafana dashboards, Claude Desktop), referenced with relative paths |
| `docs/superpowers/{specs,plans}/` | Design docs and implementation plans for substantial labs (see §14) |
| `scripts/` | Repo maintenance scripts, each with a colocated stdlib-`unittest` test file |
| `labs/installation/image-list.md`, `labs/installation/system-requirements.md` | Canonical image/chart registry and cluster requirements — must be updated when a lab introduces new images or bumps versions |

Lab filenames are kebab-case and outcome-descriptive (`configure-routing-openai.md`, `mcp-eager-auth-auth0.md`). No numeric prefixes except the root `001`/`002` (and the installation-variant labs that mirror them).

## 2. Lab archetypes

Pick the archetype before writing; conventions differ slightly.

1. **Lightweight flat lab** (~100–420 lines): one feature, terse, imperative. Setup → configure → test → observability → cleanup. Examples: `configure-routing-openai.md`, `in-cluster-mcp.md`, `local-token-rate-limiting.md`.
2. **Heavyweight runbook** (~600–1100 lines): multi-system integration (IdPs, OAuth, upgrades). Numbered steps, background/why sections, ASCII architecture diagrams, troubleshooting tables, validated-version statements. Examples: `mcp-eager-auth-auth0.md`, `figma-mcp-auth0/README.md`, `in-place-rolling-upgrades.md`.
3. **Subdirectory lab**: a runbook that ships assets (see §4).
4. **Reference guide** (rare): no hands-on skeleton, e.g. `production-observability-alerting-and-scaling.md`, `system-requirements.md`. Prose + tables + query cookbook. Only use this shape for genuinely non-hands-on content.

## 3. Canonical lab skeleton (normative)

```markdown
# <Outcome-Oriented Title in Title Case>

<Optional 1-sentence intro: "In this lab, you'll ...">

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- <extra deps: $OPENAI_API_KEY, external accounts, required CLIs, version gates>

## Lab Objectives
- <verb-first outcome bullets, naming CRDs in backticks>
- Validate <the behavior>

## Overview / Background          <- optional; the "why", ASCII diagram, comparison table

## <Setup: secret + backend + HTTPRoute>

## <Step / task sections>         <- each: prose "why" → command block → test → expected output

## Observability                  <- copy the standard block (see §12)

## Cleanup                        <- always last; reverse order (see §10)
```

Rules:

- **Title**: H1, Title Case, describes the outcome, no lab number. Verb-first (`# Configure Basic Routing to OpenAI`) or `<Feature> using Agentgateway` (`# Transformations using Agentgateway`) are both established; verb-first preferred for new labs.
- **Pre-requisites boilerplate** (use verbatim): *"This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces."* Reference labs `001`/`002` by bare backticked number in this sentence; reference all other labs with relative markdown links (`[Mock OpenAI Server lab](configure-mock-openai-server.md)`, `[001 — Install...](../../001-install-enterprise-agentgateway.md)` from two levels deep).
- **Hard requirements** (architecture, sizing, version gates) go in bold-lead blockquotes right after Pre-requisites: `> **Node architecture (important):** ...`. Version-gated features state it plainly: *"available in **v2026.6.3 and later**"*.
- **Lab Objectives**: always present, bulleted, verb-first, e.g. `- Create a route to OpenAI using an `EnterpriseAgentgatewayBackend` and `HTTPRoute``. Multi-part labs may use bold sub-labels: `- **Part A — Impersonation:** ...`.
- **Step headings** (normative): for multi-step runbooks use `## Step N — Title` (em-dash — this is the dominant style; do **not** use `## Step N: Title`). Short labs skip numbering and use plain descriptive `##`/`###` Title Case headings. Use `---` horizontal rules between major sections in long labs.
- **Runbooks** should state what they were validated against: *"This runbook was validated against controller **v2026.6.1** on a local KinD cluster."*
- There is no "Next Steps" section convention; cross-link related labs inline instead. `## Key Takeaways` as a closing bullet list is optional.

## 4. Subdirectory labs (asset-bearing)

A lab gets its own folder only when it ships assets that can't live inline: screenshots, a large OpenAPI spec, templated YAML. Layout (from `labs/mcp/figma-mcp-auth0/`):

```
labs/mcp/<lab-name>/
  README.md            <- the runbook (not <lab-name>.md)
  <name>-mcp.yaml      <- envsubst-templated manifests
  <name>-openapi.json  <- large specs
  images/              <- 01-kebab-description.png, 02-..., zero-padded numbered screenshots
  .gitignore           <- credential env files + generated certs
```

- Include a **"Files in this folder"** table near the top of the README.
- Templated YAML is applied/deleted symmetrically: `envsubst < figma-mcp.yaml | kubectl apply -f -` and `envsubst < figma-mcp.yaml | kubectl delete -f - --ignore-not-found`.
- **Secrets**: credentials go in a gitignored, `source`-able env file (e.g. `.figma-creds.env` with a header comment "Source before running the runbook"). Never commit a populated one — commit nothing, or placeholders only. Generated certs go in a gitignored `example_certs/`.
- Manifests >256 KB must be applied with `kubectl apply --server-side` (dodges the last-applied-annotation size limit).
- Anti-pattern to avoid: a flat `.md` with a sibling bare `images/`-only folder; if you need images, either put them in the repo-level `images/` (as the keycloak eager-auth lab does with `images/keycloak/`) or make it a full subdirectory lab.

## 5. Tone and voice

- Second person, imperative, present tense: "Create one Secret per user...", "Send a request as alice:". Occasional "we"/"let's" in verification prose is fine ("We should see...").
- **Why-first**: every non-trivial config gets a rationale *before* the command. Three established vehicles: a `### Why ...?` sub-header, a `> **Why ...?**` blockquote at the point of a surprising choice, or a comparison table. The strongest labs explain what the client *cannot* forge/override, what fails without the setting, and what error you'd see.
- Be honest about limitations and report real observed numbers with versions: *"Observed result (v2026.6.1, `shutdown.max: 110`): `streams_completed`: 157, `streams_cut`: 3"* — including when the result is imperfect, with the explanation.
- Call out gotchas at the exact step where they bite, including misleading error messages (*"a misleading 403 — it means 'malformed request', not 'no access'"*).

## 6. Command-block conventions

All commands use ```` ```bash ```` fences. Manifests are applied inline via heredoc inside the bash fence — not separate `yaml` fences.

**Heredoc quoting is semantic (normative):**

- Default: unquoted `kubectl apply -f - <<EOF` when the manifest interpolates shell vars (`$OPENAI_API_KEY`, `${AUTH0_GATEWAY_HOST}`).
- Quoted `kubectl apply -f - <<'EOF'` when the body contains `$`, regexes, backticks, or script text that must NOT expand (guardrail regex policies, k6 scripts with `__ENV`, CEL with `$`). If you must mix, escape selectively (`\$VAR`) inside an unquoted heredoc.
- Spell it `-f -` (with the space). Both `-f-` and `-f -` exist in the repo; standardize on the space for new labs.

**The universal idioms — copy these verbatim:**

Gateway address (works for both LB IPs and hostnames):

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
echo $GATEWAY_IP
```

Idempotent secret creation:

```bash
kubectl create secret generic openai-secret -n agentgateway-system \
--from-literal="Authorization=Bearer $OPENAI_API_KEY" \
--dry-run=client -oyaml | kubectl apply -f -
```

Installed-version auto-detection (instead of hardcoding):

```bash
export ENTERPRISE_AGW_VERSION=$(helm get metadata enterprise-agentgateway -n agentgateway-system | awk '/^VERSION:/ {print $2}')
```

**Env vars**: `SCREAMING_SNAKE_CASE`, exported. `GATEWAY_IP` is the gateway address, always. IdP/provider vars are prefixed (`AUTH0_*`, `ENTRA_*`, `FIGMA_*`, `KEYCLOAK_*`). Document "you must already have this set" with the self-referential export (`export OPENAI_API_KEY=$OPENAI_API_KEY`) and put placeholder guidance in an inline comment: `export AUTH0_ISSUER=https://YOUR_TENANT.us.auth0.com/     # TRAILING SLASH REQUIRED`.

**curl conventions**: `curl -i "$GATEWAY_IP:8080/openai" -H "content-type: application/json" -d '{...}'` with a pretty-printed JSON body. Use `-v` when demonstrating failover/response codes, and `-s -o /dev/null -w "HTTP %{http_code}\n"` inside `for i in {1..20}` loops for rate-limit/budget exhaustion tests. The house test prompt is `"Whats your favorite poem?"` (deliberately short/countable for token labs).

## 7. Verification and expected output

Every mutating step is immediately followed by a check. Two accepted styles:

1. **`Expected output:`** label + fenced block (use this exact casing for new labs; the repo also has `Expected Output:` / `Expected response:` / `Expected behavior:` drift). Truncate long output and say so: `Expected output (truncated):`.
2. Prose assertion for simple cases: *"Both should return `HTTP 200`"*, *"Verify that the request is denied with a 403"*. Guardrail-style compact form: `Expected: `HTTP 403` — <message>`.

Standard verification tools:

- `kubectl get pods -n agentgateway-system` + expected-output table (as in `001`).
- Access logs: `kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20` (add `--follow` and tail multiple pods with `--prefix` to show session stickiness).
- `jq` for extraction (`| jq -r '.access_token'`), header grepping (`curl -s -D - -o /dev/null ... | grep -iE "^HTTP|x-waf-action"`).
- Rigorous labs may end with a `## Validation checklist` numbered list and/or an "Interpreting the Results" table (pattern | result | why).
- Zero-downtime/upgrade labs: run a k6 `Job` for continuous load, trigger the change mid-run, and state the explicit success criterion (*"`http_req_failed` is `0.00%` and `checks` is `100.00%`"*).

## 8. YAML manifest conventions

- **Namespace**: `agentgateway-system` for everything unless the lab is specifically about namespaces (blue/green) or deploys a standalone system (`keycloak`).
- **The stable resource trio**: `gateway.networking.k8s.io/v1` `HTTPRoute`; `enterpriseagentgateway.solo.io/v1alpha1` `EnterpriseAgentgatewayBackend` and `EnterpriseAgentgatewayPolicy`. Rate limiting: `ratelimit.solo.io/v1alpha1` `RateLimitConfig` (short name `rlc`). Gateway params: `EnterpriseAgentgatewayParameters` named `agentgateway-config`, attached via `spec.infrastructure.parametersRef` on the `Gateway` `agentgateway-proxy`.
- **Naming**: descriptive kebab-case tied to the lab. Canonical reusable pair: backend `openai-all-models` + route `openai` on path prefix `/openai`. Policies named for their function (`comprehensive-prompt-guard`, `timeout-retry-policy`).
- **backendRefs to enterprise backends always carry group/kind**:

  ```yaml
  backendRefs:
    - name: openai-all-models
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayBackend
  ```

- Routes set `timeouts: request: "120s"` by default (except labs demonstrating timeouts).
- Auth: `auth: secretRef: {name: openai-secret}` for real providers; `auth: passthrough: {}` for mocks.
- **Comment style**: the house "uncomment to enable" marker is `#---`:

  ```yaml
  openai: {}
    #--- Uncomment to configure model override ---
    #model: ""
  ```

  Plain `#` lines for explanatory comments; numbered `# 1.` / `# 2.` comment headers to structure multi-part cleanup blocks.
- MCP Services: note there is `appProtocol` drift (`kgateway.dev/mcp` in newer labs vs `agentgateway.dev/mcp` in older ones) — match the lab family you're extending and verify against the current release. OpenAPI-protocol MCP backends must use the enterprise `spec.entMcp` (+ `openAPI.schemaRef`); OSS `spec.mcp` only supports `StreamableHTTP`/`SSE`.

## 9. Callouts, tables, diagrams, images

- **Callouts (normative)**: bold-lead blockquotes — `> **Note:** ...`, `> **Tip:** ...`, `> **Why ...?** ...`, `> **Note — <specific topic>:** ...`. This is the dominant house style. GitHub alert syntax (`> [!NOTE]`) appears only in `001` and the newest labs (WAF); either is acceptable but don't mix both within one lab. Inline `**Bold:**` lead-ins (not blockquoted) are used for structured explanation lists (`**Key Configuration Points:**`, `**What's happening:**`).
- **Diagrams**: ASCII art in fenced code blocks. **No mermaid anywhere in the repo** — keep it that way for consistency (renders everywhere, diffable). Two established shapes: boxed left-to-right flows with numbered arrows (OAuth flows), and fan-out topologies (federation).
- **Tables**: GitHub pipe tables for decision matrices (X vs Y), variable references, tool-name mappings, metric references, results summaries, and — in runbooks — a **Troubleshooting table** (`| Symptom | Likely cause | Fix |`).
- **Images**: screenshots only for UI-heavy steps (IdP consoles, Grafana, Claude Desktop). Reference with relative paths (`../../images/claude-desktop/...` or `images/01-...png` in subdirectory labs). Zero-padded numbered kebab names.
- Collapsible `<details><summary>` blocks for optional deep-dives.

## 10. Cleanup (normative)

Always the final section, `## Cleanup`, a single bash block (or numbered blocks in runbooks) that:

1. Deletes resources in **reverse creation order**, fully namespaced, one per line.
2. Uses `--ignore-not-found` on every delete (newer labs do; older ones error if a step was skipped — follow the newer style).
3. **Restores any mutated global state** — this is the part most easily forgotten: patch replicas back (`kubectl patch ... '{"spec":{"deployment":{"spec":{"replicas":2}}}}'`), re-run `helm upgrade` to remove values you added, restore log levels, `rm -rf` venvs and generated certs, `unset` env vars, remove `/etc/hosts` entries.
4. In runbooks, structure teardown with `# 1.` / `# 2.` ... comment headers reversing each step.

```bash
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system comprehensive-prompt-guard --ignore-not-found
kubectl delete httproute -n agentgateway-system openai --ignore-not-found
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system openai-all-models --ignore-not-found
kubectl delete secret -n agentgateway-system openai-secret --ignore-not-found
```

## 11. Shared assets (`lib/`) and app-based labs

- Reusable manifests/scripts/apps live under `lib/<concern>/`, **not** beside the lab. If reusable, give the asset a README with a `## Used by` section back-linking consuming labs (and keep those links current when labs move).
- Labs reference lib assets with **repo-root-relative paths, run from the workshop root** — no `cd`, no `$REPO_ROOT`: `kubectl apply -n keycloak -f lib/keycloak/deploy.yaml`, `./lib/jwt/generate-jwt.sh lib/jwt/claims/admin.json`.
- **Python apps** (crewai/langchain): code is a committed file in `lib/`, configured purely via env vars (`os.environ.get("GATEWAY_IP")`), with instructive comments in the source. venv lives *inside* the lib dir and binaries are invoked by full path — never `source activate`:

  ```bash
  python3.12 -m venv lib/crewai/multi-agent-researcher-writer/.venv
  lib/crewai/multi-agent-researcher-writer/.venv/bin/pip install -r lib/crewai/multi-agent-researcher-writer/requirements.txt
  GATEWAY_IP="$GATEWAY_IP" CREWAI_TRACING_ENABLED=false \
    lib/crewai/multi-agent-researcher-writer/.venv/bin/python3 lib/crewai/multi-agent-researcher-writer/crew.py
  ```

- **k6 load scripts** are the exception: embedded inline as ConfigMap heredocs (`<<'EOF'` to protect `__ENV`) and run as `batch/v1` Jobs mounting the ConfigMap. Pin the image version (`grafana/k6:0.54.0`, not `:latest`).
- **JWT test identities**: prefer the shared helper — `lib/jwt/generate-jwt.sh` with persona claim files in `lib/jwt/claims/` (`kid: workshop-jwt-key-001`, issuer `workshop.solo.io` chosen deliberately so lab JWTs don't cross-validate against other labs' policies). Demo keypairs must be flagged "**Demo-only — do not use outside this workshop.**" (Be aware a second, older system exists: `jwt-auth-with-rbac.md` embeds static pre-signed tokens with inline JWKS `kid: solo-public-key-001`. Don't create a third.)
- For live-IdP labs, mint tokens via the IdP API into env vars: `export USER_JWT=$(curl ... | jq -r '.access_token')`.

## 12. Observability section

Most labs end feature work with a near-verbatim `## Observability` block — copy it wholesale from a sibling lab in the same family rather than rewriting:

- Sub-headings: `### View Metrics Endpoint`, `### View Metrics and Traces in Grafana`, `### View Access Logs`, `### (Optional) View Traces in Jaeger`.
- Grafana access: `kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000`, login `admin` / `prom-operator`.
- Metrics endpoint: port-forward `15020` and curl `/metrics`.
- Canonical MCP metric names: `agentgateway_mcp_tool_calls_total`, `agentgateway_mcp_server_requests_total`, `agentgateway_mcp_request_duration_seconds`; access-log fields `mcp.method`, `mcp.resource`, `mcp.target`, `http.status`.
- PromQL goes in ```` ```promql ```` fences. Sum input/output token types separately to avoid label conflicts; note that `increase()` extrapolation can produce fractional tokens.
- Don't re-document the monitoring stack — point at `002`.

## 13. MCP-specific conventions

- **Testing tiers** (pick per lab weight): (1) **MCP Inspector** — `npx @modelcontextprotocol/inspector@0.21.1` (pin the version), UI steps: Transport `Streamable HTTP`, URL `http://$GATEWAY_IP:8080/mcp`, Connect → Tools → List Tools → Run Tool; (2) **Claude Code** — `claude mcp add --transport http <name> http://$GATEWAY_IP:8080/mcp`, verify with `/mcp` and `claude mcp list`; (3) **raw curl JSON-RPC** for "under the hood" sections.
- The canonical curl JSON-RPC sequence: `initialize` with `-i` to capture headers, extract `mcp-session-id` into `SID`, then pass `-H "Mcp-Session-Id: $SID"` on every call, always with the dual Accept header `application/json, text/event-stream`. Pipe responses to `python3 -m json.tool`. Note that with `sessionRouting: Stateless` no session id is emitted and the empty header is harmless.
- **SSE requires single-replica session affinity** — if your lab uses `protocol: SSE`, include the replica patch and the note; StreamableHTTP/OpenAPI backends scale across replicas.
- External APIs used by MCP labs get prereq bullets with signup links and a required/optional label (*"**FRED API key** (required — the pod won't start without it)"*); prefer key-free APIs (Open-Meteo) where possible.
- IdP console setup is documented as numbered steps under a dedicated `### <IdP> (Layer A)` heading with exact copy-paste callback URLs in fenced blocks, plus a variable reference table.

### Variant labs (multi-IdP)

The established pattern is **clone-and-substitute, explicitly scoped**: copy the closest existing lab wholesale, swap the IdP-specific prereq block, env-var prefix, JWKS/issuer config, and callback URLs — and add a top-of-file blockquote declaring the delta:

> **This is the Entra variant of [`../figma-mcp-auth0/`](../figma-mcp-auth0/README.md).** Only **Layer A** (the inbound MCP auth) changed — Auth0 → Entra. **Layer B is unchanged.**

Mark unchanged sections as *"Identical to the Auth0 lab."* rather than re-explaining them. Keep verification/cleanup blocks byte-identical to the sibling except for the IdP name.

## 14. Development workflow for substantial labs

For a substantial new lab, the repo workflow is brainstorm → design → plan → implement:

- `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` — opens with `**Date:**` / `**Status:**` / `**Author:**`, then `## Goal`, `## Decisions (from brainstorming)`, `## Deliverables`.
- `docs/superpowers/plans/YYYY-MM-DD-<topic>.md` — an executable, checkbox-tracked (`- [ ]`) implementation plan (same date/topic slug as the spec).
- Helper scripts in `scripts/` ship with a colocated stdlib-`unittest` test file (`test_<name>.py`, direct import, positive **and** negative cases per rule) and a docstring linking their design spec.

## 15. Repo maintenance when adding a lab

**README.md** (the index):

- Add a bullet under the correct `## <Category>` section: `- [Lab Title](labs/<category>/<name>.md) _(Provider)_`. Provider/tech tags in trailing italics: `_(OpenAI)_`, `_(AWS Bedrock / EKS)_`.
- Multi-category labs are listed under **each** relevant section with reciprocal `_(see also: <Other Categories>)_` italics. An em-dash description may follow the link for complex labs.
- New category → new `## Section`, `---` separator, and a `# Table of Contents` anchor entry.
- Add capability bullets under `# Use Cases`.
- Bump `## Validated on` if you validated on newer versions.

**tracks/**: add a row (`| Lab | What you'll learn |`) to the relevant `### Use Case` table in `tracks/llm-track.md` and/or `tracks/mcp-track.md` — paths climb one level (`../labs/...`). New capability areas may warrant a new `### Use Case` block and a line in the completion-order diagram.

**labs/installation/image-list.md**: any new chart-managed image → new `### <image>` block; version bump → update the `**vX.Y.Z**` header and every tag. Update `system-requirements.md` if version support or sizing changes.

**CHANGELOG.md + commit**:

- Open a new version block at the top: `X.Y.Z - (M-D-YY)` followed by a `---` underline. Patch bump for a single lab/fix; minor bump for a batch of labs or structural change.
- Bullets name touched files in backticks and lead with `Add`/`Update`/`Bump`: `- Add \`labs/security/WAF.md\`: WAF for agentic traffic ...`. Mark breaking changes inline with `**Breaking:**`.
- Commit message is exactly `see CHANGELOG (X.Y.Z)` — one commit per changelog version; all descriptive detail lives in the CHANGELOG.

## 16. Known inconsistencies (and what to do)

These exist in the corpus; new labs should follow the **normative** column.

| Drift observed | Normative choice for new labs |
|---|---|
| `## Step N — Title` vs `## Step N: Title` vs unnumbered | `## Step N — Title` (em-dash) for runbooks; unnumbered `##`/`###` for short labs |
| `kubectl apply -f -` vs `-f-` | `-f -` (with space) |
| `Expected output:` / `Expected Output:` / `Expected response:` | `Expected output:` |
| `> **Note:**` blockquotes vs `> [!NOTE]` alerts | Bold-lead blockquotes; don't mix styles in one lab |
| Cleanup with/without `--ignore-not-found` | Always `--ignore-not-found` |
| `appProtocol: kgateway.dev/mcp` vs `agentgateway.dev/mcp` | Both verified working live on v2026.6.3 — match the lab family you extend. What actually breaks discovery is *omitting* `appProtocol`: a Service port without an MCP appProtocol is never picked up by selector-based backends (`mcp: no backends configured`) |
| Three JWT identity systems (lib/jwt helper, static inline tokens, live Keycloak) | Prefer `lib/jwt/generate-jwt.sh` for static-identity labs, live IdP for OAuth labs; don't invent a new one |
| `grafana/k6:latest` vs pinned | Pin the image version |
| Lowercase `## curl openai` headings | Established quirk in routing labs; fine to keep within that family |
| Scratch files in `/tmp` | Avoid; keep everything in env vars or the lab folder |

Also watch for: stale relative links after files move (update `lib/*/README.md` `## Used by` links), README `## Validated on` lagging the install labs' `ENTERPRISE_AGW_VERSION`, and copy-paste boilerplate (Observability blocks, JWKS blobs) silently drifting from the source you cloned.

---

## Checklist when adding a lab

- [ ] Picked archetype (§2) and cloned the closest existing lab in the same family
- [ ] Skeleton: Title → Pre-requisites (boilerplate sentence) → Lab Objectives → steps → Observability → Cleanup (§3)
- [ ] Every step: why-prose → command → verification with `Expected output:` (§5, §7)
- [ ] Heredocs quoted correctly (`<<'EOF'` only for literal-`$` bodies); standard `GATEWAY_IP` / secret / version idioms copied verbatim (§6)
- [ ] Manifests: `agentgateway-system` namespace, kebab-case names, group/kind on backendRefs, `#---` uncomment markers (§8)
- [ ] Cleanup deletes in reverse order with `--ignore-not-found` and restores all mutated global state (§10)
- [ ] Shared/reusable assets placed in `lib/<concern>/`, referenced root-relative; secrets gitignored, never committed populated (§4, §11)
- [ ] README.md: category bullet + provider italics + reciprocal `_(see also:)_` + Use Cases bullets (§15)
- [ ] tracks/: row added to the relevant use-case table (§15)
- [ ] image-list.md / system-requirements.md updated if images or versions changed (§15)
- [ ] CHANGELOG.md: new version block; commit as `see CHANGELOG (X.Y.Z)` (§15)
