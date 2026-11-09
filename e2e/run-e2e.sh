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
#   ./e2e/run-e2e.sh --uninstall-base         # remove 002 + 001, keep the Gateway API CRDs
#   ./e2e/run-e2e.sh routing/direct-response  # one lab
#   ./e2e/run-e2e.sh --tier t0                # only credential-free labs
#   ./e2e/run-e2e.sh --track llm --env gke    # every runnable entry of a use-case track
#   ./e2e/run-e2e.sh --track llm --resolve-creds  # resolve credentials, run nothing
#   ./e2e/run-e2e.sh --track llm --skip llm/x # record llm/x as SKIP (declined); repeatable
#   ./e2e/run-e2e.sh --track mcp --no-prompt --report[=dir]  # run, then write the report (default dir reports/)
#   ./e2e/run-e2e.sh --report-only[=dir] --track mcp  # build from whatever outcomes exist, run nothing;
#                                             # --track and --resolve-creds reset them, single-lab runs do not
#   ./e2e/run-e2e.sh --lint                   # validate specs against labs, run nothing
#   ./e2e/run-e2e.sh --list                   # show coverage table
#   ./e2e/run-e2e.sh --custom <name>          # run custom-labs/<name>/spec.yaml
#   ./e2e/run-e2e.sh --custom <name> --lint   # lint that one custom lab, run nothing
#   ./e2e/run-e2e.sh --custom <name> --keep   # skip its Cleanup blocks, leave the resources up
#
# E2E_SPEC_TIMEOUT=<secs> in the environment overrides every spec's timeout
# (used to force a timeout failure on a real lab).
#
# Only one run at a time: labs share a cluster, so a second invocation is
# refused while another holds e2e/.work/.run.lock.
#
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; DIM='\033[2m'; BOLD='\033[1m'; NC='\033[0m'

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${E2E_DIR}/.." && pwd)"
SPEC_DIR="${E2E_SPEC_DIR:-${E2E_DIR}/specs}"
# User-owned labs (gitignored), one directory per lab: <name>/<lab>.md + spec.yaml.
CUSTOM_ROOT="${E2E_CUSTOM_ROOT:-${REPO_ROOT}/custom-labs}"
CUSTOM_NAME=""
WORK_ROOT="${E2E_WORK_ROOT:-${E2E_DIR}/.work}"
LABDOC="${E2E_DIR}/lib/labdoc.py"

export E2E_DIR E2E_REPO_ROOT="${REPO_ROOT}"
# Only --keep sets E2E_KEEP, for the one custom lab it runs. A value from the
# caller's environment would make shipped labs skip Cleanup.
unset E2E_KEEP
# Captured before any spec's metadata is eval'd, since that sets the same name.
E2E_TIMEOUT_OVERRIDE="${E2E_SPEC_TIMEOUT:-}"

# Bash 4+ is required (associative arrays). macOS /bin/bash is 3.2; the
# shebang picks up homebrew bash from PATH.
if (( BASH_VERSINFO[0] < 4 )); then
  printf "${RED}ERROR: bash >= 4 required (running %s). Try: brew install bash${NC}\n" "$BASH_VERSION" >&2
  exit 1
fi

# labdoc.py needs PyYAML, and the python3 first on PATH can lack it (a fresh
# Homebrew python, for example). Use the first interpreter that imports yaml.
PYTHON=""
for py in "${E2E_PYTHON:-}" python3 python3.13 python3.12 python3.11 /usr/bin/python3; do
  [[ -n "$py" ]] && command -v "$py" >/dev/null 2>&1 && "$py" -c 'import yaml' 2>/dev/null \
    && { PYTHON="$py"; break; }
done
if [[ -z "$PYTHON" ]]; then
  printf "${RED}ERROR: no python3 with PyYAML found. Run 'python3 -m pip install pyyaml' or set E2E_PYTHON${NC}\n" >&2
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
KEEP=false
TRACK=""
ENV_CLASS=""
RESOLVE_CREDS=false
UNINSTALL_BASE=false
REPORT=false
REPORT_ONLY=false
REPORT_DIR=""
declare -a SKIP_KEYS=()
export E2E_VERBOSE="${E2E_VERBOSE:-0}"

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

ORIGINAL_ARGS=("$@")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)      usage; exit 0 ;;
    --install-base) INSTALL_BASE=true; shift ;;
    --only-base)    ONLY_BASE=true; shift ;;
    --uninstall-base) UNINSTALL_BASE=true; shift ;;
    --lint)         LINT_ONLY=true; shift ;;
    --list)         LIST_ONLY=true; shift ;;
    --tier)         TIER_FILTER="$2"; shift 2 ;;
    --track)        TRACK="$2"; shift 2 ;;
    --custom)       [[ "${2:-}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { printf "${RED}--custom needs a name${NC}\n" >&2; exit 1; }
                    CUSTOM_NAME="$2"; shift 2 ;;
    --env)          ENV_CLASS="$2"; shift 2 ;;
    --resolve-creds) RESOLVE_CREDS=true; shift ;;
    --skip)         [[ -n "${2:-}" ]] || { printf "${RED}--skip needs a lab key${NC}\n" >&2; exit 1; }
                    SKIP_KEYS+=("${2%.yaml}"); shift 2 ;;
    --report)         REPORT=true; shift ;;
    --report=*)       REPORT=true; REPORT_DIR="${1#--report=}"; shift ;;
    --report-only)    REPORT_ONLY=true; shift ;;
    --report-only=*)  REPORT_ONLY=true; REPORT_DIR="${1#--report-only=}"; shift ;;
    --no-prompt)    export E2E_PROMPT=0; shift ;;
    --verbose|-v)   export E2E_VERBOSE=1; shift ;;
    --keep-work)    KEEP_WORK=true; shift ;;
    --keep)         KEEP=true; shift ;;
    --*)            printf "${RED}Unknown option: %s${NC}\n" "$1" >&2; usage >&2; exit 1 ;;
    *)              SPECIFIC="$1"; shift ;;
  esac
done

if [[ "$REPORT_ONLY" == "true" && -n "$SPECIFIC" ]]; then
  printf "${RED}--report-only takes no positional argument (got %s); use --report-only=<dir> for a directory${NC}\n" "$SPECIFIC" >&2
  exit 2
fi

if [[ -n "$TRACK" ]]; then
  [[ -z "$TIER_FILTER" ]] || { printf "${RED}--track and --tier are mutually exclusive${NC}\n" >&2; exit 1; }
  [[ -z "$SPECIFIC" ]]    || { printf "${RED}--track and a positional spec are mutually exclusive${NC}\n" >&2; exit 1; }
fi

if [[ -n "$CUSTOM_NAME" ]]; then
  _cu_conflict=""
  [[ -n "$TRACK" ]]                                           && _cu_conflict="--track"
  [[ -z "$_cu_conflict" && -n "$TIER_FILTER" ]]               && _cu_conflict="--tier"
  [[ -z "$_cu_conflict" && "$UNINSTALL_BASE" == "true" ]]     && _cu_conflict="--uninstall-base"
  [[ -z "$_cu_conflict" && "$INSTALL_BASE" == "true" ]]       && _cu_conflict="--install-base"
  [[ -z "$_cu_conflict" && "$RESOLVE_CREDS" == "true" ]]      && _cu_conflict="--resolve-creds"
  [[ -z "$_cu_conflict" && -n "$SPECIFIC" ]]                  && _cu_conflict="a positional spec"
  [[ -z "$_cu_conflict" && "$LIST_ONLY" == "true" ]]          && _cu_conflict="--list"
  [[ -z "$_cu_conflict" ]] || { printf "${RED}--custom and %s are mutually exclusive${NC}\n" "$_cu_conflict" >&2; exit 1; }
fi

# --keep leaves a custom lab's resources up for inspection, so it only makes
# sense for a lab you wrote yourself.
if [[ "$KEEP" == "true" && -z "$CUSTOM_NAME" ]]; then
  printf "${RED}--keep requires --custom${NC}\n" >&2; exit 1
fi
if [[ "$KEEP" == "true" && "$LINT_ONLY" == "true" ]]; then
  printf "${RED}--keep and --lint are mutually exclusive${NC}\n" >&2; exit 1
fi

# A custom name with no directory behind it is refused before anything is
# written: no work dir, no lock, no reports directory.
if [[ -n "$CUSTOM_NAME" && ! -d "${CUSTOM_ROOT}/${CUSTOM_NAME}" ]]; then
  printf "${RED}No custom lab at %s${NC}\n" "${CUSTOM_ROOT}/${CUSTOM_NAME}" >&2; exit 1
fi

if [[ "$UNINSTALL_BASE" == "true" ]]; then
  _ub_conflict=""
  [[ -z "$TRACK" ]]                  || _ub_conflict="--track"
  [[ "$INSTALL_BASE" != "true" ]]    || _ub_conflict="--install-base"
  [[ -z "$TIER_FILTER" ]]            || _ub_conflict="--tier"
  [[ "$RESOLVE_CREDS" != "true" ]]   || _ub_conflict="--resolve-creds"
  [[ -z "$SPECIFIC" ]]               || _ub_conflict="a positional spec"
  [[ "$REPORT_ONLY" != "true" ]]     || _ub_conflict="--report-only"
  [[ "$REPORT" != "true" ]]          || _ub_conflict="--report"
  [[ "$ONLY_BASE" != "true" ]]       || _ub_conflict="--only-base"
  [[ -z "$_ub_conflict" ]] || { printf "${RED}--uninstall-base and %s are mutually exclusive${NC}\n" "$_ub_conflict" >&2; exit 1; }
fi

if [[ "$REPORT_ONLY" == "true" && "$INSTALL_BASE" == "true" ]]; then
  printf "${RED}--report-only and --install-base are mutually exclusive${NC}\n" >&2; exit 1
fi

# --env: the environment class the tracks filter on. Default to what the
# environment probe recorded, else unknown.
if [[ -z "$ENV_CLASS" ]]; then
  ENV_CLASS="unknown"
  if [[ -f "${WORK_ROOT}/env.json" ]]; then
    ENV_CLASS="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("class") or "unknown")' \
      "${WORK_ROOT}/env.json" 2>/dev/null || echo unknown)"
  fi
fi
export E2E_ENV="$ENV_CLASS"

# ---------------------------------------------------------------------------
# build_report
#   Calls report.py build with this run's track (adhoc when none), the env
#   probe output, and the session log. Prints the two file paths. With
#   --custom it builds the single-row custom report, written by default to
#   custom-labs/<name>/reports.
# ---------------------------------------------------------------------------
build_report() {
  local env_json="${WORK_ROOT}/env.json" out="${REPORT_DIR:-${REPO_ROOT}/reports}" which
  if [[ -n "$CUSTOM_NAME" ]]; then
    out="${REPORT_DIR:-${CUSTOM_ROOT}/${CUSTOM_NAME}/reports}"
    which=(--custom "$CUSTOM_NAME")
  else
    which=(--track "${TRACK:-adhoc}")
  fi
  if [[ ! -f "$env_json" ]]; then
    env_json="${WORK_ROOT}/env-report.json"
    printf '{"class": "%s"}\n' "$ENV_CLASS" > "$env_json"
  fi
  "$PYTHON" "${E2E_DIR}/lib/report.py" build --work "$WORK_ROOT" "${which[@]}" \
    --env-json "$env_json" --session "${WORK_ROOT}/session.jsonl" --out "$out"
}

# ---------------------------------------------------------------------------
# run_with_timeout <secs> <cmd...>
#   This machine has neither `timeout` nor `gtimeout`, and a lab that hangs on
#   a port-forward would otherwise wedge the whole suite. Runs the child in its
#   own process group so backgrounded port-forwards die with it.
#
#   That process group is not the terminal's foreground group, so Ctrl-C reaches
#   only the runner. on_interrupt forwards it; otherwise the driver outlives the
#   runner, keeps changing the cluster, and writes into the next run's work dir.
# ---------------------------------------------------------------------------
E2E_CHILD_PGID=""
E2E_CURRENT_KEY=""
E2E_IN_GRACE=0
E2E_CURRENT_START=0

# write_outcome_file <key> <status> <rc> <duration> <requires> [only_on] [k=v...]
#   One line per lab at ${WORK_ROOT}/<key>/outcome. The report builder reads it,
#   so the field names and status values are a contract. Optional trailing
#   fields: drift=1, cleanup_fail=N, reason=declined.
write_outcome_file() {
  local key="$1" status="$2" rc="$3" dur="$4" req="$5" only_on="${6:-}" line
  shift 5; (( $# )) && shift
  line="status=${status} rc=${rc} duration=${dur} requires=${req}"
  [[ -n "$only_on" ]] && line+=" only_on=${only_on}"
  (( $# )) && line+=" $*"
  mkdir -p "${WORK_ROOT}/${key}"
  printf '%s\n' "$line" > "${WORK_ROOT}/${key}/outcome"
}

# custom_snapshot: the objects a custom lab could leave behind, one sorted line
#   each: `kind/name` for cluster-scoped kinds, `<namespace> kind/name` for the
#   shared namespaces. run_lab diffs it before and after. Returns 1 when any
#   kubectl call exits non-zero or the cluster-scoped list is empty (a real
#   cluster always has namespaces), so the caller reports LEFTOVER CHECK NOT RUN
#   instead of a diff against a partial list.
#   Every namespaced kind listed comes with the 001 base install. kubectl fails
#   the whole call on a kind the cluster does not know, so on a cluster without
#   the base the check reports NOT RUN rather than a silent pass.
CUSTOM_SNAPSHOT_NAMESPACES=(agentgateway-system default)
custom_snapshot() {
  local all ns_out ns
  all="$(kubectl get namespaces,clusterroles,clusterrolebindings,gatewayclasses,crds,validatingwebhookconfigurations,mutatingwebhookconfigurations,priorityclasses -o name 2>/dev/null)" || return 1
  [[ -n "$all" ]] || return 1
  for ns in "${CUSTOM_SNAPSHOT_NAMESPACES[@]}"; do
    ns_out="$(kubectl get -n "$ns" enterpriseagentgatewaybackends,ratelimitconfigs,secrets,configmaps,referencegrants,deployments,services,serviceaccounts -o name --ignore-not-found 2>/dev/null)" || return 1
    [[ -n "$ns_out" ]] && all+=$'\n'"$(sed "s|^|${ns} |" <<<"$ns_out")"
  done
  printf '%s\n' "$all" | sort
}

# note_leftover <line>
#   Appends one line to the report's Leftovers section. Shipped labs write the
#   shared file, which only a --track run starts afresh; run_lab points
#   LEFTOVERS_FILE at <work>/custom/<name>/leftovers.txt for a custom lab.
LEFTOVERS_FILE="${WORK_ROOT}/leftovers.txt"
note_leftover() {
  printf '%s\n' "$1" >> "$LEFTOVERS_FILE"
}

# is_declined <key>: true when --skip named the key.
is_declined() {
  local k
  for k in ${SKIP_KEYS[@]+"${SKIP_KEYS[@]}"}; do [[ "$k" == "$1" ]] && return 0; done
  return 1
}

run_with_timeout() {
  local secs="$1"; shift
  local pid rc waited=0

  set -m
  "$@" & pid=$!
  set +m
  E2E_CHILD_PGID="$pid"

  while kill -0 "$pid" 2>/dev/null; do
    if (( waited >= secs )); then
      # SIGTERM runs the driver's EXIT trap, which runs the lab's Cleanup. Give
      # it time to finish (deleting a namespace takes a while) before SIGKILL.
      kill -TERM "-${pid}" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      local grace=0
      E2E_IN_GRACE=1
      while kill -0 "$pid" 2>/dev/null && (( grace < ${E2E_CLEANUP_GRACE:-180} )); do
        sleep 1; grace=$((grace + 1))
      done
      if kill -0 "$pid" 2>/dev/null; then
        note_leftover "CLEANUP INCOMPLETE: ${E2E_CURRENT_KEY} (cleanup still running after ${grace}s, killed)"
      fi
      kill -KILL "-${pid}" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      E2E_CHILD_PGID=""
      E2E_IN_GRACE=0
      return 124
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid"; rc=$?
  E2E_CHILD_PGID=""
  return "$rc"
}

# on_interrupt
#   SIGTERM lets the driver's EXIT trap run the lab's Cleanup section. A driver
#   still alive after 30s gets SIGKILL, so the runner never leaves one behind.
on_interrupt() {
  trap - INT TERM HUP
  local pgid="$E2E_CHILD_PGID" i
  [[ -n "$E2E_CURRENT_KEY" ]] && write_outcome_file "$E2E_CURRENT_KEY" interrupted 130 \
    "$((SECONDS - E2E_CURRENT_START))" "$(tr ' ' ',' <<<"${E2E_SPEC_REQUIRES:-}")"
  if [[ -n "$pgid" ]] && kill -0 "-${pgid}" 2>/dev/null; then
    printf "\n${YELLOW}Interrupted: stopping the running lab (pgid %s) and its cleanup${NC}\n" "$pgid" >&2
    # During the timeout grace the driver already got SIGTERM and is running
    # Cleanup; a second SIGTERM would kill that Cleanup.
    [[ "${E2E_IN_GRACE:-0}" == "1" ]] || kill -TERM "-${pgid}" 2>/dev/null
    for i in $(seq 1 30); do
      kill -0 "-${pgid}" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "-${pgid}" 2>/dev/null; then
      note_leftover "CLEANUP INCOMPLETE: ${E2E_CURRENT_KEY} (interrupted; cleanup killed after 30s)"
      printf "${RED}Cleanup for %s did not finish; the report lists it under Leftovers${NC}\n" "$E2E_CURRENT_KEY" >&2
    fi
    kill -KILL "-${pgid}" 2>/dev/null
  fi
  exit 130
}

# ---------------------------------------------------------------------------
# Spec discovery
# ---------------------------------------------------------------------------
discover_specs() {
  find "${SPEC_DIR}" -name '*.yaml' -not -name '_*' | sort
}

spec_key() {  # e2e/specs/routing/direct-response.yaml -> routing/direct-response
  if [[ "$1" == "${CUSTOM_ROOT}/"* ]]; then  # custom-labs/<name>/spec.yaml -> custom/<name>
    local crel="${1#"${CUSTOM_ROOT}"/}"
    printf 'custom/%s' "${crel%%/*}"; return
  fi
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
  # Four fields: the Gateway's own spec surface, then the inventory of
  # HTTPRoutes, enterprise policies and AgentgatewayModels in the baseline
  # namespace. A lab that leaves a stray route, policy or model behind corrupts
  # later labs just as surely as one that rewrites the Gateway — the inventory
  # makes that visible.
  local gw routes pols models
  gw="$(kubectl get gateway "$BASELINE_GW" -n "$BASELINE_NS" \
    -o jsonpath='{.spec.listeners[*].port}|{.spec.infrastructure.parametersRef.name}' 2>/dev/null)"
  routes="$(kubectl get httproutes -n "$BASELINE_NS" -o name 2>/dev/null | sort | paste -sd, -)"
  pols="$(kubectl get enterpriseagentgatewaypolicies -n "$BASELINE_NS" -o name 2>/dev/null | sort | paste -sd, -)"
  models="$(kubectl get agentgatewaymodels -n "$BASELINE_NS" -o name 2>/dev/null | sort | paste -sd, -)"
  # Unit separator (0x1f): a single byte that can't appear in resource names,
  # so the drift report can split the four fields reliably.
  printf '%s\x1f%s\x1f%s\x1f%s' "$gw" "$routes" "$pols" "$models"
}

baseline_exists() {
  # The fingerprint always carries its three separator bytes, so it is never the
  # empty string — test the Gateway field instead.
  local gw
  IFS=$'\x1f' read -r gw _ _ _ <<<"$1"
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
  local start="$SECONDS" req=""
  E2E_CURRENT_KEY="$key"; E2E_CURRENT_START="$start"
  # <status> <rc>: record the outcome, then clear the in-flight marker.
  local extra=()
  write_outcome() {
    write_outcome_file "$key" "$1" "$2" "$((SECONDS - start))" "$req" "" ${extra[@]+"${extra[@]}"}
    E2E_CURRENT_KEY=""
  }

  if is_declined "$key"; then
    printf "  ${YELLOW}⊘${NC} %-58s ${YELLOW}SKIP${NC} (declined)\n" "$key"
    TOTAL_SKIP=$((TOTAL_SKIP+1)); SKIPPED+=("${key} (declined)")
    write_outcome_file "$key" skip 0 0 "" "" reason=declined; E2E_CURRENT_KEY=""; return
  fi

  local meta
  meta="$("$PYTHON" "$LABDOC" meta "$spec")" || { printf "${RED}✗ %s — bad spec${NC}\n" "$key"; TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("$key"); write_outcome fail 1; return; }
  eval "$meta"
  [[ -n "$E2E_TIMEOUT_OVERRIDE" ]] && E2E_SPEC_TIMEOUT="$E2E_TIMEOUT_OVERRIDE"
  req="${E2E_SPEC_REQUIRES// /,}"

  local lab="${REPO_ROOT}/${E2E_SPEC_LAB}"
  if [[ ! -f "$lab" ]]; then
    printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC} — lab not found: %s\n" "$key" "$E2E_SPEC_LAB"
    TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("$key"); write_outcome fail 1; return
  fi

  if [[ "$E2E_SPEC_TIER" == "excluded" || "$E2E_SPEC_TIER" == "t5-manual" ]]; then
    printf "  ${DIM}—${NC} %-58s ${DIM}EXCLUDED${NC} — %s\n" "$key" "${E2E_SPEC_REASON:-not automatable}"
    EXCLUDED+=("$key"); write_outcome excluded 0; return
  fi

  if [[ -n "$E2E_SPEC_REQUIRES" ]] && ! e2e_keys_present $E2E_SPEC_REQUIRES; then
    printf "  ${YELLOW}⊘${NC} %-58s ${YELLOW}SKIP${NC} — missing: %s\n" "$key" "$E2E_SPEC_REQUIRES"
    TOTAL_SKIP=$((TOTAL_SKIP+1)); SKIPPED+=("${key} (${E2E_SPEC_REQUIRES})"); write_outcome skip 0; return
  fi

  local workdir="${WORK_ROOT}/${key}"
  rm -rf "$workdir"; mkdir -p "$workdir/blocks"
  LEFTOVERS_FILE="${WORK_ROOT}/leftovers.txt"
  [[ "$key" == custom/* ]] && LEFTOVERS_FILE="${workdir}/leftovers.txt"

  local driver="${workdir}/driver.sh"
  if ! "$PYTHON" "$LABDOC" plan "$lab" "$spec" "$workdir" >"$driver" 2>"${workdir}/plan.err"; then
    printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC}\n" "$key"
    sed 's/^/      /' "${workdir}/plan.err"
    TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("$key"); write_outcome fail 1; return
  fi
  chmod +x "$driver"

  printf "${CYAN}${BOLD}[%s]${NC} ${DIM}%s${NC}\n" "$key" "${E2E_SPEC_DESC:-}"

  # The _base labs *establish* the baseline — 001 creates the Gateway and the
  # three observability policies — so drift is meaningless for them. Every
  # other lab is measured against what they left behind.
  local is_base=false
  [[ "$spec" == *"/_base/"* ]] && is_base=true

  local drifted=false base_before="" base_after=""
  [[ "$is_base" == "false" ]] && base_before="$(baseline_fingerprint)"

  # Custom labs get a cluster snapshot before and after, and --keep.
  local is_custom=false snap_before="" snap_ok=true leftover_fail=false
  [[ "$key" == custom/* ]] && is_custom=true
  if [[ "$is_custom" == "true" && "$KEEP" != "true" ]]; then
    snap_before="$(custom_snapshot)" || snap_ok=false
  fi

  local rc=0
  if [[ "$is_custom" == "true" && "$KEEP" == "true" ]]; then
    E2E_KEEP=1 run_with_timeout "$E2E_SPEC_TIMEOUT" bash "$driver" || rc=$?
  else
    run_with_timeout "$E2E_SPEC_TIMEOUT" bash "$driver" || rc=$?
  fi

  if [[ "$is_custom" == "true" && "$KEEP" == "true" ]]; then
    extra+=("keep=1")
    note_leftover "LEFTOVER CHECK SKIPPED: ${key} kept on the cluster"
  elif [[ "$is_custom" == "true" ]]; then
    # A failed snapshot would report nothing, or every object, as leftover, so
    # the check does not run and the lab fails. So does any leftover.
    local snap_after left n_left=0
    snap_after="$(custom_snapshot)" || snap_ok=false
    if [[ "$snap_ok" != "true" ]]; then
      printf "  ${RED}LEFTOVER CHECK NOT RUN: %s (cluster snapshot failed)${NC}\n" "$key"
      note_leftover "LEFTOVER CHECK NOT RUN: ${key} (cluster snapshot failed)"
      extra+=("leftover_check=not-run")
      leftover_fail=true
    else
      while IFS= read -r left; do
        [[ -n "$left" ]] || continue
        printf "  ${YELLOW}LEFTOVER: %s${NC}\n" "$left"
        note_leftover "LEFTOVER: ${left}"
        n_left=$((n_left + 1))
      done < <(comm -13 <(printf '%s\n' "$snap_before") <(printf '%s\n' "$snap_after"))
      if (( n_left > 0 )); then
        extra+=("leftover=${n_left}")
        leftover_fail=true
      fi
    fi
    [[ "$leftover_fail" == "true" ]] && printf "  ${RED}✗${NC} %-58s ${RED}LEFTOVERS${NC}\n" "$key"
  fi

  # Attribute baseline damage to the lab that caused it, while we still know
  # which lab that was.
  [[ "$is_base" == "false" ]] && base_after="$(baseline_fingerprint)"
  if [[ "$is_base" == "false" ]] && baseline_exists "$base_before" &&
     [[ "$base_after" != "$base_before" ]]; then
    printf "  ${RED}✗${NC} %-58s ${RED}BASELINE DRIFT${NC}\n" "$key"
    local gw_b routes_b pols_b models_b gw_a routes_a pols_a models_a
    IFS=$'\x1f' read -r gw_b routes_b pols_b models_b <<<"$base_before"
    IFS=$'\x1f' read -r gw_a routes_a pols_a models_a <<<"$base_after"
    [[ "$gw_b"     != "$gw_a"     ]] && printf "      gateway:  %s -> %s\n" "$gw_b" "$gw_a"
    [[ "$routes_b" != "$routes_a" ]] && printf "      routes:   %s -> %s\n" "${routes_b:-none}" "${routes_a:-none}"
    [[ "$pols_b"   != "$pols_a"   ]] && printf "      policies: %s -> %s\n" "${pols_b:-none}" "${pols_a:-none}"
    [[ "$models_b" != "$models_a" ]] && printf "      models:   %s -> %s\n" "${models_b:-none}" "${models_a:-none}"
    if [[ "$gw_b" != "$gw_a" ]] && baseline_restore; then
      printf "      ${YELLOW}restored the 001 Gateway so the run can continue${NC}\n"
    fi
    drifted=true
    extra+=("drift=1")
    note_leftover "BASELINE DRIFT: ${key}"
    BASELINE_DRIFT+=("$key")
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
    FAILED+=("${key} (baseline drift)")
  fi

  # The driver writes `result` after its Cleanup section finishes, so an empty
  # file means Cleanup never completed.
  local p=0 f=0 s=0 cf=0
  E2E_R_pass=0; E2E_R_fail=0; E2E_R_skip=0; E2E_R_cleanup_fail=0
  if [[ -s "${workdir}/result" ]]; then
    # shellcheck disable=SC2046
    eval $(sed 's/\([a-z_]*\)=/E2E_R_\1=/g' "${workdir}/result")
    p="${E2E_R_pass:-0}"; f="${E2E_R_fail:-0}"; s="${E2E_R_skip:-0}"; cf="${E2E_R_cleanup_fail:-0}"
  elif [[ "$rc" -ne 0 ]]; then
    note_leftover "CLEANUP INCOMPLETE: ${key}"
    extra+=("cleanup_fail=incomplete")
  fi
  if [[ "$cf" -gt 0 ]]; then
    printf "  ${YELLOW}!${NC} %-58s ${YELLOW}%s cleanup block(s) failed${NC}\n" "$key" "$cf"
    note_leftover "CLEANUP BLOCK FAILED: ${key}"
    extra+=("cleanup_fail=${cf}")
  fi

  if [[ "$rc" -eq 124 ]]; then
    printf "  ${RED}✗${NC} %-58s ${RED}FAIL${NC} — TIMEOUT (>%ss)\n" "$key" "$E2E_SPEC_TIMEOUT"
    TOTAL_FAIL=$((TOTAL_FAIL+1)); FAILED+=("${key} (timeout)")
    write_outcome timeout 124
    printf "\n"; return
  fi

  TOTAL_PASS=$((TOTAL_PASS + p))
  TOTAL_FAIL=$((TOTAL_FAIL + f))
  TOTAL_SKIP=$((TOTAL_SKIP + s))
  [[ "$f" -gt 0 || "$rc" -ne 0 ]] && FAILED+=("$key")
  [[ "$f" -eq 0 && "$rc" -ne 0 ]] && TOTAL_FAIL=$((TOTAL_FAIL + 1))
  # Leftovers fail a lab that otherwise passed; a lab already counted as failed
  # (steps, rc, baseline drift) is not counted twice.
  if [[ "$leftover_fail" == "true" && "$f" -eq 0 && "$rc" -eq 0 && "$drifted" == "false" ]]; then
    TOTAL_FAIL=$((TOTAL_FAIL + 1)); FAILED+=("${key} (leftovers)")
  fi

  if [[ "$f" -gt 0 || "$rc" -ne 0 || "$drifted" == "true" || "$leftover_fail" == "true" ]]; then
    write_outcome fail "$rc"
  else
    write_outcome pass 0
  fi
  printf "\n"
}

# ---------------------------------------------------------------------------
# --list / --lint
# ---------------------------------------------------------------------------
if [[ "$LIST_ONLY" == "true" ]]; then
  printf "${BOLD}%-46s %-10s %s${NC}\n" "LAB" "TIER" "REQUIRES"
  while IFS= read -r spec; do
    eval "$("$PYTHON" "$LABDOC" meta "$spec")"
    printf "%-46s %-10s %s\n" "$(spec_key "$spec")" "$E2E_SPEC_TIER" "${E2E_SPEC_REQUIRES:--}"
  done < <(discover_specs)
  exit 0
fi

if [[ "$LINT_ONLY" == "true" && -n "$CUSTOM_NAME" ]]; then
  spec="${CUSTOM_ROOT}/${CUSTOM_NAME}/spec.yaml"
  [[ -f "$spec" ]] || { printf "${RED}No custom lab at %s${NC}\n" "$spec" >&2; exit 1; }
  lint_fail=0
  meta="$("$PYTHON" "$LABDOC" meta "$spec")" || exit 1
  eval "$meta"
  lab="${REPO_ROOT}/${E2E_SPEC_LAB}"
  [[ -f "$lab" ]] || { printf "${RED}✗ %s — lab not found: %s${NC}\n" "$(spec_key "$spec")" "$E2E_SPEC_LAB"; exit 1; }
  # The spec's lab: must be the lab file in its own directory, so the lab, its
  # spec and its reports stay together and the harness keys it by name.
  want_lab="${CUSTOM_ROOT}/${CUSTOM_NAME}/${CUSTOM_NAME}.md"
  if [[ "$(realpath "$lab")" != "$(realpath "$want_lab" 2>/dev/null)" ]]; then
    printf "${RED}✗ %s${NC}\n" "$(spec_key "$spec")"
    printf "    spec lab: must be %s\n" "${want_lab#"${REPO_ROOT}"/}"
    lint_fail=1
  fi
  if out="$("$PYTHON" "$LABDOC" lint --custom "$lab" "$spec" 2>&1)"; then
    printf "${GREEN}✓${NC} %s\n" "$out"
  else
    printf "${RED}✗ %s${NC}\n" "$(spec_key "$spec")"
    printf '%s\n' "$out" | sed 's/^/    /'
    lint_fail=1
  fi
  # conventions.py scopes its Cleanup rule to custom-labs/ paths, so lint from
  # the directory that holds CUSTOM_ROOT. With the default CUSTOM_ROOT this is
  # REPO_ROOT and the path is the lab's repo-relative path.
  conv_root="$REPO_ROOT"; conv_rel="${lab#${REPO_ROOT}/}"
  if [[ "$lab" == "${CUSTOM_ROOT}/"* ]]; then
    conv_root="$(dirname "$CUSTOM_ROOT")"; conv_rel="$(basename "$CUSTOM_ROOT")/${lab#${CUSTOM_ROOT}/}"
  fi
  printf "\n${BOLD}Conventions:${NC}\n"
  "$PYTHON" "${E2E_DIR}/lib/conventions.py" "$conv_root" --file "$conv_rel" || lint_fail=1
  exit "$lint_fail"
fi

if [[ "$LINT_ONLY" == "true" ]]; then
  lint_fail=0
  while IFS= read -r spec; do
    eval "$("$PYTHON" "$LABDOC" meta "$spec")"
    lab="${REPO_ROOT}/${E2E_SPEC_LAB}"
    if [[ ! -f "$lab" ]]; then
      printf "${RED}✗ %s — lab not found: %s${NC}\n" "$(spec_key "$spec")" "$E2E_SPEC_LAB"
      lint_fail=1; continue
    fi
    if out="$("$PYTHON" "$LABDOC" lint "$lab" "$spec" 2>&1)"; then
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
  "$PYTHON" "${E2E_DIR}/lib/conventions.py" "${REPO_ROOT}" || lint_fail=1

  printf "\n${BOLD}Tracks:${NC}\n"
  track_out="$("$PYTHON" "${E2E_DIR}/lib/tracks.py" lint)" || lint_fail=1
  [[ -n "$track_out" ]] && printf '%s\n' "$track_out" | sed 's/^/  /'
  printf "Tracks: %s problem(s)\n" "$(printf '%s' "$track_out" | grep -c . || true)"

  exit "$lint_fail"
fi

# ---------------------------------------------------------------------------
# --install-base
# ---------------------------------------------------------------------------
install_base() {
  printf "${BOLD}═══ Installing baseline (001 + 002) ═══${NC}\n\n"
  local spec
  for spec in "${SPEC_DIR}/_base/001-install-enterprise-agentgateway.yaml" \
              "${SPEC_DIR}/_base/002-set-up-monitoring-tools.yaml"; do
    [[ -f "$spec" ]] || { printf "${RED}missing base spec: %s${NC}\n" "$spec"; exit 1; }
    run_lab "$spec"
  done
  if [[ "$TOTAL_FAIL" -gt 0 ]]; then
    printf "${RED}Baseline install failed — not running labs.${NC}\n"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# --uninstall-base
#   Removes 002 (monitoring) then 001 (controller, Gateway, enterprise CRDs).
#   The Gateway API CRDs are cluster-wide and may be shared, so the optional
#   block in 001 that deletes them is never run. Needs no license key.
# ---------------------------------------------------------------------------
uninstall_base() {
  printf "${BOLD}═══ Uninstalling baseline (002 + 001, Gateway API CRDs kept) ═══${NC}\n\n"
  local spec
  for spec in "${SPEC_DIR}/_base/901-uninstall-monitoring.yaml" \
              "${SPEC_DIR}/_base/902-uninstall-agentgateway.yaml"; do
    [[ -f "$spec" ]] || { printf "${RED}missing base spec: %s${NC}\n" "$spec"; exit 1; }
    run_lab "$spec"
  done
  local rc=0
  if [[ "$TOTAL_FAIL" -ne 0 ]]; then
    printf "${RED}uninstall step failed, see above${NC}\n"; rc=1
  fi

  # Leftover namespaces: anything present now that was not in the namespace
  # snapshot detect-env.sh took before the session. agw-evaluator holds the
  # evaluator identity the user applied, so it stays.
  local snapshot="${WORK_ROOT}/env.json" before now ns phase waited=0 leftover=()
  if before="$("$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1])); print("\n".join(d["namespaces"]))' \
      "$snapshot" 2>/dev/null)" && [[ -n "$before" ]]; then
    while :; do
      leftover=()
      now="$(kubectl get namespaces -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' 2>/dev/null)" \
        || { printf "${RED}cannot list namespaces${NC}\n"; rc=1; break; }
      local terminating=0
      while read -r ns phase; do
        [[ -n "$ns" && "$ns" != "agw-evaluator" ]] || continue
        grep -qxF "$ns" <<<"$before" && continue
        leftover+=("${ns}${phase:+ (${phase})}")
        [[ "$phase" == "Terminating" ]] && terminating=$((terminating + 1))
      done <<<"$now"
      # Namespaces the uninstall just deleted are still Terminating for a while.
      (( ${#leftover[@]} > 0 && terminating == ${#leftover[@]} && waited < 180 )) || break
      sleep 5; waited=$((waited + 5))
    done
    for ns in ${leftover[@]+"${leftover[@]}"}; do
      printf "${RED}LEFTOVER NAMESPACE: %s${NC}\n" "$ns"
      note_leftover "LEFTOVER NAMESPACE: ${ns}"
    done
    (( ${#leftover[@]} == 0 )) || rc=1
  else
    printf "${YELLOW}No namespace snapshot in %s; run e2e/lib/detect-env.sh before the base install to enable this check.${NC}\n" "$snapshot"
    note_leftover "NAMESPACE CHECK NOT RUN: no namespace snapshot from before the session"
  fi

  local line existing
  line="$("${E2E_DIR}/lib/detect-env.sh" --skip-rbac-check --skip-lb-probe \
    --out "${WORK_ROOT}/env-teardown.json" 2>/dev/null | grep '^existing_install' || true)"
  printf '%s\n' "${line:-existing_install: (probe failed)}"
  existing="${line#existing_install:}"; existing="${existing# }"
  if [[ -n "$existing" || -z "$line" || "$rc" -ne 0 ]]; then
    printf "${RED}Something from the baseline remains on the cluster.${NC}\n"; rc=1
  fi
  [[ "$rc" -eq 0 ]] && printf "${GREEN}Baseline removed.${NC}\n"
  exit "$rc"
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
  trap 'on_interrupt' INT TERM HUP
}

release_run_lock() {
  [[ -n "$RUN_LOCK" ]] && rm -rf "$RUN_LOCK"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
START="$SECONDS"
mkdir -p "$WORK_ROOT"

# --report-only reads existing outcome files and touches nothing else, so it
# needs no run list, credentials or lock.
if [[ "$REPORT_ONLY" == "true" ]]; then
  build_report
  exit $?
fi

acquire_run_lock
e2e_load_env_local

[[ "$UNINSTALL_BASE" == "true" ]] && uninstall_base

if [[ "$INSTALL_BASE" == "true" ]]; then
  e2e_resolve_key SOLO_TRIAL_LICENSE_KEY || {
    printf "${RED}SOLO_TRIAL_LICENSE_KEY is required for --install-base${NC}\n"; exit 1; }
  install_base
  [[ "$ONLY_BASE" == "true" ]] && { printf "${GREEN}Baseline installed.${NC}\n"; exit 0; }
fi

# Build the run list
declare -a RUN_SPECS=()
# A --report run without --track writes a report for this invocation only, so
# it starts with no leftover lines from earlier runs.
# A custom run writes its own leftovers file, so the shared one stays as is.
if [[ -z "$TRACK" && -z "$CUSTOM_NAME" && "$REPORT" == "true" ]]; then
  rm -f "${WORK_ROOT}/leftovers.txt"
fi
if [[ -n "$TRACK" ]]; then
  # A track run is a fresh session: drop every earlier outcome and the old
  # leftovers so the report cannot mix runs. Single-lab runs never clear,
  # because the enablement flow runs labs one at a time and reports at the end.
  # --report-only exits before this point.
  # Custom-lab outcomes belong to the author's own runs and survive the reset.
  find "$WORK_ROOT" -name outcome -type f -not -path "${WORK_ROOT}/custom/*" -delete 2>/dev/null
  rm -f "${WORK_ROOT}/leftovers.txt"
  track_rows="$("$PYTHON" "${E2E_DIR}/lib/tracks.py" list "$TRACK" --env "$ENV_CLASS" 2>&1)" || {
    if [[ "$track_rows" == *"no track '"* ]]; then
      printf "${RED}no track named %s${NC}\n" "$TRACK" >&2
    else
      printf '%s\n' "$track_rows" >&2
    fi
    exit 1; }
  printf "${BOLD}Track: %s | Env: %s${NC}\n" "$TRACK" "$ENV_CLASS"
  while IFS=$'\t' read -r tkey tstatus _; do
    [[ -n "$tkey" ]] || continue
    case "$tstatus" in
      run)
        [[ -f "${SPEC_DIR}/${tkey}.yaml" ]] || { printf "${RED}track entry %s has no spec file${NC}\n" "$tkey" >&2; exit 1; }
        RUN_SPECS+=("${SPEC_DIR}/${tkey}.yaml") ;;
      no-spec)
        printf "  ${DIM}—${NC} %-58s ${DIM}NOT AUTOMATED${NC}\n" "$tkey"
        write_outcome_file "$tkey" no-spec 0 0 "" ;;
      not-applicable:*)
        printf "  ${DIM}—${NC} %-58s ${DIM}NOT APPLICABLE (only on %s)${NC}\n" "$tkey" "${tstatus#not-applicable:}"
        write_outcome_file "$tkey" not-applicable 0 0 "" "${tstatus#not-applicable:}" ;;
    esac
  done <<<"$track_rows"
elif [[ -n "$CUSTOM_NAME" ]]; then
  cand="${CUSTOM_ROOT}/${CUSTOM_NAME}/spec.yaml"
  [[ -f "$cand" ]] || { printf "${RED}No custom lab at %s${NC}\n" "$cand" >&2; exit 1; }
  RUN_SPECS=("$cand")
elif [[ -n "$SPECIFIC" ]]; then
  cand="${SPEC_DIR}/${SPECIFIC%.yaml}.yaml"
  [[ -f "$cand" ]] || { printf "${RED}No spec at %s${NC}\n" "$cand" >&2; exit 1; }
  RUN_SPECS=("$cand")
else
  while IFS= read -r spec; do
    [[ "$spec" == *"/_base/"* ]] && continue
    if [[ -n "$TIER_FILTER" ]]; then
      eval "$("$PYTHON" "$LABDOC" meta "$spec")"
      [[ "$E2E_SPEC_TIER" == "$TIER_FILTER" ]] || continue
    fi
    RUN_SPECS+=("$spec")
  done < <(discover_specs)
fi

# Resolve every credential the run needs, up front, in one prompt batch.
declare -A NEEDED=()
for spec in "${RUN_SPECS[@]}"; do
  is_declined "$(spec_key "$spec")" && continue
  eval "$("$PYTHON" "$LABDOC" meta "$spec")"
  [[ "$E2E_SPEC_TIER" == "excluded" || "$E2E_SPEC_TIER" == "t5-manual" ]] && continue
  for v in $E2E_SPEC_REQUIRES; do NEEDED["$v"]=1; done
done
if (( ${#NEEDED[@]} > 0 )); then
  if [[ "$E2E_PROMPT" == "0" ]]; then
    printf "${BOLD}Resolving credentials${NC} ${DIM}(env → e2e/.env.local → shell rc)${NC}\n"
  else
    printf "${BOLD}Resolving credentials${NC} ${DIM}(env → e2e/.env.local → shell rc → prompt)${NC}\n"
  fi
  for v in $(printf '%s\n' "${!NEEDED[@]}" | sort); do
    if e2e_resolve_key "$v"; then
      printf "  ${GREEN}✓${NC} %s\n" "$v"
    else
      printf "  ${YELLOW}⊘${NC} %s — labs needing it will skip\n" "$v"
    fi
  done
  printf "\n"
fi
[[ "$RESOLVE_CREDS" == "true" ]] && exit 0

printf "${BOLD}═══════════════════════════════════════════════════════════════${NC}\n"
printf "${BOLD}  Enterprise Agentgateway Workshop — Lab E2E${NC}\n"
printf "${BOLD}  Context: %s | Specs: %d${NC}\n" \
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

if [[ "$REPORT" == "true" ]]; then
  build_report || printf "${RED}report build failed${NC}\n" >&2
fi

[[ "$TOTAL_FAIL" -eq 0 ]]
