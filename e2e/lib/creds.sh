#!/usr/bin/env bash
# e2e/lib/creds.sh — credential resolution for key-gated labs.
#
# Resolution order for each required variable:
#   1. already exported in the environment
#   2. e2e/.env.local            (cached from an earlier prompt; gitignored)
#   3. ~/.zshrc ~/.bashrc ~/.zprofile ~/.bash_profile ~/.env
#      Only the single matching `export VAR=...` line is evaluated — the shell
#      config is never sourced wholesale, which would run unrelated startup code.
#   4. interactive prompt (TTY only), then cached to e2e/.env.local
#   5. unresolved -> the lab is skipped and reported as skipped
#
# The runner resolves every key the selected labs need up front, so prompts
# arrive in one batch instead of interrupting a long run.

E2E_ENV_LOCAL="${E2E_ENV_LOCAL:-${E2E_DIR}/.env.local}"
E2E_RC_FILES=("$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.zprofile" "$HOME/.bash_profile" "$HOME/.env")

E2E_PROMPT="${E2E_PROMPT:-1}"
declare -a E2E_RESOLVED=()
declare -a E2E_MISSING=()

e2e_load_env_local() {
  [[ -f "$E2E_ENV_LOCAL" ]] || return 0
  # shellcheck disable=SC1090
  set -a; source "$E2E_ENV_LOCAL"; set +a
}

# e2e_scrape_rc <VAR> — echo the value from a shell rc file, if present.
e2e_scrape_rc() {
  local var="$1" f line
  for f in "${E2E_RC_FILES[@]}"; do
    [[ -f "$f" ]] || continue
    line="$(grep -hE "^[[:space:]]*(export[[:space:]]+)?${var}=" "$f" 2>/dev/null | tail -n1)"
    [[ -z "$line" ]] && continue
    # Evaluate only this assignment, in a subshell, so rc-file side effects
    # and any `$(...)` in unrelated lines cannot run.
    local value
    value="$(bash -c "${line}; printf '%s' \"\${${var}}\"" 2>/dev/null)"
    if [[ -n "$value" && "$value" != "\$${var}" ]]; then
      printf '%s' "$value"
      return 0
    fi
  done
  return 1
}

e2e_cache_key() {
  local var="$1" value="$2"
  touch "$E2E_ENV_LOCAL"
  chmod 600 "$E2E_ENV_LOCAL"
  grep -vE "^${var}=" "$E2E_ENV_LOCAL" >"${E2E_ENV_LOCAL}.tmp" 2>/dev/null || true
  printf '%s=%q\n' "$var" "$value" >>"${E2E_ENV_LOCAL}.tmp"
  mv "${E2E_ENV_LOCAL}.tmp" "$E2E_ENV_LOCAL"
  chmod 600 "$E2E_ENV_LOCAL"
}

# e2e_resolve_key <VAR> — returns 0 if resolved (and exports it), 1 if not.
e2e_resolve_key() {
  local var="$1" value=""

  if [[ -n "${!var:-}" ]]; then
    E2E_RESOLVED+=("${var}=env")
    return 0
  fi

  if value="$(e2e_scrape_rc "$var")" && [[ -n "$value" ]]; then
    export "${var}=${value}"
    E2E_RESOLVED+=("${var}=shellrc")
    return 0
  fi

  if [[ "$E2E_PROMPT" == "1" && -t 0 ]]; then
    printf "  ${YELLOW}?${NC} %s is not set. Paste a value (blank to skip labs needing it): " "$var" >&2
    read -rs value
    printf '\n' >&2
    if [[ -n "$value" ]]; then
      export "${var}=${value}"
      e2e_cache_key "$var" "$value"
      E2E_RESOLVED+=("${var}=prompt")
      return 0
    fi
  fi

  E2E_MISSING+=("$var")
  return 1
}

# e2e_resolve_all <VAR...> — resolve a set; returns 1 if any is missing.
e2e_resolve_all() {
  local var ok=0
  for var in "$@"; do
    e2e_resolve_key "$var" || ok=1
  done
  return "$ok"
}

e2e_keys_present() {
  local var
  for var in "$@"; do
    [[ -z "${!var:-}" ]] && return 1
  done
  return 0
}
