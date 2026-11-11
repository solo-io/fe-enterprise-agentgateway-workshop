#!/usr/bin/env bash
# session-log.sh: append one event to the evaluation session log.
#
# Usage: session-log.sh start
#          moves an existing session.jsonl aside to session-<UTC stamp>.jsonl, so a
#          new session's report holds only its own events
#        session-log.sh add <kind> <lab-key> <text...>
#   kind: decision | deviation | question | answer | failure | keep-base | run | keep-custom
#     run: one attempt at a custom lab (the report lists these under Runs)
#     keep-custom: the custom lab was left on the cluster
#   lab-key: the spec key the event belongs to, or - for none
#
# Writes one JSON line {"ts","kind","lab","text"} to ${E2E_WORK_ROOT:-e2e/.work}/session.jsonl.
# report.py reads that file when it builds the report.
set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_ROOT="${E2E_WORK_ROOT:-${E2E_DIR}/.work}"
KINDS="decision deviation question answer failure keep-base run keep-custom"

die() { printf 'session-log.sh: %s\n' "$*" >&2; exit 2; }

if [[ "${1:-}" == "start" ]]; then
  [[ $# -eq 1 ]] || die "usage: session-log.sh start"
  if [[ -f "${WORK_ROOT}/session.jsonl" ]]; then
    dest="${WORK_ROOT}/session-$(date -u +%Y%m%d-%H%M%S).jsonl"
    n=0
    while [[ -e "$dest" ]]; do n=$((n + 1)); dest="${WORK_ROOT}/session-$(date -u +%Y%m%d-%H%M%S)-${n}.jsonl"; done
    mv "${WORK_ROOT}/session.jsonl" "$dest"
    printf 'previous session log moved to %s\n' "$dest"
  fi
  exit 0
fi

[[ "${1:-}" == "add" ]] || die "usage: session-log.sh start | add <kind> <lab-key> <text...>"
shift
[[ $# -ge 3 ]] || die "usage: session-log.sh add <kind> <lab-key> <text...>"
kind="$1"; lab="$2"; shift 2
text="$*"
[[ " ${KINDS} " == *" ${kind} "* ]] || die "unknown kind '${kind}' (one of: ${KINDS})"
[[ "$lab" == "-" ]] && lab=""

mkdir -p "$WORK_ROOT"
python3 -c '
import json, sys, datetime
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({"ts": ts, "kind": sys.argv[1], "lab": sys.argv[2], "text": sys.argv[3]}))
' "$kind" "$lab" "$text" >> "${WORK_ROOT}/session.jsonl"
