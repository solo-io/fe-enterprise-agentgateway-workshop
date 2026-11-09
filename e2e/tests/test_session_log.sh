#!/usr/bin/env bash
# Tests for lib/session-log.sh: kinds, JSON escaping, lab "-" handling.
E2E="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${E2E}/lib/session-log.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export E2E_WORK_ROOT="${TMP}/work"
F="${E2E_WORK_ROOT}/session.jsonl"
T_PASS=0; T_FAIL=0
ok()   { T_PASS=$((T_PASS+1)); printf 'ok   %s\n' "$1"; }
fail() { T_FAIL=$((T_FAIL+1)); printf 'FAIL %s\n' "$1"; }

for kind in decision deviation question answer failure keep-base; do
  "$LOG" add "$kind" routing/a "text for $kind" 2>/dev/null
done
[[ "$(wc -l < "$F" | tr -d ' ')" == 6 ]] && ok "six valid kinds append six lines" || fail "line count"
python3 - "$F" <<'PY' && ok "each line has ts/kind/lab/text" || fail "line shape"
import json, sys
for ln in open(sys.argv[1]):
    d = json.loads(ln)
    assert sorted(d) == ["kind", "lab", "text", "ts"], d
    assert d["lab"] == "routing/a" and d["text"] == "text for " + d["kind"]
PY

rc=0; "$LOG" add bogus routing/a hi >/dev/null 2>&1 || rc=$?
[[ $rc == 2 ]] && ok "bad kind exits 2" || fail "bad kind rc=$rc"
rc=0; "$LOG" add decision routing/a >/dev/null 2>&1 || rc=$?
[[ $rc == 2 ]] && ok "missing text exits 2" || fail "missing text rc=$rc"
[[ "$(wc -l < "$F" | tr -d ' ')" == 6 ]] && ok "failed calls append nothing" || fail "failed calls appended"

"$LOG" add decision - 'He said "hi" | then \ left'
python3 - "$F" <<'PY' && ok "quotes, pipes and backslashes round-trip; lab - is empty" || fail "escaping"
import json, sys
d = json.loads(open(sys.argv[1]).read().splitlines()[-1])
assert d["text"] == 'He said "hi" | then \\ left', d
assert d["lab"] == "", d
PY
# start: rotates the old log aside; the fresh file holds only later events.
"$LOG" start >/dev/null
[[ ! -e "$F" ]] && ok "start moves session.jsonl aside" || fail "session.jsonl still present after start"
rotated=("${E2E_WORK_ROOT}"/session-*.jsonl)
[[ -f "${rotated[0]}" && "$(wc -l < "${rotated[0]}" | tr -d ' ')" == 7 ]] && ok "rotated log keeps the old events" || fail "rotated: ${rotated[*]}"
"$LOG" add keep-base - "new session"
[[ "$(wc -l < "$F" | tr -d ' ')" == 1 ]] && grep -q "new session" "$F" && ok "fresh log holds only later events" || fail "fresh log: $(cat "$F")"
"$LOG" start >/dev/null; "$LOG" add decision - x; "$LOG" start >/dev/null
[[ "$(ls "${E2E_WORK_ROOT}"/session-*.jsonl | wc -l | tr -d ' ')" == 3 ]] && ok "two starts in one second keep both logs" || fail "rotations: $(ls "${E2E_WORK_ROOT}")"
rc=0; "$LOG" start extra >/dev/null 2>&1 || rc=$?
[[ $rc == 2 ]] && ok "start takes no arguments" || fail "start extra rc=$rc"

"$LOG" add run custom/demo "status=pass" && ok "run kind accepted" || fail "run kind rejected"
"$LOG" add keep-custom custom/demo kept && ok "keep-custom kind accepted" || fail "keep-custom kind rejected"
usage="$("$LOG" add bogus - x 2>&1 || true)"
[[ "$usage" == *" run "* && "$usage" == *"keep-custom"* ]] && ok "usage lists run and keep-custom" || fail "usage: $usage"

printf '\n%s passed, %s failed\n' "$T_PASS" "$T_FAIL"
[[ "$T_FAIL" -eq 0 ]]
