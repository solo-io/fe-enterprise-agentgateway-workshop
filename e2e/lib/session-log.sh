#!/usr/bin/env bash
# session-log.sh: append one event to the evaluation session log.
#
# Usage: session-log.sh add <kind> <lab-key> <text...>
#   kind: decision | deviation | question | answer | failure | keep-base
#   lab-key: the spec key the event belongs to, or - for none
#
# Writes one JSON line {"ts","kind","lab","text"} to ${E2E_WORK_ROOT:-e2e/.work}/session.jsonl.
# report.py reads that file when it builds the report.
set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_ROOT="${E2E_WORK_ROOT:-${E2E_DIR}/.work}"
KINDS="decision deviation question answer failure keep-base"

die() { printf 'session-log.sh: %s\n' "$*" >&2; exit 2; }

[[ "${1:-}" == "add" ]] || die "usage: session-log.sh add <kind> <lab-key> <text...>"
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
