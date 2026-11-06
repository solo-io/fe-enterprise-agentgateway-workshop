#!/usr/bin/env bash
# Tests for run-e2e.sh's --track / --env / --resolve-creds flags and the
# per-lab outcome files. Offline: kubectl is a stub, helm is absent, and
# --resolve-creds exits before any lab runs.
E2E="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="${E2E}/run-e2e.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "${TMP}/bin" "${TMP}/work"
cat > "${TMP}/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
[[ "$1 $2" == "config current-context" ]] && echo stub-context
exit 0
STUB
chmod +x "${TMP}/bin/kubectl"

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

printf '\n%s passed, %s failed\n' "$T_PASS" "$T_FAIL"
[[ "$T_FAIL" -eq 0 ]]
