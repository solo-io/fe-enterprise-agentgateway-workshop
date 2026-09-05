# Handoff — lab E2E suite

Context for picking this up cold. Companion to `PLAN.md` (design + full 82-lab
tier table) and `README.md` (how to add a spec).

Repo: `~/Desktop/solo/solo-github/fe-enterprise-agentgateway-workshop`
Enterprise Agentgateway `v2026.7.0` · Solo UI `0.5.1`

## 1. Where things stand

Built an E2E suite at `e2e/` that tests the workshop labs by **replaying the bash
blocks out of each lab's own markdown**. A sidecar YAML spec per lab selects which
sections run and asserts on their output. Lab files are never modified by the
harness — the point is to catch doc drift when a release changes behaviour.

- **Counts are derived, not hand-maintained.** Run `./e2e/run-e2e.sh --lint` for
  the coverage list and `./e2e/run-e2e.sh --list` for the tier table; `t1-key` is
  complete. Every executing lab passes standalone; see §4 for per-lab assertion
  counts, but re-run `./e2e/run-e2e.sh` after any change rather than trusting a
  recorded total.
- `routing/openai-video` was withdrawn — the Sora API it taught is deprecated
  and shuts down 2026-09-24 (`PLAN.md` #22).
- **`e2e/` is tracked in git.** No gitignore entry to remove.
- **Lab fixes are covered in `CHANGELOG.md`** under "Fixes and improvements
  caught in testing, optimizations for agent use" — that deliberately terse
  wording is intentional; do not expand it into per-lab bullets.
- **Nothing is committed.** The user commits via their own release flow. Author
  changes, leave them staged-free.

## 2. Cluster state

`kubectl config current-context` → `cluster1` (kind via `vind`, 2 nodes).
`001` + `002` are installed and verified: controller, 2 proxy replicas, all four
extension services (ext-auth, ext-cache, rate-limiter, waf-server), Solo UI +
ClickHouse + OTEL collector, Prometheus + Grafana with the AgentGateway dashboard.
Gateway `agentgateway-proxy` is `PROGRAMMED=True` at `172.18.255.254:8080`.

Rebuild from scratch: `vind-up` then `./e2e/run-e2e.sh --install-base --only-base`
(20 assertions; installs 001 and 002 by replaying their markdown, so the install
docs are themselves tested).

## 3. Running it

```bash
./e2e/run-e2e.sh --lint                      # no cluster: specs vs labs + uncovered list
./e2e/run-e2e.sh --list                      # coverage table with tiers
./e2e/run-e2e.sh --install-base --only-base  # stand up the baseline
./e2e/run-e2e.sh                             # everything runnable
./e2e/run-e2e.sh --tier t0                   # credential-free only
./e2e/run-e2e.sh mcp/in-cluster-mcp -v       # one lab, streaming output
```

Debug artifacts per lab: `e2e/.work/<lab>/driver.sh` (generated bash),
`blocks/NNNN.sh` (exact block that ran), `blocks/NNNN.out` (its output).
Requires bash ≥ 4 (`/bin/bash` on macOS is 3.2 — shebang picks up homebrew),
python3 + PyYAML, kubectl, helm, jq, k6.

## 4. Coverage

Counts are derived — run `./e2e/run-e2e.sh --lint` for the coverage list and
`./e2e/run-e2e.sh --list` for the tier table.

A clean full run with `--install-base` is **681 PASS / 0 FAIL / 58 SKIP / 2
excluded** across 43 lab directories. Reconcile against that total, and against
the per-lab counts below, before trusting a green run — a *lower* pass count with
no explanation means coverage stopped running rather than that things improved.
All 58 skips are deliberate `run: none` (foreground port-forwards, browser steps,
MCP Inspector, OpenShift-only alternatives, the baseline `Uninstall`); a skip
reading `SKIP — missing: VAR` is a credential gap, not a deliberate one.

Counts below are the 82 workshop labs and exclude `001`/`002`, which are the
baseline rather than labs under test.

| Tier | Total | Done | Left |
|---|---|---|---|
`t0` no credentials | 20 | **14** | 6 |
`t1-key` LLM/API key | 27 | **27** | 0 |
`t2-idp` Auth0/Okta/Entra | 7 | 0 | 7 |
`t3-cloud` AWS/Azure/GCP | 9 | 0 | 9 |
`t4-infra` OpenShift/multi-cluster/air-gap | 5 | 0 | 5 |
`t5-manual` reference/interactive | 7 | 1 | 6 |
`excluded` destructive | 5 | 1 | 4 |

Two labs moved tier since `PLAN.md`'s per-lab table was written, which is why t0
reads 20 rather than 24: `routing/routing-match-types` and `mcp/mcp-tool-federation`
are `t1-key` (they authenticate to OpenAI and to FRED respectively), while
`mcp/mcp-eager-auth-keycloak` is `excluded` and `installation/image-list` is
`t5-manual`. `--list` is the authority; the PLAN table is the original plan.

**47 labs are automatable on one cluster (t0 + t1-key); 41 done = 87%.**
Every `t1-key` lab that can run here now has a spec; the only single-cluster gap
left is the 6 expensive `t0` labs.

Implemented, with assertion counts: `configure-openai-embeddings` 31 ·
`builtin-guardrails` 31 · `mcp-tool-federation` 30 · `routing-match-types` 27 ·
`mcp-tool-mode-search` 20 · `mcp-tool-mode-code` 18 · `virtual-keys` 18 ·
`advanced-guardrails-webhook` 32 · `global-rate-limiting` 29 ·
`llm-failover-advanced` 24 · `opa-authorization` 23 · `WAF` 23 ·
`byo-opa-grpc-ext-authz` 20 · `in-cluster-mcp` 17 · `openai-audio` 17 ·
`frontend-mtls` 19 · `llm-byo-grpc-ext-authz` 16 · `remote-mcp` 16 ·
`langchain-with-agentgateway` 16 · `crewai-with-agentgateway` 14 ·
`evaluate-openai-model-performance` 15 ·
`llm-cost-management` 16 · `_base/001` 14 · `timeouts-and-retries` 13 ·
`sni-matching` 15 · `configure-body-based-routing` 13 · `tls-termination` 17 ·
`llm-failover` 11 ·
`openapi-to-mcp-in-cluster` 12 · `transformations` 12 · `openai-streaming` 11 ·
`mcp-tool-rate-limiting` 11 · `mcp-byo-grpc-ext-authz` 11 ·
`openapi-to-mcp-external-api` 10 ·
`production-observability-alerting-and-scaling` 9 · `prompt-enrichment` 8 ·
`configure-mock-openai-server` 7 · `configure-routing-openai` 7 ·
`external-moderation-guardrails` 7 · `local-token-rate-limiting` 6 ·
`_base/002` 6 · `system-requirements` 6 · `direct-response` 3.

Non-running specs: `installation/image-list` (t5-manual, reference list) and
`mcp/mcp-eager-auth-keycloak` (excluded — helm-upgrades the controller, browser
OAuth).

## 5. Recommended next steps, in order

1. **The 6 remaining t0 labs** — the only single-cluster work left, and all of it
   expensive: `mcp/composable-mcp` (29 blocks, `lib/jwt`),
   `identity-delegation/obo-token-exchange-fundamentals` (33 blocks, in-cluster
   Keycloak), `platform-engineering/platform-and-developer-helm-charts-mcp` (36
   blocks, local charts), both `load-testing/*-k6` (k6 is installed here), and
   `inference/configure-inference-routing-with-vllm` (downloads Qwen2.5-0.5B — may
   not be viable on kind at all; its all-replica `/metrics` loop is still the one
   lab edit never verified by a run).

   Three t1-key labs are deliberately uncovered and should stay that way:
   `configure-openai-batches` (Step 5 downloads results from a batch job that can
   take hours), `routing/configure-routing-anthropic` (no `CLAUDE_API_KEY` here —
   it would only ever SKIP), and
   `platform-engineering/centralized-llm-ops-helm-chart` (reinstalls the gateway;
   classed `excluded`).
2. **Raise the readiness budget on the npx-based MCP server** (~6 labs). The
   container runs `npx -y @modelcontextprotocol/server-everything` at startup,
   downloading from npm every time, with `initialDelaySeconds: 15` +
   `failureThreshold: 3` ≈ 45s. Under repeated runs this is the single biggest
   source of suite flakiness — it caused a 12-failure cascade in one full run, and
   the same lab passed 17/0 immediately after on a clean namespace.
3. **t3-cloud identity-federation labs** (IRSA, AKS workload identity, Vertex SA)
   can never run on kind. Give them specs that validate manifest rendering and CRD
   acceptance only, with the reason in `reason:`.

## 6. Conventions and gotchas — all learned the hard way

**Spec authoring**

- `run:` is `all` | `none` | 1-based ordinals *within a section* (`[1, 3]`).
- Assertions attach to the **last** block of a step. To assert on several blocks of
  one section, write several steps against the same section.
- `wait:` fires after the section's **last** block (a section often opens with
  `kubectl create namespace`).
- `retry: {attempts, delay, until}` — needed for anything async: route programming,
  policy attachment, label-selector backend discovery. Like `assert:` and `wait:`,
  it attaches to the step's **last** block only. It used to be applied to every
  block in the section, which misfired silently: a section's opening `kubectl
  apply` prints `... created`, never the `type: Attached` the guard waits for, so
  it spun out its full budget on every run — ~310s of dead sleep across the suite,
  no protection against the race it was written for, and 8-10× repeated execution
  of side-effecting curls in the rate-limit and budget labs. Write `until:` to
  match what the **last** block prints, and keep it in step with the step's
  assertion — a guard stricter than its own assertion (`proxied_completions=[2-9]`
  against an asserted `[2-9]|[0-9]{2,}`) can never match a two-digit count.
  Audit for regressions with: for each `e2e_run_block` in `.work/*/*/driver.sh`
  that carries `attempts > 1`, grep its `until` against the sibling `.out`; a
  guard that never matched is a dead guard.
- `probe:` is harness-authored verification, for where a lab verifies by hand
  (MCP Inspector, Grafana, jwt.io). The only non-doc commands in the suite.
- `run: none` always needs a `reason:` — it surfaces as SKIP so gaps stay visible.
- A renamed lab heading fails loudly with `DRIFT: ... has no section '...'`.
  Fix the spec; never loosen the matcher.

**Shell traps**

- Drivers run with **no `set -e`, `-u`, or `-o pipefail`** — deliberately, to match
  a reader's terminal. `pipefail` breaks `cmd | grep -q` (grep exits on first match,
  producer takes SIGPIPE, pipeline reports failure). Only bites on large outputs
  like a 128KB `/metrics` dump, so it reads as a flake. Cost two debug cycles.
- Assertions use here-strings, never `printf | grep -q`, for the same reason.
- Use `e2e_pf` / `e2e_pf_stop` for port-forwards. A raw `kill $pf; wait $pf`
  returns 143, which becomes the block's exit code and fails the step even when
  every assertion passed.
- Blocks are `source`d into **one shell**, so `export GATEWAY_IP`, `$SESSION`,
  `$SID`, `$TOKEN` carry across blocks — and functions a probe defines are visible
  to later probes.
- BSD `wc -c` pads with spaces: match `[[:space:]]+[0-9]+ bytes`, not a single space.

**MCP specifics**

- `lib.sh` exports `e2e_mcp_init` / `_rpc` / `_tool` / `_tools` / `_status` /
  `_expect`. Use them instead of hand-rolling; `e2e_mcp_expect <needle>
  present|absent <cmd...>` polls, which is why `in-cluster-mcp` went 77s → 51s.
- The gateway answers MCP over `text/event-stream`: bodies arrive as `data: {...}`.
  Strip with `sed 's/^data: //'` before any JSON parse.
- Upstream JSON inside MCP content is **escaped**: assert on `\"object\":\"list\"`,
  not `"object": "list"`.
- With `sessionRouting: Stateless` the gateway issues **no** session id — an empty
  `$SID` is correct. Never assert on its value.
- `agentgateway_*` metrics are `name{labels} value`, so match
  `agentgateway_[a-z_]+[{ ]` — a trailing space alone fails.

**Cluster hygiene**

- Namespace `mcp` is **shared by six labs — never delete it in a lab's Cleanup.**
  Doing so leaves it `Terminating` while the next lab deploys into it. Idempotent
  `kubectl create namespace mcp --dry-run=client -o yaml | kubectl apply -f -`
  already handles re-runnability. Lab-private namespaces (`stripe-mcp`,
  `composable-mcp`) *are* deleted by their own labs.
- The runner fingerprints the shared Gateway (`listeners[*].port` +
  `infrastructure.parametersRef.name`) before and after every lab, names any lab
  that damages it, and restores it. A full `kubectl apply` of the shared Gateway
  silently drops `parametersRef`, which detaches the params and freezes proxy
  replicas — the bug `sni-matching` had.
- `entRateLimit` uses a fixed wall-clock minute window: retry the whole burst
  rather than asserting on one call, and allow ~10s settle after attach.

**Never run from the suite**

- `claude mcp add` / `claude mcp remove` (`remote-mcp` Cleanup block 2) — rewrites
  the operator's real Claude Code config.
- Placeholder-bearing blocks (`<your-fred-key>`, `--version <new-version>`).
  Replace with a probe that uses resolved env vars.
- Foreground `kubectl port-forward` and `kubectl logs --follow` — never return.
- 002's optional `GRAFANA_ADMIN_PASSWORD` export — sets the password to the literal
  `your-secure-password`.

## 7. Credentials

Resolution order per variable: environment → `e2e/.env.local` (cached, chmod 600,
gitignored) → shell rc files (only the matching `export VAR=` line is evaluated in
a subshell, never the whole file) → interactive prompt → skip. All resolved up
front so prompts arrive in one batch; `--no-prompt` is CI-safe. Unresolved keys
mean the lab reports `SKIP — missing: VAR`, never a silent pass.

Present in `~/.zshrc`: `OPENAI_API_KEY`, `SOLO_TRIAL_LICENSE_KEY`, `FRED_API_KEY`,
`BLS_API_KEY`, all six `AUTH0_*`, all seven `OKTA_*`, four `ENTRA_*`, `FIGMA_*`,
`GEMINI_API_KEY`, `NVIDIA_API_KEY`.

Missing: `CLAUDE_API_KEY`, `AWS_*`, `AZURE_OPENAI_API_KEY`, `BEDROCK_API_KEY`,
GCP/Vertex creds, and `ENTRA_CLIENT_ID`/`ENTRA_CLIENT_SECRET`/`ENTRA_API_SCOPE`.

Two known blockers for t2-idp: the Okta app cannot mint `client_credentials`
tokens (those labs need a supplied `VALID_TOKEN`), and the Auth0 eager-OAuth issuer
only binds :7777 when `tokenExchange.enabled=true`.

## 8. Known flakiness

- `openapi-to-mcp-external-api` weather call → `upstream call failed: Connect:
  deadline has elapsed` on a cold connection to `api.open-meteo.com`. Retries now.
- `mcp-tool-mode-code` "Hit the timeout" → produced **zero bytes** and a nonzero
  exit. The 60s `codeMode.timeout` races the HTTPRoute's own request timeout; when
  the route wins the connection drops instead of returning the MCP error result the
  lab documents. Retries now, but **worth investigating** — if the route timeout is
  genuinely shorter, the lab's documented output is only reliable by luck.
- The npx readiness issue in §5.2 is the dominant one.

None of the three recurred in the 681-assertion full run, so the retries are
holding. The `mcp-tool-mode-code` timeout race is still worth the investigation
noted above.

Three more, all found by a second full run and all fixed:

- **`routing/openai-audio` — `kubectl logs -l` races a terminating pod.** The
  selector resolves the pod list first, then streams each pod, so it exits 1 with
  `pods "..." not found` when a preceding lab has just rolled the proxy
  (`llm-failover*` run immediately before it alphabetically). Its spec now retries
  until an audio request line is actually present. The old assertion —
  `contains: agentgateway-proxy` — could never have caught this: the lab's block
  passes `--prefix`, so the pod name is on every line even when no request was
  logged. It now asserts the request line itself.
- **`routing/timeouts-and-retries` asserted a retry count that the product does
  not guarantee.** The 2s probe demanded `retry.attempt=[5-9]`. Three runs
  measured **7, then 4, then no `retry.attempt` field at all**. The cause is in
  the lab's own policy: it retries `codes: [503]` only, and the lab induces
  failure by scaling the mock to 0 — so whether the proxy answers a zero-endpoint
  Service with a quick 503 (retried, many attempts) or just hangs until the
  request timeout (no retry, field absent) is a race. Two successive attempts to
  bracket the count both failed: `[3-9]|[0-9]{2,}`, and even "at least as many as
  the 100ms budget" (measured 0 vs 3). The probe now asserts only what is
  invariant — `http.status=504`, `reason=Timeout`, and `duration=200[0-9]ms`,
  which is what actually proves the 2s policy took effect, since the 100ms policy
  caps the same request at ~1xx ms — and echoes the count for diagnosis.

  Two loose ends here, both for the user to decide:
  **(a)** the lab documents `retry.attempt=7` as expected output, which is one
  sample of a racy quantity; a reader will often see something else, so the lab
  arguably needs a deterministic 503 source (a mock that returns 503) rather than
  a scaled-to-0 Service if it wants to teach backoff.
  **(b)** the sibling 100ms probe still asserts `retry.attempt=[2-4]`. It has held
  on every run observed (measuring 3 each time) and was left alone rather than
  pre-emptively weakened, but it is the same racy quantity and is the most likely
  next flake in this lab.
- **`routing/openai-streaming` — curl's progress meter corrupted the body.** See
  the meter note below; the lab's curl now passes `--no-progress-meter`, which is
  also what makes its real output match the SSE its "Expected output" documents.

**curl's progress meter can land in the middle of a response body**, not just
glued to a status line. Blocks captured stdout and stderr into one file, and the
meter goes to stderr with `\r`, so a body could arrive as
`"total_tokens":<CR>100 16966 ... 14009<CR>91,` — leaving `"total_tokens":`
followed by no digit, so `matches: '"total_tokens":[0-9]+'` failed. It split a
token in 84 block outputs across 22 labs; it only *fails* where an assertion
happens to read that spot, which is why it presents as an unreproducible flake.
`e2e_run_block` now captures the two streams separately and concatenates
stderr-then-stdout, so the meter cannot interleave into a body and stdout stays
last for `assert_status`'s `-w '%{http_code}'` branch. The separating newline
matters: concatenating a meter that has no trailing newline glues it onto stdout's
first line and breaks line-anchored matches. Find remaining cases with:

```bash
for f in e2e/.work/*/*/blocks/*.out; do
  n=$(perl -ne '$c++ while /[:0-9]\r[ \t]*\d/g; END{print $c+0}' "$f")
  [ "$n" -gt 0 ] && echo "$n $f"
done
```

Do not try to repair a merged capture after the fact. Stripping the `\r` alone
makes `"total_tokens":[0-9]+` match the *meter's* digits — a false pass that is
worse than the failure.

Worth knowing that a *passing* run can still hide a broken retry: reaching
`attempt N/N` in the log is ambiguous — `lib.sh`'s loop breaks whether the `until`
regex finally matched or the budget simply ran out — and the step then passes on
its assertions either way. Ten steps sat at `N/N` before the retry scoping was
fixed. Use the driver-vs-`.out` audit in §6 rather than reading the attempt
counters.

Two new sources handled while adding the t1-key specs:

- **`curl`'s progress meter can swallow a status line.** The meter goes to stderr
  with `\r` and no trailing newline, so on a large or slow response the headers
  land glued to it: `... --:--:--     0HTTP/1.1 200 OK`. `assert_status` was
  line-anchored, so the same lab passed or failed depending on response size and
  timing. It now splits the meter off before matching. Found on the embeddings
  lab, where the response is ~30KB; it would have hit any large-response lab.
- **Rate-limit counters outlive a run.** `virtual-keys` budgets live in
  `ext-cache` Redis for a full hour, so a second run inside that hour starts
  exhausted and the isolation assertions fail for the wrong reason. Its spec now
  opens with the lab's own documented reset (`rollout restart ext-cache`). Any
  future budget lab needs the same.

**Two suite runs on one cluster void each other.** Gateway-scoped policies are
the reason: `virtual-keys` and `llm-cost-management` both attach `api-key-auth`
(`mode: Strict`) for the duration of the lab, so anything else in flight gets
`401 api key authentication failure: no API Key found`, and `llm-failover` scales
the shared proxy to one replica. Both produce failures that read as genuine lab
bugs on labs that are actually fine. `pgrep -f run-e2e.sh` before starting, and
discard the overlapping run rather than debugging it.

## 9. Open decisions for the user

- The 8th lab given the all-replica `/metrics` loop,
  `inference/configure-inference-routing-with-vllm`, has no spec yet, so its loop
  is **unverified** — the only edit made without being able to run it.
- Whether to add a `.gitignore` bullet to the changelog (currently omitted, since
  it would reference a directory not in the repo).
- `llm-cost-management`'s spec leaves the `cost-management` feature flag enabled
  on the `management` release, because the lab's Cleanup does not revert the helm
  upgrade. Harmless, but it means the flag is on for every later run.

## 10. Standing instructions from the user

- Fix real lab bugs in `labs/` as they're found, add a regression assertion, and
  report the edits — this is authorized, not per-change.
- Handle inherently interactive things (Inspector, browser, OAuth consent) in the
  harness, not by editing labs.
- Destructive labs are excluded entirely; `--install-base` exists so a throwaway
  cluster is cheap if that ever changes.
- Leave everything uncommitted.

## 11. Lab bugs fixed in this pass (covered by the CHANGELOG entry)

Full list of earlier fixes is in `PLAN.md`. These five are newer:

1. **`routing/openai-streaming` documented the opposite of what happens.** Its
   Note said streaming responses carry no `usage` object and that token counts
   are only in the access logs. The gateway sets `stream_options.include_usage`
   for its own token metrics and forwards the resulting final chunk, so the
   client *does* get usage — verified by calling OpenAI directly, which sends no
   such chunk. Rewrote the Note, added the chunk to the expected output, and the
   spec now asserts its exact shape (`"choices":[],"usage":{`).
2. **`routing/configure-openai-embeddings` showed the wrong log format and made a
   false claim.** Two ~65-line expected-output blocks were pretty-printed JSON
   with `request.body`/`response.body`/`rq.headers.*` fields; the gateway emits
   logfmt. Worse, the prose said `gen_ai.operation.name` "changes based on the
   endpoint" — `chat` for completions, `embeddings` for embeddings. The
   embeddings line carries **no `gen_ai.*` fields at all**, because that route is
   `Passthrough`. Replaced both blocks with real captured output and documented
   the actual `Completions` vs `Passthrough` distinction, which is the more
   useful lesson. The spec asserts it per-log-line.
3. **`rate-limiting/local-token-rate-limiting` promised a 429 that was a coin
   flip.** A local limit is a per-replica counter and `001` deploys two replicas,
   so "you should be rate limited on the second request" held only if both
   requests hit the same pod. Replaced the single curl with a 6-request burst and
   explained the per-replica counter. The spec asserts both replicas reject.
4. **Duplicate section headings, three labs.** `configure-openai-embeddings` had
   `View access logs` and `View Access Logs`; `prompt-enrichment` and
   `local-token-rate-limiting` each had two `curl openai` sections. Headings are
   matched case-insensitively, so a spec cannot address either one — the parser
   errors with `has 2 sections named`. Renamed the more specific one in each case
   (`Compare access logs across endpoints`, `curl openai with enrichment
   applied`, `curl openai until the limit trips`).
5. **`security/tls-termination` and `security/frontend-mtls` both destroyed the
   baseline** — bug #1 all over again in two more labs. Each applied a full
   Gateway manifest named `agentgateway-proxy` with only an HTTPS `443` listener
   (dropping HTTP `8080`), then "restored" it *without*
   `infrastructure.parametersRef`, leaving the baseline permanently detached from
   `agentgateway-config`. Both now use a JSON patch to append the listener (plus
   `/spec/tls` for mTLS) and remove it on Cleanup, so nothing 001 set is
   restated. Chosen over a dedicated Gateway because a second Gateway's pods are
   labelled with *its* name, which would have broken both labs' access-log step —
   see `PLAN.md` #16 for the measurement.
6. **The promptfoo eval lab could not work at all**, and its configs errored.
   The 0.11.4 reorg moved the eval YAMLs under `labs/evaluations/` but left both
   `promptfoo eval -c evaluations/...` commands pointing at the old root path —
   command arguments, not markdown links, so the reorg's link sweep missed them.
   Separately, `openai:gpt-5.6-terra` (no `:chat:`) makes promptfoo use the
   Responses API, which the gateway's `Completions` route rejects with
   `missing field \`messages\`` — every test for that provider errored. Fixed the
   paths and pinned `openai:chat:` in all four eval configs; the spec asserts
   `0 errors`.
7. **Two ext-authz labs documented output that never appears.**
   `llm-byo-grpc-ext-authz` claimed an allowed response carries
   `x-ext-authz-check-result: allowed` — it does not, because on allow the gateway
   forwards the original request and returns the provider's response (the `denied`
   variant is visible only because the ext-authz server generates that whole
   response). `byo-opa-grpc-ext-authz` told the reader to look for `"result": false`
   in OPA's decision log, which emits compact JSON `"result":false`.
8. **`routing/llm-failover` documented a 3-request pattern with one curl**, and
   its Cleanup restarted the proxy without waiting for the rollout, so the next
   lab could start against churning pods. Both fixed.
9. **`transformations/transformations` had a no-op example.** Left over from the
   0.13.1 model rename: "this may differ from the requested model, e.g.
   `gpt-5.4-nano` → `gpt-5.4-nano`". Now shows the dated ID the lab's own
   expected output already had, and the spec asserts the two differ.
