# e2e

Automated tests for the workshop labs. Each lab's markdown **is** its test: the
harness lifts the ```bash blocks out of the lab and runs them in one shell, so a
lab that stops working fails here. `--list` prints every lab with its tier and
whether it has a spec; `--lint` prints the ones that do not.

## Quick start

```bash
# fresh cluster
./e2e/run-e2e.sh --install-base --only-base

# then
./e2e/run-e2e.sh                       # everything runnable
./e2e/run-e2e.sh --tier t0             # no credentials needed
./e2e/run-e2e.sh routing/direct-response -v
./e2e/run-e2e.sh --lint                # no cluster required
```

Flags: `--install-base`, `--only-base`, `--tier <t0|t1-key|…>`, `--lint`,
`--list`, `--no-prompt`, `--verbose`, `--keep-work`.

`--list` is the authority on tiers and per-lab coverage, and `--lint` on which
labs have no spec yet. Both are derived from `e2e/specs/`, so re-run them after
any change rather than trusting a recorded total. A full run that reports fewer
passing assertions than the last one, with nothing removed, means coverage
stopped running.

`--lint` also runs `e2e/lib/conventions.py` against every README/tracks/labs
markdown file. It checks four things: relative `.md` links resolve, every
`export GATEWAY_IP=$(kubectl get svc ...)` block matches the canonical form in
style-guide.md §6 byte-for-byte (labs with a legitimate different Gateway are
allowlisted in `GATEWAY_IP_ALLOW`), every lab has a `Cleanup` section at heading level 2 or 3
(reference docs and installers are allowlisted in `CLEANUP_ALLOW`), and no lab
uses a bare `kind: AgentgatewayBackend` instead of the Enterprise CRD.

Requires bash ≥ 4, `python3` + PyYAML, `kubectl`, `helm`, `jq` (`k6` for the
load-testing labs).

## Adding a lab

1. See what the harness sees:

   ```bash
   python3 e2e/lib/labdoc.py sections labs/routing/my-lab.md
   ```

   This prints every heading with its bash-block count. Block ordinals in the
   spec are 1-based **within a section**.

2. Write `e2e/specs/routing/my-lab.yaml`:

   ```yaml
   lab: labs/routing/my-lab.md
   description: one line, shown next to the lab name in output
   tier: t0
   requires: []
   timeout: 300

   steps:
     - section: "Create the route"
       run: all
       wait: "--for=condition=Available deploy/x -n ns --timeout=300s"

     - section: "Send a request"
       retry: {attempts: 8, delay: 3, until: "HTTP/[0-9.]+ 200"}
       assert:
         - status: 200
         - contains: "expected text"

   cleanup:
     section: "Cleanup"
   ```

3. `./e2e/run-e2e.sh --lint` then `./e2e/run-e2e.sh routing/my-lab -v`.

Assertions: `status`, `contains`, `not_contains`, `matches`, `not_matches`, `rc`.
All apply to the combined stdout+stderr of the step's last block.

`run:` is `all`, `none`, or 1-based ordinals within the section (`run: [1, 3]`).
`assert:`, `wait:`, and `retry:` all attach to the step's **last** block, so a
section that opens with `kubectl create namespace` waits after its final block,
not its first. To assert on several blocks of one section, write several steps
against the same section. `run: none` requires a `reason:`, which surfaces as
SKIP so the gap stays visible.

Write `retry.until` to match what the last block prints, and keep it no stricter
than the step's own assertion. A guard demanding `proxied_completions=[2-9]`
under an assertion of `[2-9]|[0-9]{2,}` can never match a two-digit count, so it
spins out its whole budget and protects nothing.

Reaching `attempt N/N` in the log does not mean the guard ever matched: the loop
in `lib.sh` breaks whether the regex matched or the budget ran out, and the step
then passes on its assertions either way. Audit rather than read the counters.
For each `e2e_run_block` in `.work/*/*/driver.sh` carrying `attempts > 1`, grep
its `until` regex against the sibling `.out`; a guard that never matched is dead.

Field reads use `resource:` instead of a probe:

```yaml
- resource: deploy/agentgateway-proxy
  jsonpath: .status.readyReplicas
  expect: "2"                # exact match after polling
  namespace: agentgateway-system   # default; shown for clarity
  name: proxy is scaled out        # optional label
```

Reserve `probe:` for genuinely imperative verification (MCP protocol
drives, browser-verified labs, multi-step curl flows).

## Things that will bite you

**Expected-output blocks are fenced ```bash in many labs.** `run: all` would try
to execute a Kubernetes table. Check `labdoc.py sections` counts and use explicit
ordinals: `run: [1, 2]`.

**Foreground `port-forward` blocks never return.** Set `run: none` with a
`reason:`, then write a probe using the helpers:

```yaml
  - probe: "the UI answers over its Service"
    script: |
      e2e_pf agentgateway-system svc/solo-enterprise-ui 14000:80
      echo "code=$(curl -s -o /dev/null -w '%{http_code}' localhost:14000)"
      e2e_pf_stop
    assert:
      - contains: "code=200"
```

Use `e2e_pf`/`e2e_pf_stop` rather than a raw `kubectl port-forward &` plus
`kill`/`wait` — `wait` on a killed job returns 143, which becomes the block's exit
code and fails the step even when every assertion passed.

**Blocks share one shell.** A lab that exports `GATEWAY_IP` or a `SESSION` id in
one block and uses it later works as written. Functions a probe defines are
visible to later probes too (the MCP spec defines `mcp_init`/`mcp_rpc` once).

**Anything asynchronous needs `retry`.** Route programming, policy attachment, and
label-selector backend discovery all complete after `kubectl apply` returns.

**Drivers run with no `set -e`, `-u`, or `-o pipefail`,** deliberately, to match
a reader's terminal. `pipefail` breaks `cmd | grep -q`: grep exits at the first
match, the producer takes SIGPIPE, and the pipeline reports failure. It only
shows up on large output such as a 128KB `/metrics` dump, so it presents as a
flake. Assertions use here-strings for the same reason, never `printf | grep -q`.

**curl's progress meter can land inside a response body.** It goes to stderr with
`\r` and no trailing newline, so a merged capture can yield
`"total_tokens":<CR>100 16966 ... 14009<CR>91,` and fail
`matches: '"total_tokens":[0-9]+'` on a body that is correct. `e2e_run_block`
captures the two streams separately and concatenates stderr-then-stdout with a
newline between them, which keeps stdout last for `assert_status`'s
`-w '%{http_code}'` branch. Never repair a merged capture by stripping the `\r`:
the regex then matches the meter's own digits and passes for the wrong reason.
A lab whose printed output is part of the lesson should pass
`--no-progress-meter` in its own curl.

**BSD `wc -c` pads with spaces.** Match `[[:space:]]+[0-9]+ bytes`, not a single
space.

**Namespace `mcp` is shared by six labs, so no lab's Cleanup may delete it.**
Deleting it leaves the namespace `Terminating` while the next lab deploys into
it. The idempotent `kubectl create namespace mcp --dry-run=client -o yaml |
kubectl apply -f -` already covers re-runnability. Lab-private namespaces such
as `stripe-mcp` and `composable-mcp` are deleted by their own labs.

**Renamed a lab heading?** The spec fails loudly with `DRIFT: ... has no section`.
That is intended — fix the spec, don't loosen the matcher.

**A lab that isn't re-runnable** (e.g. a bare `kubectl create namespace` whose
Cleanup doesn't delete the namespace) needs `allow_failure: true` on that block.
Prefer fixing the lab; if you tolerate it, say why in a comment so the gap stays
visible.

**Two labs cannot share a heading, even across letter case.** Headings are
matched case-insensitively, so a lab with both `View access logs` and
`View Access Logs` — or two `curl openai` sections — makes neither addressable
and the parser raises `has 2 sections named`. That is a real defect in the lab;
rename the more specific one rather than working around it.

**A lab that writes files writes them to the repo root**, which is where drivers
`cd`. Open the spec with a probe that moves to the per-lab work dir:

```yaml
  - probe: "keep generated artifacts out of the repo"
    script: |
      cd "$E2E_WORKDIR" && pwd
    assert:
      - contains: ".work/routing/my-lab"
```

Because blocks share one shell, that `cd` carries through every later block and
the lab's relative paths keep working unchanged. See `routing/openai-audio`.

**A heredoc containing a backslash escape must be quoted.** Lab blocks are
replayed by a real shell, so `kubectl apply -f - <<EOF` with `\\s` in a YAML
double-quoted scalar collapses to `\s` and kubectl rejects it:
`error converting YAML to JSON: found unknown escape character`. Quote it —
`<<'EOF'` — as `builtin-guardrails` does. This is a lab bug, not a harness
limitation: the reader hits it too. `security/WAF` Use Case D shipped this way.

**Never run two suite invocations against one cluster.** The labs share one
Gateway, and several attach Gateway-scoped policies while they run — `virtual-keys`
and `llm-cost-management` both apply `api-key-auth` with `mode: Strict`, which
401s *all* gateway traffic until their Cleanup removes it. A second run overlapping
that window fails in ways that look like real lab bugs: `api key authentication
failure: no API Key found` on a lab that has nothing to do with API keys.
`llm-failover` is worse in the other direction — it scales the shared proxy to one
replica, so it silently changes the environment every concurrently-running lab
sees. Check with `pgrep -f run-e2e.sh` before starting, and treat any run that
overlapped another as void rather than debugging its failures.

**Rate-limit and budget state outlives a run.** Counters live in `ext-cache`
Redis for the whole window, so a budget lab run twice inside an hour starts
exhausted and its isolation assertions fail for the wrong reason. Open the spec
with the lab's own documented reset
(`kubectl rollout restart deployment/ext-cache-enterprise-agentgateway -n
agentgateway-system`). See `security/virtual-keys`. `entRateLimit` counts in a
fixed wall-clock minute, so retry the whole burst instead of asserting on a
single call, and allow ~10s of settle after the policy attaches.

**The baseline-integrity guard fingerprints the shared Gateway** on
`listeners[*].port` plus `infrastructure.parametersRef.name`, before and after
every lab. It names the lab that changed either one and re-patches the
`parametersRef`; leftover HTTPRoutes and policies are reported for manual
cleanup. A full `kubectl apply` of the shared Gateway drops `parametersRef`,
which detaches the params and freezes the proxy replica count. A lab that needs
its own listener should patch one in and remove it on Cleanup, as
`security/tls-termination` and `security/frontend-mtls` do.

**Some blocks must never run from the suite.** Give each `run: none` with a
`reason:`, or replace it with a probe:

- `claude mcp add` / `claude mcp remove` (`remote-mcp` Cleanup) rewrite the
  operator's own Claude Code config.
- Placeholder-bearing blocks (`<your-fred-key>`, `--version <new-version>`) need
  a probe that uses the resolved env var instead.
- Foreground `kubectl port-forward` and `kubectl logs --follow` never return.
- 002's optional `GRAFANA_ADMIN_PASSWORD` export sets the Grafana password to the
  literal string `your-secure-password`.

## MCP labs

`lib.sh` exports `e2e_mcp_init`, `_rpc`, `_tool`, `_tools`, `_status`, and
`_expect`; use them rather than hand-rolling JSON-RPC. `e2e_mcp_expect <needle>
present|absent <cmd...>` polls, which took `in-cluster-mcp` from 77s to 51s.

- The gateway answers MCP over `text/event-stream`, so bodies arrive as
  `data: {...}`. Strip the prefix with `sed -n 's/^data: //p'` before any JSON
  parse.
- Upstream JSON inside MCP content arrives escaped, so assert on
  `\"object\":\"list\"`.
- With `sessionRouting: Stateless` the gateway issues no session id. An empty
  `$SID` is correct there; never assert on its value.
- `agentgateway_*` metrics render as `name{labels} value`, so match
  `agentgateway_[a-z_]+[{ ]`. A trailing space alone fails.

## Debugging

Every block and its captured output is kept under `e2e/.work/<lab>/`:

```
e2e/.work/mcp/in-cluster-mcp/
  driver.sh          the generated bash driver — read this first
  blocks/0007.sh     the exact block that ran
  blocks/0007.out    its combined output
  result             pass/fail/skip counts
```

`-v` streams block output live. To see the driver without running it:

```bash
python3 e2e/lib/labdoc.py plan labs/mcp/in-cluster-mcp.md \
  e2e/specs/mcp/in-cluster-mcp.yaml /tmp/wd
```

## Testing the harness itself

The harness has its own tests, no cluster needed:

```bash
python3 -m unittest discover -s e2e/tests   # labdoc.py parse/plan logic
bash e2e/tests/test_assertions.sh           # lib.sh assertion semantics
```

Run both after changing `e2e/lib/`.

## Credentials

Resolved per variable: environment → `e2e/.env.local` → your shell rc files (only
the matching `export VAR=` line is evaluated, never the whole file) → interactive
prompt → skip. Prompted values are cached to `e2e/.env.local` (`chmod 600`,
gitignored). All resolution happens before the first lab runs, so prompts come in
one batch. `--no-prompt` makes it CI-safe; unresolved keys mean the lab reports
`SKIP — missing: VAR` rather than passing quietly.
