#!/usr/bin/env bash
# Fixture tests for lib.sh assertions. Feeds synthetic captured output through
# each assert_* and checks the pass/fail counters. No cluster, no curl.
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)/lib.sh"
export E2E_WORKDIR="$(mktemp -d)"
source "$LIB"
e2e_begin

T_PASS=0; T_FAIL=0
check() {  # check <expected pass-delta> <expected fail-delta> <desc>
  local dp=$((E2E_PASS - P0)) df=$((E2E_FAIL - F0))
  if [[ "$dp" == "$1" && "$df" == "$2" ]]; then
    T_PASS=$((T_PASS+1)); printf 'ok   %s\n' "$3"
  else
    T_FAIL=$((T_FAIL+1)); printf 'FAIL %s (pass+%s fail+%s, wanted +%s/+%s)\n' "$3" "$dp" "$df" "$1" "$2"
  fi
}
snap() { P0=$E2E_PASS; F0=$E2E_FAIL; }

# -i style: status line in body
snap; E2E_LAST=$'HTTP/1.1 200 OK\ncontent-type: application/json\n\n{"ok":true}'
assert_status 200 t >/dev/null; check 1 0 "assert_status matches -i status line"

snap; assert_status 401 t >/dev/null; check 0 1 "assert_status rejects wrong code"

# curl meter glued to the status line (the documented intermittent case)
snap; E2E_LAST=$'  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0HTTP/1.1 200 OK\nbody'
assert_status 200 t >/dev/null; check 1 0 "assert_status unglues the curl meter"

# -w '%{http_code}' style: bare code as last line
snap; E2E_LAST=$'some output\n429'
assert_status 429 t >/dev/null; check 1 0 "assert_status matches bare trailing code"

# KNOWN false-positive shape, kept as documentation: a bare numeric last line
# that is data, not a status, still satisfies assert_status.
snap; E2E_LAST=$'tokens used\n200'
assert_status 200 t >/dev/null; check 1 0 "documented: trailing data line '200' passes"

snap; E2E_LAST='the quick brown fox'
assert_contains "quick" t >/dev/null; check 1 0 "assert_contains fixed-string hit"
snap; assert_contains "qu.ck" t >/dev/null; check 0 1 "assert_contains is literal, not regex"
snap; assert_not_contains "wolf" t >/dev/null; check 1 0 "assert_not_contains"
snap; assert_matches 'q.i.k' t >/dev/null; check 1 0 "assert_matches ERE"
snap; assert_not_matches '^fox' t >/dev/null; check 1 0 "assert_not_matches anchors"
snap; E2E_RC=7; assert_rc 7 t >/dev/null; check 1 0 "assert_rc equality"
snap; assert_rc 0 t >/dev/null; check 0 1 "assert_rc mismatch fails"

printf '\n%s passed, %s failed\n' "$T_PASS" "$T_FAIL"
rm -rf "$E2E_WORKDIR"
exit $((T_FAIL > 0))
