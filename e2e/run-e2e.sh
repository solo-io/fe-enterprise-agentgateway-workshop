#!/usr/bin/env bash
# e2e/run-e2e.sh — E2E runner for the Enterprise Agentgateway workshop labs.
#
# Each lab's test is its own markdown: the runner lifts the bash blocks out of
# the lab file and executes them, so a lab that stops working fails its test.
# The spec files under e2e/specs/ only select sections and assert on output.
#
# Usage:
#   ./e2e/run-e2e.sh                          # every lab whose tier is runnable
#   ./e2e/run-e2e.sh --install-base           # install 001 + 002 first, then run
#   ./e2e/run-e2e.sh --install-base --only-base   # install 001 + 002 and stop
#   ./e2e/run-e2e.sh routing/direct-response  # one lab
#   ./e2e/run-e2e.sh --tier t0                # only credential-free labs
#   ./e2e/run-e2e.sh --lint                   # validate specs against labs, run nothing
#   ./e2e/run-e2e.sh --list                   # show coverage table
#
# Only one run at a time: labs share a cluster, so a second invocation is
# refused while another holds e2e/.work/.run.lock.
#
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; DIM='\033[2m'; BOLD='\033[1m'; NC='\033[0m'

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${E2E_DIR}/.." && pwd)"
SPEC_DIR="${E2E_DIR}/specs"
WORK_ROOT="${E2E_WORK_ROOT:-${E2E_DIR}/.work}"
LABDOC="${E2E_DIR}/lib/labdoc.py"

export E2E_DIR E2E_REPO_ROOT="${REPO_ROOT}"

# Bash 4+ is required (associative arrays). macOS /bin/bash is 3.2; the
# shebang picks up homebrew bash from PATH.
if (( BASH_VERSINFO[0] < 4 )); then
  printf "${RED}ERROR: bash >= 4 required (running %s). Try: brew install bash${NC}\n" "$BASH_VERSION" >&2
  exit 1
fi

# shellcheck source=lib/creds.sh
source "${E2E_DIR}/lib/creds.sh"

INSTALL_BASE=false
ONLY_BASE=false
LINT_ONLY=false
LIST_ONLY=false
TIER_FILTER=""
SPECIFIC=""
KEEP_WORK=false
export E2E_VERBOSE="${E2E_VERBOSE:-0}"

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

ORIGINAL_ARGS=("$@")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)      usage; exit 0 ;;
    --install-base) INSTALL_BASE=true; shift ;;
    --only-base)    ONLY_BASE=true; shift ;;
    --lint)         LINT_ONLY=true; shift ;;
    --list)         LIST_ONLY=true; shift ;;
    --tier)         TIER_FILTER="$2"; shift 2 ;;
    --no-prompt)    export E2E_PROMPT=0; shift ;;
    --verbose|-v)   export E2E_VERBOSE=1; shift ;;
    --keep-work)    KEEP_WORK=true; shift ;;
    --*)            printf "${RED}Unknown option: %s${NC}\n" "$1" >&2; usage >&2; exit 1 ;;
    *)              SPECIFIC="$1"; shift ;;
  esac
done

# ---------------------------------------------------------------------------
# run_with_timeout <secs> <cmd...>
#   This machine has neither `timeout` nor `gtimeout`, and a lab that hangs on
#   a port-forward would otherwise wedge the whole suite. Runs the child in its
#   own process group so backgrounded port-forwards die with it.
# ---------------------------------------------------------------------------
run_with_timeout() {
  local secs="$1"; shift
  local pid rc waited=0

  set -m
  "$@" & pid=$!
  set +m

  while kill -0 "$pid" 2>/dev/null; do
    if (( waited >= secs )); then
      kill -TERM "-${pid}" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      sleep 2
      kill -KILL "-${pid}" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      return 124
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid"; rc=$?
  return "$rc"
}

# ---------------------------------------------------------------------------
# Spec discovery
# ---------------------------------------------------------------------------
discover_specs() {
  find "${SPEC_DIR}" -name '*.yaml' -not -name '_*' | sort
}

spec_key() {  # e2e/specs/routing/direct-response.yaml -> routing/direct-response
  local rel="${1#${SPEC_DIR}/}"
  printf '%s' "${rel%.yaml}"
}

# ---------------------------------------------------------------------------
# Baseline integrity guard
#
# Labs share one cluster, so a lab that damages the 001 baseline (most easily by
# a full `kubectl apply` of the shared Gateway, which drops
# `infrastructure.parametersRef`) breaks every lab after it. Without this check
# the damage surfaces as an unrelated failure several labs later. Fingerprint
# before and after each lab, attribute drift to the lab that caused it, and
# restore so the run can continue.
# ---------------------------------------------------------------------------
BASELINE_NS=agentgateway-system
BASELINE_GW=agentgateway-proxy
BASELINE_PARAMS=agentgateway-config
declare -a BASELINE_DRIFT=()

baseline_fingerprint() {
  # Three fields: the Gateway's own spec surface, then the inventory of
  # HTTPRoutes and enterprise policies in the baseline namespace. A lab that
  # leaves a stray route or policy behind corrupts later labs just as surely
  # as one that rewrites the Gateway — the inventory makes that visible.
  local gw routes pols
  gw="$(kubectl get gateway "$BASELINE_GW" -n "$BASELINE_NS" \
    -o jsonpath='{.spec.listeners[*].port}|{.spec.infrastructure.parametersRef.name}' 2>/dev/null)"
  routes="$(kubectl get httproutes -n "$BASELINE_NS" -o name 2>/dev/null | sort | paste -sd, -)"
  pols="$(kubectl get enterpriseagentgatewaypolicies -n "$BASELINE_NS" -o name 2>/dev/null | sort | paste -sd, -)"
  # Unit separator (0x1f): a single byte that can't appear in resource names,
  # so the drift report can split the three fields reliably.
  printf '%s\x1f%s\x1f%s' "$gw" "$routes" "$pols"
}

baseline_exists() {
  # The fingerprint always carries its two separator bytes, so it is never the
  # empty string — test the Gateway field instead.
  local gw
  IFS=$'\x1f' read -r gw _ _ <<<"$1"
  [[ -n "$gw" ]]
}

baseline_restore() {
  kubectl patch gateway "$BASELINE_GW" -n "$BASELINE_NS" --type=merge -p \
    "{\"spec\":{\"infrastructure\":{\"parametersRef\":{\"group\":\"enterpriseagentgateway.solo.io\",\"kind\":\"EnterpriseAgentgatewayParameters\",\"name\":\"${BASELINE_PARAMS}\"}}}}" \
    >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
TOTAL_PASS=0; TOTAL_FAIL=0; TOTAL_SKIP=0
declare -a FAILED=() SKIPPED=() EXCLUDED=()

# ---------------------------------------------------------------------------
# run_lab <spec_file>
# ---------------------------------------------------------------------------
run_lab() {
  local spec="$1"
  local key; key="$(spec_key "$spec")"

  local meta
  meta="$(python3 "$LABDOC" meta "$spec")" || { printf "${RED}✗ %s — bad spec${NC}\n" "$key"; TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("$key"); return; }
  eval "$meta"

  local lab="${REPO_ROOT}/${E2E_SPEC_LAB}"
  if [[ ! -f "$lab" ]]; then
    printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC} — lab not found: %s\n" "$key" "$E2E_SPEC_LAB"
    TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("$key"); return
  fi

  if [[ "$E2E_SPEC_TIER" == "excluded" || "$E2E_SPEC_TIER" == "t5-manual" ]]; then
    printf "  ${DIM}—${NC} %-58s ${DIM}EXCLUDED${NC} — %s\n" "$key" "${E2E_SPEC_REASON:-not automatable}"
    EXCLUDED+=("$key"); return
  fi

  if [[ -n "$E2E_SPEC_REQUIRES" ]] && ! e2e_keys_present $E2E_SPEC_REQUIRES; then
    printf "  ${YELLOW}⊘${NC} %-58s ${YELLOW}SKIP${NC} — missing: %s\n" "$key" "$E2E_SPEC_REQUIRES"
    TOTAL_SKIP=$((TOTAL_SKIP+1)); SKIPPED+=("${key} (${E2E_SPEC_REQUIRES})"); return
  fi

  local workdir="${WORK_ROOT}/${key}"
  rm -rf "$workdir"; mkdir -p "$workdir/blocks"

  local driver="${workdir}/driver.sh"
  if ! python3 "$LABDOC" plan "$lab" "$spec" "$workdir" >"$driver" 2>"${workdir}/plan.err"; then
    printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC}\n" "$key"
    sed 's/^/      /' "${workdir}/plan.err"
    TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("$key"); return
  fi
  chmod +x "$driver"

  printf "${CYAN}${BOLD}[%s]${NC} ${DIM}%s${NC}\n" "$key" "${E2E_SPEC_DESC:-}"

  # The _base labs *establish* the baseline — 001 creates the Gateway and the
  # three observability policies — so drift is meaningless for them. Every
  # other lab is measured against what they left behind.
  local is_base=false
  [[ "$spec" == *"/_base/"* ]] && is_base=true

  local base_before="" base_after=""
  [[ "$is_base" == "false" ]] && base_before="$(baseline_fingerprint)"

  local rc=0
  run_with_timeout "$E2E_SPEC_TIMEOUT" bash "$driver" || rc=$?

  # Attribute baseline damage to the lab that caused it, while we still know
  # which lab that was.
  [[ "$is_base" == "false" ]] && base_after="$(baseline_fingerprint)"
  if [[ "$is_base" == "false" ]] && baseline_exists "$base_before" &&
     [[ "$base_after" != "$base_before" ]]; then
    printf "  ${RED}✗${NC} %-58s ${RED}BASELINE DRIFT${NC}\n" "$key"
    local gw_b routes_b pols_b gw_a routes_a pols_a
    IFS=$'\x1f' read -r gw_b routes_b pols_b <<<"$base_before"
    IFS=$'\x1f' read -r gw_a routes_a pols_a <<<"$base_after"
    [[ "$gw_b"     != "$gw_a"     ]] && printf "      gateway:  %s -> %s\n" "$gw_b" "$gw_a"
    [[ "$routes_b" != "$routes_a" ]] && printf "      routes:   %s -> %s\n" "${routes_b:-none}" "${routes_a:-none}"
    [[ "$pols_b"   != "$pols_a"   ]] && printf "      policies: %s -> %s\n" "${pols_b:-none}" "${pols_a:-none}"
    if [[ "$gw_b" != "$gw_a" ]] && baseline_restore; then
      printf "      ${YELLOW}restored the 001 Gateway so the run can continue${NC}\n"
    fi
    BASELINE_DRIFT+=("$key")
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
    FAILED+=("${key} (baseline drift)")
  fi

  if [[ "$rc" -eq 124 ]]; then
    printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC} — TIMEOUT (>%ss)\n" "$key" "$E2E_SPEC_TIMEOUT"
    TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("${key} (timeout)")
    printf "\n"; return
  fi

  local p=0 f=0 s=0
  if [[ -f "${workdir}/result" ]]; then
    # shellcheck disable=SC2046
    eval $(sed 's/\([a-z]*\)=/E2E_R_\1=/g' "${workdir}/result")
    p="${E2E_R_pass:-0}"; f="${E2E_R_fail:-0}"; s="${E2E_R_skip:-0}"
  fi

  TOTAL_PASS=$((TOTAL_PASS + p))
  TOTAL_FAIL=$((TOTAL_FAIL + f))
  TOTAL_SKIP=$((TOTAL_SKIP + s))
  [[ "$f" -gt 0 || "$rc" -ne 0 ]] && FAILED+=("$key")
  [[ "$f" -eq 0 && "$rc" -ne 0 ]] && TOTAL_FAIL=$((TOTAL_FAIL + 1))

  printf "\n"
}

# ---------------------------------------------------------------------------
# --list / --lint
# ---------------------------------------------------------------------------
if [[ "$LIST_ONLY" == "true" ]]; then
  printf "${BOLD}%-46s %-10s %s${NC}\n" "LAB" "TIER" "REQUIRES"
  while IFS= read -r spec; do
    eval "$(python3 "$LABDOC" meta "$spec")"
    printf "%-46s %-10s %s\n" "$(spec_key "$spec")" "$E2E_SPEC_TIER" "${E2E_SPEC_REQUIRES:--}"
  done < <(discover_specs)
  exit 0
fi

if [[ "$LINT_ONLY" == "true" ]]; then
  lint_fail=0
  while IFS= read -r spec; do
    eval "$(python3 "$LABDOC" meta "$spec")"
    lab="${REPO_ROOT}/${E2E_SPEC_LAB}"
    if [[ ! -f "$lab" ]]; then
      printf "${RED}✗ %s — lab not found: %s${NC}\n" "$(spec_key "$spec")" "$E2E_SPEC_LAB"
      lint_fail=1; continue
    fi
    if out="$(python3 "$LABDOC" lint "$lab" "$spec" 2>&1)"; then
      printf "${GREEN}✓${NC} %s\n" "$out"
    else
      printf "${RED}✗ %s${NC}\n" "$(spec_key "$spec")"
      printf '%s\n' "$out" | sed 's/^/    /'
      lint_fail=1
    fi
  done < <(discover_specs)

  printf "\n${BOLD}Labs without a spec:${NC}\n"
  missing=0
  while IFS= read -r lab; do
    rel="${lab#${REPO_ROOT}/}"
    grep -qsRF "lab: ${rel}" "${SPEC_DIR}" || { printf "  ${YELLOW}·${NC} %s\n" "$rel"; missing=$((missing+1)); }
  done < <(find "${REPO_ROOT}/labs" -name '*.md' | sort)
  printf "  %s lab(s) uncovered\n" "$missing"

  printf "\n${BOLD}Conventions:${NC}\n"
  python3 "${E2E_DIR}/lib/conventions.py" "${REPO_ROOT}" || lint_fail=1

  exit "$lint_fail"
fi

# ---------------------------------------------------------------------------
# --install-base
# ---------------------------------------------------------------------------
install_base() {
  printf "${BOLD}═══ Installing baseline (001 + 002) ═══${NC}\n\n"
  local spec
  for spec in "${SPEC_DIR}/_base/001-install-enterprise-agentgateway.yaml" \
              "${SPEC_DIR}/_base/002-set-up-ui-and-monitoring-tools.yaml"; do
    [[ -f "$spec" ]] || { printf "${RED}missing base spec: %s${NC}\n" "$spec"; exit 1; }
    run_lab "$spec"
  done
  if [[ "$TOTAL_FAIL" -gt 0 ]]; then
    printf "${RED}Baseline install failed — not running labs.${NC}\n"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# acquire_run_lock
#   Labs share one cluster and one Gateway, so two concurrent runs corrupt each
#   other: a lab races ahead of the baseline install, or a Gateway-scoped policy
#   from one run 401s the other. The failures land on whichever lab was unlucky
#   and look like product regressions, so refuse to start instead.
#
#   No flock(1) on macOS. mkdir is atomic on every filesystem we care about.
#   A run killed with SIGKILL leaves the directory behind, so a lock whose PID
#   is gone (or is no longer a run-e2e.sh) is stale and gets taken over.
# ---------------------------------------------------------------------------
RUN_LOCK=""
acquire_run_lock() {
  local lock="${WORK_ROOT}/.run.lock" holder
  if ! mkdir "$lock" 2>/dev/null; then
    holder="$(cat "${lock}/pid" 2>/dev/null || true)"
    if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null &&
       ps -o command= -p "$holder" 2>/dev/null | grep -q run-e2e.sh; then
      printf "${RED}Another run-e2e.sh is already running (pid %s).${NC}\n" "$holder" >&2
      printf "${DIM}  started: %s${NC}\n" "$(cat "${lock}/started" 2>/dev/null || echo unknown)" >&2
      printf "${DIM}  args:    %s${NC}\n" "$(cat "${lock}/args" 2>/dev/null || echo unknown)" >&2
      printf "\nLabs share one cluster; overlapping runs void both. Wait for it, or kill it.\n" >&2
      exit 1
    fi
    printf "${YELLOW}Clearing stale lock from pid %s${NC}\n" "${holder:-unknown}" >&2
    rm -rf "$lock"
    mkdir "$lock" 2>/dev/null || {
      printf "${RED}Could not acquire %s${NC}\n" "$lock" >&2; exit 1; }
  fi
  RUN_LOCK="$lock"
  printf '%s\n' "$$" > "${lock}/pid"
  date '+%Y-%m-%d %H:%M:%S' > "${lock}/started"
  printf '%s\n' "${ORIGINAL_ARGS[*]:-}" > "${lock}/args"
  trap 'release_run_lock' EXIT
}

release_run_lock() {
  [[ -n "$RUN_LOCK" ]] && rm -rf "$RUN_LOCK"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
START="$SECONDS"
mkdir -p "$WORK_ROOT"
acquire_run_lock
e2e_load_env_local

if [[ "$INSTALL_BASE" == "true" ]]; then
  e2e_resolve_key SOLO_TRIAL_LICENSE_KEY || {
    printf "${RED}SOLO_TRIAL_LICENSE_KEY is required for --install-base${NC}\n"; exit 1; }
  install_base
  [[ "$ONLY_BASE" == "true" ]] && { printf "${GREEN}Baseline installed.${NC}\n"; exit 0; }
fi

# Build the run list
declare -a RUN_SPECS=()
if [[ -n "$SPECIFIC" ]]; then
  cand="${SPEC_DIR}/${SPECIFIC%.yaml}.yaml"
  [[ -f "$cand" ]] || { printf "${RED}No spec at %s${NC}\n" "$cand" >&2; exit 1; }
  RUN_SPECS=("$cand")
else
  while IFS= read -r spec; do
    [[ "$spec" == *"/_base/"* ]] && continue
    if [[ -n "$TIER_FILTER" ]]; then
      eval "$(python3 "$LABDOC" meta "$spec")"
      [[ "$E2E_SPEC_TIER" == "$TIER_FILTER" ]] || continue
    fi
    RUN_SPECS+=("$spec")
  done < <(discover_specs)
fi

# Resolve every credential the run needs, up front, in one prompt batch.
declare -A NEEDED=()
for spec in "${RUN_SPECS[@]}"; do
  eval "$(python3 "$LABDOC" meta "$spec")"
  [[ "$E2E_SPEC_TIER" == "excluded" || "$E2E_SPEC_TIER" == "t5-manual" ]] && continue
  for v in $E2E_SPEC_REQUIRES; do NEEDED["$v"]=1; done
done
if (( ${#NEEDED[@]} > 0 )); then
  printf "${BOLD}Resolving credentials${NC} ${DIM}(env → shell rc → prompt)${NC}\n"
  for v in $(printf '%s\n' "${!NEEDED[@]}" | sort); do
    if e2e_resolve_key "$v"; then
      printf "  ${GREEN}✓${NC} %s\n" "$v"
    else
      printf "  ${YELLOW}⊘${NC} %s — labs needing it will skip\n" "$v"
    fi
  done
  printf "\n"
fi

printf "${BOLD}═══════════════════════════════════════════════════════════════${NC}\n"
printf "${BOLD}  Enterprise Agentgateway Workshop — Lab E2E${NC}\n"
printf "${BOLD}  Cluster: %s | Specs: %d${NC}\n" \
  "$(kubectl config current-context 2>/dev/null || echo unknown)" "${#RUN_SPECS[@]}"
printf "${BOLD}═══════════════════════════════════════════════════════════════${NC}\n\n"

for spec in "${RUN_SPECS[@]}"; do
  run_lab "$spec"
done

DURATION=$((SECONDS - START))
printf "${BOLD}═══════════════════════════════════════════════════════════════${NC}\n"
printf "${BOLD}  ${GREEN}%d PASS${NC}${BOLD} | ${RED}%d FAIL${NC}${BOLD} | ${YELLOW}%d SKIP${NC}${BOLD} | %d excluded | %ds${NC}\n" \
  "$TOTAL_PASS" "$TOTAL_FAIL" "$TOTAL_SKIP" "${#EXCLUDED[@]}" "$DURATION"
if (( ${#FAILED[@]} > 0 )); then
  printf "${BOLD}  Failed:${NC}\n"
  for t in "${FAILED[@]}"; do printf "    ${RED}✗${NC} %s\n" "$t"; done
fi
if (( ${#SKIPPED[@]} > 0 )); then
  printf "${BOLD}  Skipped:${NC}\n"
  for t in "${SKIPPED[@]}"; do printf "    ${YELLOW}⊘${NC} %s\n" "$t"; done
fi
if (( ${#BASELINE_DRIFT[@]} > 0 )); then
  printf "${BOLD}  Damaged the shared baseline (restored after each):${NC}\n"
  for t in "${BASELINE_DRIFT[@]}"; do printf "    ${RED}!${NC} %s\n" "$t"; done
fi
printf "${BOLD}═══════════════════════════════════════════════════════════════${NC}\n"

[[ "$KEEP_WORK" == "true" ]] || printf "${DIM}  block output kept in %s${NC}\n" "$WORK_ROOT"

[[ "$TOTAL_FAIL" -eq 0 ]]
