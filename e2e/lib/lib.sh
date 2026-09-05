#!/usr/bin/env bash
# e2e/lib/lib.sh — runtime for generated lab drivers.
#
# Sourced by the bash driver that labdoc.py emits. Every block from the lab
# markdown is `source`d into THIS shell, so variables the lab exports in one
# block (GATEWAY_IP, SESSION, MCP_SESSION_ID, ...) are visible to the next —
# same as a reader following the lab in one terminal.
#
# Deliberately none of `set -e`, `set -u`, `set -o pipefail`: lab blocks are
# written for an interactive shell, and the point of this harness is to run them
# the way a reader's terminal does. `pipefail` in particular breaks the very
# common `cmd | grep -q ...` idiom — grep exits on the first match, the producer
# takes SIGPIPE, and the pipeline reports failure despite the match succeeding.
# It shows up only for large outputs, so it reads as a flake. A block's exit code
# is captured and asserted on instead of aborting the run.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

E2E_PASS=0
E2E_FAIL=0
E2E_SKIP=0
E2E_BLOCKS=0
E2E_RC=0
E2E_OUT=""          # path to the last block's captured output
E2E_LAST=""         # last block's output, in memory
E2E_CLEANUP_DONE=0
E2E_VERBOSE="${E2E_VERBOSE:-0}"

e2e_begin() {
  mkdir -p "${E2E_WORKDIR}/blocks"
  : >"${E2E_WORKDIR}/result"
}

e2e_phase() {
  printf "  ${DIM}── %s ──${NC}\n" "$1"
}

e2e_note() {
  printf "  ${YELLOW}⊘${NC} %-58s ${YELLOW}SKIP${NC} — %s\n" "$1" "${2:-}"
  E2E_SKIP=$((E2E_SKIP + 1))
}

# ---------------------------------------------------------------------------
# e2e_run_block <block.sh> <label> <attempts> <delay> <until_regex> <allow_fail>
#   Sources one lab bash block in the current shell, capturing combined output.
#   Retries while <until_regex> is absent (route programming is racy: a pod can
#   be Ready before the gateway's endpoint table refreshes, yielding a 503).
# ---------------------------------------------------------------------------
e2e_run_block() {
  local block="$1" label="$2" attempts="${3:-1}" delay="${4:-2}" until_re="${5:-}" allow="${6:-0}"
  local attempt=1

  E2E_BLOCKS=$((E2E_BLOCKS + 1))
  E2E_OUT="${block%.sh}.out"

  while :; do
    # Capture the two streams separately, then join them. Merging them live
    # (`>"$E2E_OUT" 2>&1`) let curl's progress meter — stderr, `\r`-delimited,
    # no trailing newline — land in the MIDDLE of a response body:
    #   data: {..."total_tokens":<CR>100 16966 ... 14009<CR>91,...}
    # which leaves `"total_tokens":` followed by no digit, so a body assertion
    # fails depending only on response size and timing. It split a token in 84
    # block outputs across 22 labs. stdout goes LAST so `assert_status`'s
    # `tail -n1` still finds a bare `-w '%{http_code}'` number, and the
    # separating newline is required — concatenating a meter with no trailing
    # newline glues it onto stdout's first line and breaks `^HTTP/` matches.
    # shellcheck disable=SC1090
    source "$block" >"${E2E_OUT}.stdout" 2>"${E2E_OUT}.stderr"
    E2E_RC=$?
    if [[ -s "${E2E_OUT}.stderr" ]]; then
      { cat "${E2E_OUT}.stderr"; printf '\n'; cat "${E2E_OUT}.stdout"; } >"$E2E_OUT"
    else
      cat "${E2E_OUT}.stdout" >"$E2E_OUT"
    fi
    rm -f "${E2E_OUT}.stdout" "${E2E_OUT}.stderr"
    E2E_LAST="$(cat "$E2E_OUT" 2>/dev/null)"

    # Here-strings, never `printf | grep -q`: grep -q exits on the first match,
    # printf takes SIGPIPE, and `set -o pipefail` then reports the pipeline as
    # failed. Large outputs (a 128KB /metrics dump) hit it every time; small
    # ones never do, which makes it look like a flake.
    if [[ -n "$until_re" ]] && ! grep -qE -- "$until_re" <<<"$E2E_LAST"; then
      if (( attempt < attempts )); then
        attempt=$((attempt + 1))
        sleep "$delay"
        continue
      fi
    elif [[ "$E2E_RC" -ne 0 && "$allow" != "1" && $attempt -lt $attempts ]]; then
      attempt=$((attempt + 1))
      sleep "$delay"
      continue
    fi
    break
  done

  local suffix=""
  (( attempt > 1 )) && suffix=" ${DIM}(attempt ${attempt}/${attempts})${NC}"

  if [[ "$E2E_RC" -eq 0 ]]; then
    printf "  ${CYAN}▸${NC} %-58s ${DIM}ran${NC}%b\n" "$label" "$suffix"
  elif [[ "$allow" == "1" ]]; then
    printf "  ${CYAN}▸${NC} %-58s ${DIM}ran (rc=%s, tolerated)${NC}\n" "$label" "$E2E_RC"
  else
    printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC} — block exited %s\n" "$label" "$E2E_RC"
    e2e_dump_output
    E2E_FAIL=$((E2E_FAIL + 1))
  fi

  [[ "$E2E_VERBOSE" == "1" ]] && e2e_dump_output
  return 0
}

e2e_dump_output() {
  printf "${DIM}"
  head -c 4000 <<<"$E2E_LAST" | sed -n '1,40p' | sed 's/^/      /'
  printf "${NC}"
}

# ---------------------------------------------------------------------------
# e2e_wait <kubectl wait args...>
#   Retries rather than pre-checking existence: right after an apply, `kubectl
#   wait` fails with "no matching resources found" because the controller has
#   not created the Pod object yet.
# ---------------------------------------------------------------------------
e2e_wait() {
  local args="$1" attempt out
  for attempt in 1 2 3 4 5 6; do
    # shellcheck disable=SC2086
    if out="$(kubectl wait $args 2>&1)"; then
      printf "  ${CYAN}▸${NC} %-58s ${DIM}ready${NC}\n" "wait: $args"
      return 0
    fi
    printf '%s' "$out" | grep -q 'no matching resources' || break
    sleep 5
  done
  printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC} — %s\n" "wait: $args" "$(printf '%s' "$out" | head -n1)"
  E2E_FAIL=$((E2E_FAIL + 1))
}

# ---------------------------------------------------------------------------
# Port-forward helpers, available to lab blocks and probes (blocks are sourced
# into this shell, so these functions are in scope).
#
#   e2e_pf agentgateway-system svc/solo-enterprise-ui 14000:80
#   ... curl localhost:14000 ...
#   e2e_pf_stop
#
# Use these instead of a raw `kubectl port-forward &` + `kill $pf; wait $pf`:
# `wait` on a killed job returns 143, which becomes the block's exit code and
# fails the step even though every assertion passed.
# ---------------------------------------------------------------------------
E2E_PF_PID=""

e2e_pf() {
  local ns="$1" target="$2" ports="$3"
  local lport="${ports%%:*}" i

  kubectl port-forward -n "$ns" "$target" "$ports" >/dev/null 2>&1 &
  E2E_PF_PID=$!

  for i in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${lport}") 2>/dev/null; then
      exec 3<&- 2>/dev/null
      return 0
    fi
    kill -0 "$E2E_PF_PID" 2>/dev/null || break
    sleep 1
  done
  printf 'e2e_pf: port-forward to %s/%s on %s never became ready\n' "$ns" "$target" "$ports"
  return 1
}

e2e_pf_stop() {
  local pid="${1:-$E2E_PF_PID}"
  [[ -n "$pid" ]] || return 0
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  E2E_PF_PID=""
  return 0
}

# ---------------------------------------------------------------------------
# MCP helpers. Most MCP labs verify through the MCP Inspector (a browser app),
# so their specs drive the protocol over curl instead. Shared here rather than
# redefined per spec.
#
#   E2E_MCP_URL="http://$GATEWAY_IP:8080/mcp"
#   E2E_MCP_AUTH="$SOME_JWT"          # optional; omitted when empty
#   SID="$(e2e_mcp_init)"
#   e2e_mcp_rpc "$SID" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
#   e2e_mcp_tool "$SID" echo '{"message":"hi"}'
# ---------------------------------------------------------------------------
E2E_MCP_URL="${E2E_MCP_URL:-}"
E2E_MCP_AUTH="${E2E_MCP_AUTH:-}"

_e2e_mcp_headers() {
  printf '%s\n' -H 'Content-Type: application/json' \
                -H 'Accept: application/json, text/event-stream'
  [[ -n "$E2E_MCP_AUTH" ]] && printf '%s\n' -H "Authorization: Bearer ${E2E_MCP_AUTH}"
}

# e2e_mcp_init [url] — echo the session id the gateway issues, retrying while
# route/backend programming settles. Empty output means initialize failed.
e2e_mcp_init() {
  local url="${1:-$E2E_MCP_URL}" i sid hdrs
  mapfile -t hdrs < <(_e2e_mcp_headers)
  for i in $(seq 1 8); do
    sid="$(curl -s -D - -o /dev/null -X POST "$url" "${hdrs[@]}" \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}' \
      | grep -i '^mcp-session-id' | tr -d '\r' | awk '{print $2}')"
    if [[ -n "$sid" ]]; then printf '%s' "$sid"; return 0; fi
    sleep 3
  done
  return 1
}

# e2e_mcp_status [url] — echo just the HTTP status of an unauthenticated-style
# initialize, for asserting 401/403 behaviour.
e2e_mcp_status() {
  local url="${1:-$E2E_MCP_URL}" hdrs
  mapfile -t hdrs < <(_e2e_mcp_headers)
  curl -s -o /dev/null -w '%{http_code}' -X POST "$url" "${hdrs[@]}" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"e2e","version":"1"}}}'
}

# e2e_mcp_rpc <session-id> <json-body> [url]
e2e_mcp_rpc() {
  local sid="$1" body="$2" url="${3:-$E2E_MCP_URL}" hdrs
  mapfile -t hdrs < <(_e2e_mcp_headers)
  curl -s -X POST "$url" "${hdrs[@]}" -H "mcp-session-id: ${sid}" -d "$body"
}

# e2e_mcp_tool <session-id> <tool-name> <arguments-json> [url]
e2e_mcp_tool() {
  local sid="$1" name="$2" args="${3:-\{\}}" url="${4:-$E2E_MCP_URL}"
  e2e_mcp_rpc "$sid" \
    "{\"jsonrpc\":\"2.0\",\"id\":99,\"method\":\"tools/call\",\"params\":{\"name\":\"${name}\",\"arguments\":${args}}}" \
    "$url"
}

# e2e_mcp_tools <session-id> [url] — tools/list
e2e_mcp_tools() {
  e2e_mcp_rpc "$1" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' "${2:-$E2E_MCP_URL}"
}

# e2e_mcp_expect <needle> <absent|present> <cmd...> — run cmd until the needle is
# present/absent in its output, then echo that output. Policy attachment and
# label-selector discovery are async, so a single call races the control plane.
e2e_mcp_expect() {
  local needle="$1" mode="$2"; shift 2
  local i out
  for i in $(seq 1 8); do
    out="$("$@" 2>&1)"
    if [[ "$mode" == "present" ]]; then
      grep -qF -- "$needle" <<<"$out" && break
    else
      grep -qF -- "$needle" <<<"$out" || break
    fi
    sleep 4
  done
  printf '%s\n' "$out"
}

# ---------------------------------------------------------------------------
# Assertions — all operate on the last block's captured output.
# ---------------------------------------------------------------------------
_pass() { printf "  ${GREEN}✓${NC} %-58s ${GREEN}PASS${NC}\n" "$1"; E2E_PASS=$((E2E_PASS + 1)); }
_fail() {
  printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC} — %s\n" "$1" "$2"
  E2E_FAIL=$((E2E_FAIL + 1))
  e2e_dump_output
}

assert_status() {
  local want="$1" name="$2" text
  # curl writes its progress meter to stderr with \r and no trailing newline, so
  # on a large or slow response the status line lands glued to the meter:
  #   "... --:--:--     0HTTP/1.1 200 OK"
  # Whether that happens depends on response size and timing, so a line-anchored
  # match fails intermittently on the same lab. Break the meter off first, then
  # keep the anchored match.
  text="$(sed 's|\([0-9[:space:]]\)\(HTTP/[0-9]\)|\1\
\2|g' <<<"$E2E_LAST")"
  # Labs curl with -i (status line in body) or -w '%{http_code}' (bare number).
  if grep -qE "^HTTP/[0-9.]+[[:space:]]+${want}\b" <<<"$text"; then
    _pass "$name"
  elif [[ "$(tail -n1 <<<"$E2E_LAST")" == "$want" ]]; then
    _pass "$name"
  else
    local got
    got="$(grep -oE '^HTTP/[0-9.]+[[:space:]]+[0-9]{3}' <<<"$text" | head -n1)"
    _fail "$name" "expected HTTP ${want}, saw '${got:-no status line}'"
  fi
}

assert_contains() {
  if grep -qF -- "$1" <<<"$E2E_LAST"; then _pass "$2"
  else _fail "$2" "output does not contain '$1'"; fi
}

assert_not_contains() {
  if grep -qF -- "$1" <<<"$E2E_LAST"; then _fail "$2" "output unexpectedly contains '$1'"
  else _pass "$2"; fi
}

assert_matches() {
  if grep -qE -- "$1" <<<"$E2E_LAST"; then _pass "$2"
  else _fail "$2" "output does not match /$1/"; fi
}

assert_not_matches() {
  if grep -qE -- "$1" <<<"$E2E_LAST"; then _fail "$2" "output unexpectedly matches /$1/"
  else _pass "$2"; fi
}

assert_rc() {
  if [[ "$E2E_RC" == "$1" ]]; then _pass "$2"
  else _fail "$2" "expected rc $1, got $E2E_RC"; fi
}

# ---------------------------------------------------------------------------
# e2e_assert_resource_field <label> <ns> <resource> <jsonpath> <want>
#   Reads one field of one object and asserts exact equality. Polls first:
#   policy attachment and status fields settle asynchronously, so a single
#   read races the control plane.
# ---------------------------------------------------------------------------
e2e_assert_resource_field() {
  local label="$1" ns="$2" res="$3" jp="$4" want="$5" i got=""
  for i in 1 2 3 4 5 6; do
    got="$(kubectl get -n "$ns" "$res" -o jsonpath="{${jp}}" 2>/dev/null)"
    [[ "$got" == "$want" ]] && break
    sleep 2
  done
  if [[ "$got" == "$want" ]]; then _pass "$label"
  else _fail "$label" "expected '${want}', got '${got:-<empty>}'"; fi
}

# ---------------------------------------------------------------------------
# Cleanup — always runs (EXIT trap), so a failing lab still restores the
# baseline for the next one.
# ---------------------------------------------------------------------------
e2e_run_cleanup() {
  local rc=$?
  [[ "$E2E_CLEANUP_DONE" == "1" ]] && return "$rc"
  E2E_CLEANUP_DONE=1

  # Kill anything the lab backgrounded (port-forwards, tail -f).
  jobs -p 2>/dev/null | while read -r pid; do kill "$pid" 2>/dev/null; done

  if declare -F e2e_define_cleanup >/dev/null; then
    e2e_define_cleanup
  fi
  e2e_write_result
  return "$rc"
}

e2e_write_result() {
  printf 'pass=%s fail=%s skip=%s blocks=%s\n' \
    "$E2E_PASS" "$E2E_FAIL" "$E2E_SKIP" "$E2E_BLOCKS" >"${E2E_WORKDIR}/result"
}

e2e_end() {
  e2e_run_cleanup
  if [[ "$E2E_FAIL" -gt 0 ]]; then exit 1; fi
  exit 0
}
