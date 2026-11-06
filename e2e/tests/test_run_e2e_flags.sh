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
cat > "${TMP}/bin/kubectl" <<STUB
#!/usr/bin/env bash
[[ "\$1 \$2" == "config current-context" ]] && echo stub-context
[[ "\$1 \$2" == "get gateway" && -f "${TMP}/gw" ]] && cat "${TMP}/gw"
[[ "\$1 \$2" == "get namespaces" && -f "${TMP}/ns" ]] && cat "${TMP}/ns"
exit 0
STUB
chmod +x "${TMP}/bin/kubectl"
printf '#!/usr/bin/env bash\necho "helm stub: no cluster in tests" >&2; exit 1\n' > "${TMP}/bin/helm"
chmod +x "${TMP}/bin/helm"

export PATH="${TMP}/bin:${PATH}"
export E2E_WORK_ROOT="${TMP}/work"

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

printf '\n%s passed, %s failed\n' "$T_PASS" "$T_FAIL"
[[ "$T_FAIL" -eq 0 ]]
