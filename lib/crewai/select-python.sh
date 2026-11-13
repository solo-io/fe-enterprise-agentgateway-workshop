#!/usr/bin/env bash
# Print the interpreter used by the workshop's CrewAI environment.
set -eu
for candidate in "${CREWAI_PYTHON:-}" python3.12 python3.11 python3; do
  [[ -n "$candidate" ]] || continue
  if ! command -v "$candidate" >/dev/null 2>&1; then
    [[ -n "${CREWAI_PYTHON:-}" && "$candidate" == "$CREWAI_PYTHON" ]] && break
    continue
  fi
  if "$candidate" -c 'import sys, venv; sys.exit(0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)' 2>/dev/null; then
    command -v "$candidate"
    exit 0
  fi
  if [[ -n "${CREWAI_PYTHON:-}" && "$candidate" == "$CREWAI_PYTHON" ]]; then
    break
  fi
done
echo 'CrewAI requires Python 3.11 or 3.12 with venv. Install one and put it on PATH, or set CREWAI_PYTHON to its executable.' >&2
exit 2
