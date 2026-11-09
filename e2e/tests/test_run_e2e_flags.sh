#!/usr/bin/env bash
# Tests for run-e2e.sh's --track / --env / --resolve-creds flags and the
# per-lab outcome files. Offline: kubectl and helm are stubs, and
# --resolve-creds exits before any lab runs.
E2E="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="${E2E}/run-e2e.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# Hermetic: never read the developer's e2e/.env.local or shell rc files.
# PYTHONUSERBASE keeps a user-site PyYAML importable after HOME moves.
export PYTHONUSERBASE="${PYTHONUSERBASE:-$(python3 -m site --user-base 2>/dev/null)}"
export E2E_ENV_LOCAL=/dev/null HOME="$TMP"
mkdir -p "${TMP}/bin" "${TMP}/work"
# The Gateway fingerprint answers from ${TMP}/gw when it exists, so a fixture
# probe can simulate baseline drift by rewriting that file.
# E2E_STUB_GET_FAIL=1 makes every `kubectl get` print what it has and exit 1.
# `kubectl get -n <ns> ...` answers from ${TMP}/snap-<ns> when it exists.
cat > "${TMP}/bin/kubectl" <<STUB
#!/usr/bin/env bash
if [[ -n "\${E2E_STUB_GET_FAIL:-}" && "\$1" == "get" ]]; then
  [[ "\$2" == namespaces,* && -f "${TMP}/snap" ]] && sort "${TMP}/snap"
  exit 1
fi
[[ "\$1 \$2" == "get -n" && -f "${TMP}/snap-\$3" ]] && sort "${TMP}/snap-\$3"
[[ "\$1 \$2" == "config current-context" ]] && echo stub-context
[[ "\$1 \$2" == "get gateway" && -f "${TMP}/gw" ]] && cat "${TMP}/gw"
[[ "\$1 \$2" == "get namespaces" && -f "${TMP}/ns" ]] && cat "${TMP}/ns"
[[ "\$1" == "get" && "\$2" == namespaces,* && -f "${TMP}/snap" ]] && sort "${TMP}/snap"
exit 0
STUB
chmod +x "${TMP}/bin/kubectl"
printf '#!/usr/bin/env bash\necho "helm stub: no cluster in tests" >&2; exit 1\n' > "${TMP}/bin/helm"
chmod +x "${TMP}/bin/helm"

export PATH="${TMP}/bin:${PATH}"
export E2E_WORK_ROOT="${TMP}/work"
export E2E_TEST_SNAP="${TMP}/snap"

T_PASS=0; T_FAIL=0
ok()   { T_PASS=$((T_PASS+1)); printf 'ok   %s\n' "$1"; }
fail() { T_FAIL=$((T_FAIL+1)); printf 'FAIL %s\n' "$1"; }

run() {  # run <desc> <expected rc> <expected output substring> <args...>
  local desc="$1" erc="$2" want="$3"; shift 3
  local out rc=0
  out="$("$RUNNER" "$@" 2>&1)" || rc=$?
  if [[ "$rc" == "$erc" && "$out" == *"$want"* ]]; then ok "$desc"
  else fail "$desc (rc=$rc, wanted $erc; output: $(printf '%s' "$out" | tail -3))"; fi
}

run "--track and --tier are mutually exclusive" 1 \
  "--track and --tier are mutually exclusive" --track llm --tier t0 --no-prompt
run "--track rejects a positional spec" 1 \
  "mutually exclusive" --track llm routing/direct-response --no-prompt
run "unknown track" 1 "no track named nope" --track nope --no-prompt
run "--uninstall-base rejects --track" 1 \
  "--uninstall-base and --track are mutually exclusive" --uninstall-base --track llm --no-prompt
run "--uninstall-base rejects --install-base" 1 \
  "--uninstall-base and --install-base are mutually exclusive" --uninstall-base --install-base --no-prompt
run "--uninstall-base rejects --tier" 1 \
  "--uninstall-base and --tier are mutually exclusive" --uninstall-base --tier t0 --no-prompt
run "--uninstall-base rejects --resolve-creds" 1 \
  "--uninstall-base and --resolve-creds are mutually exclusive" --uninstall-base --resolve-creds --no-prompt
run "--uninstall-base rejects a positional spec" 1 \
  "--uninstall-base and a positional spec are mutually exclusive" --uninstall-base routing/direct-response --no-prompt
run "--uninstall-base rejects --report-only" 1 \
  "--uninstall-base and --report-only are mutually exclusive" --uninstall-base --report-only
run "--uninstall-base rejects --report" 1 \
  "--uninstall-base and --report are mutually exclusive" --uninstall-base --report --no-prompt
run "--uninstall-base rejects --only-base" 1 \
  "--uninstall-base and --only-base are mutually exclusive" --uninstall-base --only-base --no-prompt
run "--report-only rejects --install-base" 1 \
  "--report-only and --install-base are mutually exclusive" --report-only --install-base
run "--skip needs a key" 1 "--skip needs a lab key" --skip
run "--no-prompt banner names .env.local and rc files, no prompt" 0 "(env → e2e/.env.local → shell rc)" \
  --track llm --env gke --resolve-creds --no-prompt
run "--resolve-creds on a track exits 0" 0 "Resolving credentials" \
  --track llm --env gke --resolve-creds --no-prompt

if [[ -z "$(find "${TMP}/work" -name driver.sh)" ]]; then ok "no driver.sh created"
else fail "driver.sh created during --resolve-creds"; fi

# Every not-applicable and no-spec entry has an outcome file.
checked=0; bad=0
while IFS=$'\t' read -r key status _; do
  want=""
  case "$status" in
    no-spec)          want="status=no-spec rc=0 duration=0 requires=" ;;
    not-applicable:*) want="status=not-applicable rc=0 duration=0 requires= only_on=${status#not-applicable:}" ;;
    *) continue ;;
  esac
  checked=$((checked+1))
  got="$(cat "${TMP}/work/${key}/outcome" 2>/dev/null)"
  [[ "$got" == "$want" ]] || { bad=$((bad+1)); printf '  %s: wanted "%s", got "%s"\n' "$key" "$want" "$got"; }
done < <(python3 "${E2E}/lib/tracks.py" list llm --env gke)
if (( checked > 0 && bad == 0 )); then ok "outcome files for $checked non-run entries"
else fail "outcome files ($checked checked, $bad wrong)"; fi

# Duplicate entries get no outcome file of their own: the key is also a `run`
# entry, and --resolve-creds runs nothing, so no outcome may exist for it.
mkdir -p "${TMP}/work/security/virtual-keys"
echo "status=pass stale" > "${TMP}/work/security/virtual-keys/outcome"
mkdir -p "${TMP}/work/routing/configure-routing-anthropic"
echo "status=pass stale" > "${TMP}/work/routing/configure-routing-anthropic/outcome"
run "--resolve-creds rerun" 0 "Resolving credentials" \
  --track llm --env gke --resolve-creds --no-prompt
[[ ! -e "${TMP}/work/security/virtual-keys/outcome" ]] \
  && ok "stale outcome for run/duplicate key deleted" || fail "stale run-entry outcome survived"
[[ "$(cat "${TMP}/work/routing/configure-routing-anthropic/outcome")" == "status=no-spec rc=0 duration=0 requires=" ]] \
  && ok "stale no-spec outcome rewritten" || fail "no-spec outcome not rewritten"

# Failures from tracks.py itself are surfaced, not relabelled.
REALPY="$(command -v python3)"
cat > "${TMP}/bin/pywrap" <<WRAP
#!/usr/bin/env bash
for a in "\$@"; do
  [[ "\$a" == *tracks.py ]] && { echo "tracks.py: boom, tracks dir unreadable" >&2; exit 2; }
done
exec "$REALPY" "\$@"
WRAP
chmod +x "${TMP}/bin/pywrap"
E2E_PYTHON="${TMP}/bin/pywrap" run "tracks.py failure is shown, not relabelled" 1 "boom, tracks dir unreadable" \
  --track llm --no-prompt
out="$(E2E_PYTHON="${TMP}/bin/pywrap" "$RUNNER" --track llm --no-prompt 2>&1)"
[[ "$out" != *"no track named"* ]] && ok "failure not reported as unknown track" || fail "failure mislabelled as unknown track"

# --env default: unknown without env.json, env.json's class when present.
run "--env defaults to unknown" 0 "Env: unknown" --track mcp --resolve-creds --no-prompt
echo '{"class":"gke"}' > "${TMP}/work/env.json"
run "--env defaults to env.json class" 0 "Env: gke" --track mcp --resolve-creds --no-prompt
run "--env flag overrides env.json" 0 "Env: eks" --track mcp --env eks --resolve-creds --no-prompt
rm -f "${TMP}/work/env.json"

# run_lab outcome contract, via fixture specs (E2E_SPEC_DIR) that reuse a real lab.
FIX="${TMP}/specs"; mkdir -p "${FIX}/fx"
LAB="labs/routing/direct-response.md"
mkfix() {  # mkfix <name> <tier> <requires> <timeout> <probe script> <assert>
  cat > "${FIX}/fx/$1.yaml" <<YAML
lab: ${LAB}
description: fixture $1
tier: $2
requires: [$3]
timeout: $4
steps:
  - probe: "fixture probe"
    script: |
      $5
    assert:
      - contains: "$6"
YAML
}
mkfix excluded  excluded ""        30 "echo hi" hi
mkfix skipme    t1-key   NOPE_KEY,NOPE_TWO 30 "echo hi" hi
mkfix passme    t0       ""        30 "echo hi" hi
mkfix failme    t0       ""        30 "echo hi" nope
mkfix timeoutme t0       ""        1  "sleep 30" hi
outcome_of() { cat "${TMP}/work/fx/$1/outcome" 2>/dev/null; }
for spec in excluded skipme passme failme timeoutme; do
  E2E_SPEC_DIR="$FIX" "$RUNNER" "fx/${spec}" --no-prompt >/dev/null 2>&1
done
for pair in excluded:excluded skipme:skip passme:pass failme:fail timeoutme:timeout; do
  name="${pair%%:*}"; want="${pair##*:}"; got="$(outcome_of "$name")"
  [[ "$got" == "status=${want} rc="* ]] && ok "outcome status=${want} (${name})" \
    || fail "outcome ${name}: wanted status=${want}, got '${got}'"
done
[[ "$(outcome_of skipme)" == *"requires=NOPE_KEY,NOPE_TWO" ]] \
  && ok "requires is comma-joined" || fail "requires not comma-joined: $(outcome_of skipme)"

# --skip: a declined lab is recorded, not run, for positional and --track runs.
rm -rf "${TMP}/work/fx/passme"
out="$(E2E_SPEC_DIR="$FIX" "$RUNNER" fx/passme --skip fx/passme --no-prompt 2>&1)"
[[ "$(outcome_of passme)" == "status=skip rc=0 duration=0 requires= reason=declined" ]] \
  && ok "--skip writes a declined skip outcome" || fail "--skip outcome: '$(outcome_of passme)'"
[[ ! -e "${TMP}/work/fx/passme/driver.sh" ]] && ok "--skip runs nothing" || fail "--skip ran the lab"
[[ "$out" == *"(declined)"* ]] && ok "--skip prints declined" || fail "--skip output: $out"
tkey="$(python3 "${E2E}/lib/tracks.py" list mcp --env vind | awk -F'\t' '$2=="run"{print $1; exit}')"
"$RUNNER" --track mcp --env vind --skip "$tkey" --resolve-creds --no-prompt >/dev/null 2>&1
out="$("$RUNNER" --track mcp --env vind --skip "$tkey" --skip other/key --resolve-creds --no-prompt 2>&1)"; rc=$?
[[ $rc == 0 ]] && ok "--skip is repeatable with --track" || fail "--skip repeat rc=$rc: $out"

# E2E_SPEC_TIMEOUT in the environment overrides the spec's timeout.
mkfix slowpass t0 "" 30 "sleep 5; echo hi" hi
E2E_SPEC_TIMEOUT=1 E2E_SPEC_DIR="$FIX" "$RUNNER" fx/slowpass --no-prompt >/dev/null 2>&1
[[ "$(outcome_of slowpass)" == "status=timeout rc=124"* ]] && ok "E2E_SPEC_TIMEOUT overrides the spec timeout" \
  || fail "slowpass outcome: '$(outcome_of slowpass)'"

# Cleanup blocks that fail are tolerated but land in leftovers.txt and the outcome.
cat > "${FIX}/fx/cleanfail.yaml" <<YAML
lab: ${LAB}
description: fixture cleanfail
tier: t0
requires: []
timeout: 30
steps:
  - probe: "fixture probe"
    script: |
      echo hi
    assert:
      - contains: "hi"
cleanup:
  - probe: "failing cleanup"
    script: |
      false
YAML
rm -f "${TMP}/work/leftovers.txt"
E2E_SPEC_DIR="$FIX" "$RUNNER" fx/cleanfail --no-prompt >/dev/null 2>&1
[[ "$(outcome_of cleanfail)" == "status=pass "*"cleanup_fail=1" ]] \
  && ok "failed cleanup block recorded in the outcome" || fail "cleanfail outcome: '$(outcome_of cleanfail)'"
grep -qx "CLEANUP BLOCK FAILED: fx/cleanfail" "${TMP}/work/leftovers.txt" 2>/dev/null \
  && ok "failed cleanup block listed in leftovers.txt" || fail "leftovers: $(cat "${TMP}/work/leftovers.txt" 2>/dev/null)"

# Baseline drift in a single-lab run is persisted for the report.
mkfix drifter t0 "" 30 "echo '80|changed' > ${TMP}/gw; echo hi" hi
echo "80|agentgateway-config" > "${TMP}/gw"
rm -f "${TMP}/work/leftovers.txt"
E2E_SPEC_DIR="$FIX" "$RUNNER" fx/drifter --no-prompt >/dev/null 2>&1
rm -f "${TMP}/gw"
grep -qx "BASELINE DRIFT: fx/drifter" "${TMP}/work/leftovers.txt" 2>/dev/null \
  && ok "single-lab drift written to leftovers.txt" || fail "leftovers: $(cat "${TMP}/work/leftovers.txt" 2>/dev/null)"
[[ "$(outcome_of drifter)" == "status=fail "*"drift=1" ]] && ok "drift=1 in the outcome" || fail "drifter outcome: '$(outcome_of drifter)'"

# --uninstall-base lists namespaces that were not in detect-env's snapshot.
mkdir -p "${FIX}/_base"
for n in 901-uninstall-monitoring 902-uninstall-agentgateway; do
  cat > "${FIX}/_base/${n}.yaml" <<YAML
lab: ${LAB}
description: fixture ${n}
tier: t0
requires: []
timeout: 30
steps:
  - probe: "noop"
    script: |
      echo ok
YAML
done
echo '{"class":"vind","namespaces":["default","kube-system"]}' > "${TMP}/work/env.json"
printf 'default Active\nkube-system Active\nagw-evaluator Active\nmcp Active\n' > "${TMP}/ns"
rm -f "${TMP}/work/leftovers.txt"
out="$(E2E_SPEC_DIR="$FIX" "$RUNNER" --uninstall-base 2>&1)"; rc=$?
[[ $rc == 1 && "$out" == *"LEFTOVER NAMESPACE: mcp (Active)"* ]] && ok "uninstall names a leftover namespace and exits 1" \
  || fail "uninstall (rc=$rc): $(printf '%s' "$out" | tail -5)"
[[ "$out" != *"LEFTOVER NAMESPACE: agw-evaluator"* ]] && ok "agw-evaluator is not a leftover" || fail "agw-evaluator flagged"
grep -qx "LEFTOVER NAMESPACE: mcp (Active)" "${TMP}/work/leftovers.txt" 2>/dev/null \
  && ok "leftover namespace written to leftovers.txt" || fail "leftovers: $(cat "${TMP}/work/leftovers.txt" 2>/dev/null)"
echo '{"class":"vind"}' > "${TMP}/work/env.json"
rm -f "${TMP}/work/leftovers.txt"
out="$(E2E_SPEC_DIR="$FIX" "$RUNNER" --uninstall-base 2>&1)"
grep -q "NAMESPACE CHECK NOT RUN" "${TMP}/work/leftovers.txt" 2>/dev/null \
  && ok "missing snapshot is reported, not passed silently" || fail "no snapshot: $(cat "${TMP}/work/leftovers.txt" 2>/dev/null)"
rm -f "${TMP}/work/env.json" "${TMP}/ns" "${TMP}/work/leftovers.txt"
rm -rf "${FIX}/_base" "${TMP}/work/_base"

# --report / --report-only: flag parsing and the no-run path.
export E2E_CHART_VERSION=v-test
RPT="${TMP}/reports"
mkdir -p "${TMP}/work/routing/rpt"
echo "status=pass rc=0 duration=5 requires=" > "${TMP}/work/routing/rpt/outcome"
out="$("$RUNNER" --report-only="$RPT" --env vind 2>&1)"; rc=$?
if [[ $rc == 0 && "$out" == *"-vind-adhoc.md"* && "$out" == *"-vind-adhoc.json"* ]]; then ok "--report-only=<dir> prints both report paths"
else fail "--report-only (rc=$rc): $out"; fi
MD="$(printf '%s\n' "$out" | grep '\.md$')"
[[ -f "$MD" ]] && ok "--report-only wrote the Markdown" || fail "no markdown written"
grep -q "routing/rpt" "$MD" 2>/dev/null && ok "--report-only reads existing outcome files" || fail "outcome not in report"
run "--report-only --track mcp builds the mcp report" 0 "-vind-mcp.md" --report-only="$RPT" --track mcp --env vind
(cd "$TMP" && "$RUNNER" --report-only=out --env vind >/dev/null 2>&1)
ls "${TMP}"/out/*-vind-adhoc.md >/dev/null 2>&1 && ok "--report-only=out creates a relative dir" || fail "relative dir not created"
run "--report-only rejects a positional argument" 2 "--report-only takes no positional argument" --report-only routing/rpt
run "--report-only rejects a bare directory word" 2 "--report-only takes no positional argument" --report-only "$RPT"
[[ ! -d "${TMP}/work/.run.lock" ]] && ok "--report-only takes no lock" || fail "lock left behind"
run "--report with a bare spec keeps the spec positional" 1 "No spec at" --report routing/nope --no-prompt
run "--report --resolve-creds exits before the report" 0 "Resolving credentials" \
  --track llm --env gke --report --resolve-creds --no-prompt

# Stale outcomes: a track run clears them, a single-lab run leaves other labs alone.
mkdir -p "${TMP}/work/stale/lab"
echo "status=pass rc=0 duration=1 requires=" > "${TMP}/work/stale/lab/outcome"
echo "BASELINE DRIFT: old" > "${TMP}/work/leftovers.txt"
"$RUNNER" --track mcp --env vind --resolve-creds --no-prompt >/dev/null 2>&1
[[ ! -e "${TMP}/work/stale/lab/outcome" && ! -e "${TMP}/work/leftovers.txt" ]] \
  && ok "--track run clears stale outcomes and leftovers" || fail "track run left stale outcome/leftovers"
mkdir -p "${TMP}/work/stale/lab"
echo "status=pass rc=0 duration=1 requires=" > "${TMP}/work/stale/lab/outcome"
E2E_SPEC_DIR="$FIX" "$RUNNER" fx/passme --no-prompt >/dev/null 2>&1
[[ -e "${TMP}/work/stale/lab/outcome" ]] && ok "single-lab run leaves other outcomes" || fail "single-lab run cleared an outcome"

# --custom <name>: run and lint one lab under custom-labs/ (fixtures here).
export E2E_CUSTOM_ROOT="${E2E}/tests/fixtures/custom-labs"
run "--custom needs a name" 1 "--custom needs a name" --custom
run "--custom with missing spec" 1 "No custom lab at" --custom nope --no-prompt
run "--custom rejects --track" 1 "--custom and --track are mutually exclusive" --custom demo --track llm --no-prompt
run "--custom rejects --tier" 1 "--custom and --tier are mutually exclusive" --custom demo --tier t0 --no-prompt
run "--custom rejects a positional spec" 1 "--custom and a positional spec are mutually exclusive" --custom demo routing/x --no-prompt
run "--custom rejects --uninstall-base" 1 "--custom and --uninstall-base are mutually exclusive" --custom demo --uninstall-base --no-prompt
run "--custom --lint passes the demo fixture" 0 "conventions: 1 file(s), 0 violation(s)" --custom demo --lint
run "--custom --lint fails without Cleanup" 1 "no \`## Cleanup\` section" --custom nocleanup --lint
printf 'namespace/default\n' > "${TMP}/snap"  # the leftover check needs a cluster snapshot
run "--custom runs the fixture and keys it custom/demo" 0 "1 PASS" --custom demo --no-prompt
[[ -f "${E2E_WORK_ROOT}/custom/demo/outcome" ]] && grep -q "status=pass" "${E2E_WORK_ROOT}/custom/demo/outcome" \
  && ok "custom outcome at custom/demo" || fail "custom outcome at custom/demo"
run "--custom rejects a path as the name" 1 "--custom needs a name" --custom ../x --no-prompt
run "--custom rejects a flag as the name" 1 "--custom needs a name" --custom --lint
run "--custom rejects --list" 1 "--custom and --list are mutually exclusive" --custom demo --list
mkdir -p "${TMP}/badspec/broken"
printf 'not: [valid\n' > "${TMP}/badspec/broken/spec.yaml"
out="$(E2E_CUSTOM_ROOT="${TMP}/badspec" "$RUNNER" --custom broken --lint 2>&1)"; rc=$?
[[ "$rc" -eq 1 && "$out" != *"unbound variable"* ]] && ok "--custom --lint fails cleanly on a bad spec" \
  || fail "--custom --lint fails cleanly on a bad spec (rc=$rc; output: $(printf '%s' "$out" | tail -3))"

run "--keep requires --custom" 1 "--keep requires --custom" --keep routing/x --no-prompt
run "--keep and --lint are mutually exclusive" 1 "--keep and --lint are mutually exclusive" --custom demo --keep --lint
run "--custom rejects uppercase and underscores" 1 "--custom needs a name" --custom Foo_bar --no-prompt
run "--custom rejects a dot in the name" 1 "--custom needs a name" --custom a.b --no-prompt
printf 'namespace/default\n' > "${TMP}/snap"
echo "SHARED SENTINEL" > "${E2E_WORK_ROOT}/leftovers.txt"
run "a leftover fails the run" 1 "LEFTOVER: namespace/left-behind" --custom leftover --no-prompt
grep -q "^status=fail .*leftover=1" "${E2E_WORK_ROOT}/custom/leftover/outcome" \
  && ok "outcome records status=fail leftover=1" || fail "outcome: $(cat "${E2E_WORK_ROOT}/custom/leftover/outcome")"
grep -q "LEFTOVER: namespace/left-behind" "${E2E_WORK_ROOT}/custom/leftover/leftovers.txt" 2>/dev/null \
  && ok "leftover noted in the per-lab leftovers file" || fail "per-lab leftovers file"
[[ "$(cat "${E2E_WORK_ROOT}/leftovers.txt")" == "SHARED SENTINEL" ]] \
  && ok "custom run leaves the shared leftovers file alone" || fail "shared leftovers: $(cat "${E2E_WORK_ROOT}/leftovers.txt")"
# A namespaced object left in agentgateway-system: the fixture appends to the
# file named by E2E_TEST_SNAP, here the stub's agentgateway-system listing.
printf 'namespace/default\n' > "${TMP}/snap"
printf 'secret/base\n' > "${TMP}/snap-agentgateway-system"
E2E_TEST_SNAP="${TMP}/snap-agentgateway-system" run "a namespaced leftover is named with its namespace" 1 \
  "LEFTOVER: agentgateway-system namespace/left-behind" --custom leftover --no-prompt
rm -f "${TMP}/snap-agentgateway-system"
printf 'namespace/default\n' > "${TMP}/snap"
out="$(E2E_STUB_GET_FAIL=1 "$RUNNER" --custom demo --no-prompt 2>&1)"; rc=$?
[[ "$rc" -eq 1 && "$out" == *"LEFTOVER CHECK NOT RUN: custom/demo"* ]] \
  && ok "kubectl exit 1 with output fails the check" || fail "kubectl exit 1 (rc=$rc): $(printf '%s' "$out" | tail -3)"
grep -q "^status=fail .*leftover_check=not-run" "${E2E_WORK_ROOT}/custom/demo/outcome" \
  && ok "outcome records leftover_check=not-run" || fail "outcome: $(cat "${E2E_WORK_ROOT}/custom/demo/outcome")"
printf 'namespace/default\n' > "${TMP}/snap"
run "--keep skips cleanup" 0 "cleanup skipped (--keep)" --custom demo --keep --no-prompt
grep -q "keep=1" "${E2E_WORK_ROOT}/custom/demo/outcome" && ok "outcome records keep=1" || fail "outcome records keep=1"
grep -q "LEFTOVER CHECK SKIPPED" "${E2E_WORK_ROOT}/custom/demo/leftovers.txt" && ok "keep notes the skipped check" || fail "keep notes the skipped check"
[[ -f "${E2E_WORK_ROOT}/custom/demo/result" ]] && ok "--keep still writes result" || fail "--keep still writes result"
run "--track leaves custom outcomes alone" 0 "Resolving credentials" --track llm --env gke --resolve-creds --no-prompt
[[ -f "${E2E_WORK_ROOT}/custom/demo/outcome" ]] && ok "custom outcome survives --track" || fail "custom outcome survives --track"
[[ -f "${E2E_WORK_ROOT}/custom/leftover/leftovers.txt" ]] && ok "custom leftovers survive --track" || fail "custom leftovers survive --track"
unset E2E_CUSTOM_ROOT

# The track reset is anchored to the work root: a work root whose own path
# contains /custom/ still clears shipped outcomes.
W2="${TMP}/custom/work2"
mkdir -p "${W2}/routing/x" "${W2}/custom/demo"
echo "status=pass rc=0 duration=1 requires=" > "${W2}/routing/x/outcome"
echo "status=pass rc=0 duration=1 requires=" > "${W2}/custom/demo/outcome"
E2E_WORK_ROOT="$W2" "$RUNNER" --track mcp --env vind --resolve-creds --no-prompt >/dev/null 2>&1
[[ ! -e "${W2}/routing/x/outcome" ]] && ok "track reset clears shipped outcomes under a /custom/ work root" \
  || fail "track reset kept a shipped outcome under a /custom/ work root"
[[ -e "${W2}/custom/demo/outcome" ]] && ok "track reset keeps custom outcomes under a /custom/ work root" \
  || fail "track reset removed a custom outcome"

# Hardening: an empty snapshot means kubectl failed, so the check does not run.
export E2E_CUSTOM_ROOT="${E2E}/tests/fixtures/custom-labs"
rm -f "${TMP}/snap" "${E2E_WORK_ROOT}/leftovers.txt"
out="$("$RUNNER" --custom demo --no-prompt 2>&1)"; rc=$?
grep -q "LEFTOVER CHECK NOT RUN: custom/demo (cluster snapshot failed)" "${E2E_WORK_ROOT}/custom/demo/leftovers.txt" 2>/dev/null \
  && ok "empty snapshot notes LEFTOVER CHECK NOT RUN" || fail "empty snapshot: $(cat "${E2E_WORK_ROOT}/custom/demo/leftovers.txt" 2>/dev/null)"
[[ "$rc" -eq 1 ]] && ok "empty snapshot fails the run" || fail "empty snapshot rc=$rc"
[[ "$out" != *"LEFTOVER: "* ]] && ok "empty snapshot reports no LEFTOVER line" || fail "empty snapshot printed LEFTOVER"
printf 'namespace/default\n' > "${TMP}/snap"
# A caller's E2E_KEEP must not make a run skip Cleanup. Block output lands in
# the work dir, not on stdout, so look for the cleanup block's echo there.
rm -rf "${E2E_WORK_ROOT}/custom/demo"
out="$(E2E_KEEP=1 "$RUNNER" --custom demo --no-prompt 2>&1)"
grep -qs "cleanup ran" "${E2E_WORK_ROOT}"/custom/demo/blocks/*.out && [[ "$out" != *"cleanup skipped"* ]] \
  && ok "caller E2E_KEEP is ignored" \
  || fail "caller E2E_KEEP: $(printf '%s' "$out" | tail -3)"
unset E2E_CUSTOM_ROOT

mkdir -p "${TMP}/custom-labs" && cp -R "${E2E}/tests/fixtures/custom-labs/demo" "${TMP}/custom-labs/demo"
export E2E_CUSTOM_ROOT="${TMP}/custom-labs"
run "--custom --report-only writes under custom-labs/<name>/reports" 0 "/custom-labs/demo/reports/" --custom demo --report-only
unset E2E_CUSTOM_ROOT

# A missing custom lab directory is refused before anything is written.
W3="${TMP}/work3"
out="$(E2E_WORK_ROOT="$W3" E2E_CUSTOM_ROOT="${TMP}/custom-labs" "$RUNNER" --custom nope --report-only 2>&1)"; rc=$?
[[ "$rc" -eq 1 && "$out" == *"No custom lab at ${TMP}/custom-labs/nope"* ]] \
  && ok "--report-only with a missing custom lab is refused" || fail "--report-only missing lab (rc=$rc): $out"
[[ ! -e "$W3" && ! -e "${TMP}/custom-labs/nope" ]] && ok "missing custom lab creates nothing" || fail "missing custom lab created files"
out="$(E2E_WORK_ROOT="$W3" E2E_CUSTOM_ROOT="${TMP}/custom-labs" "$RUNNER" --custom nope --report --no-prompt 2>&1)"; rc=$?
[[ "$rc" -eq 1 && "$out" == *"No custom lab at"* && ! -e "$W3" ]] \
  && ok "--report with a missing custom lab is refused" || fail "--report missing lab (rc=$rc): $out"

# Custom lint: the spec's lab: must be <CUSTOM_ROOT>/<name>/<name>.md, and
# requirements are mandatory.
mkdir -p "${TMP}/lintlabs/wrong" "${TMP}/lintlabs/noreq"
sed 's|^lab: .*|lab: e2e/tests/fixtures/custom-labs/demo/demo.md|' \
  "${E2E}/tests/fixtures/custom-labs/demo/spec.yaml" > "${TMP}/lintlabs/wrong/spec.yaml"
cp "${E2E}/tests/fixtures/custom-labs/demo/demo.md" "${TMP}/lintlabs/wrong/wrong.md"
out="$(E2E_CUSTOM_ROOT="${TMP}/lintlabs" "$RUNNER" --custom wrong --lint 2>&1)"; rc=$?
[[ "$rc" -eq 1 && "$out" == *"spec lab: must be ${TMP}/lintlabs/wrong/wrong.md"* ]] \
  && ok "custom lint rejects a lab: outside <name>/<name>.md" || fail "lab path lint (rc=$rc): $(printf '%s' "$out" | tail -4)"
cp "${E2E}/tests/fixtures/custom-labs/demo/demo.md" "${TMP}/lintlabs/noreq/noreq.md"
# lab: is resolved from the repo root, so point at the temp lab relatively.
NOREQ_REL="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "${TMP}/lintlabs/noreq/noreq.md" "${E2E}/..")"
cat > "${TMP}/lintlabs/noreq/spec.yaml" <<YAML
lab: ${NOREQ_REL}
description: no requirements
tier: t0
steps:
  - section: "Say hello"
    assert:
      - contains: "hello from custom"
cleanup:
  section: "Cleanup"
YAML
out="$(E2E_CUSTOM_ROOT="${TMP}/lintlabs" "$RUNNER" --custom noreq --lint 2>&1)"; rc=$?
[[ "$rc" -eq 1 && "$out" == *"custom spec declares no requirements"* ]] \
  && ok "custom lint requires requirements" || fail "requirements lint (rc=$rc): $(printf '%s' "$out" | tail -4)"

printf '\n%s passed, %s failed\n' "$T_PASS" "$T_FAIL"
[[ "$T_FAIL" -eq 0 ]]
