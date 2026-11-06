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

# duplicate entries get none, and --env defaults to unknown without env.json.
run "--track with default env" 0 "Resolving credentials" \
  --track mcp --resolve-creds --no-prompt

printf '\n%s passed, %s failed\n' "$T_PASS" "$T_FAIL"
[[ "$T_FAIL" -eq 0 ]]
