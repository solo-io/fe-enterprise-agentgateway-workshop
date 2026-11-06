#!/usr/bin/env bash
# Mint a kubeconfig for the agw-evaluator ServiceAccount.
# Run as an admin identity after applying rbac/evaluator.yaml.
#   ./rbac/mint-kubeconfig.sh [--duration 8h] [--out <path>]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DURATION=8h
OUT="${ROOT}/e2e/.work/evaluator.kubeconfig"

while (( $# )); do
  case "$1" in
    --duration) DURATION="${2:?--duration needs a value}"; shift 2 ;;
    --out)      OUT="${2:?--out needs a path}"; shift 2 ;;
    -h|--help)  sed -n '2,5p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

for tool in kubectl jq; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

view="$(kubectl config view --raw --minify -o json)" \
  || { echo "cannot read the current kubeconfig context" >&2; exit 1; }
server="$(jq -r '.clusters[0].cluster.server // empty' <<<"$view")"
ca_data="$(jq -r '.clusters[0].cluster["certificate-authority-data"] // empty' <<<"$view")"
ca_file="$(jq -r '.clusters[0].cluster["certificate-authority"] // empty' <<<"$view")"
[[ -n "$server" ]] || { echo "current context has no cluster server" >&2; exit 1; }
if [[ -z "$ca_data" && -n "$ca_file" ]]; then ca_data="$(base64 < "$ca_file" | tr -d '\n')"; fi

token="$(kubectl create token agw-evaluator -n agw-evaluator --duration "$DURATION")" \
  || { echo "cannot create a token; apply rbac/evaluator.yaml as admin first" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
umask 077
{
  echo "apiVersion: v1"
  echo "kind: Config"
  echo "clusters:"
  echo "  - name: agw-evaluator"
  echo "    cluster:"
  echo "      server: ${server}"
  if [[ -n "$ca_data" ]]; then
    echo "      certificate-authority-data: ${ca_data}"
  else
    echo "      insecure-skip-tls-verify: true"
  fi
  echo "users:"
  echo "  - name: agw-evaluator"
  echo "    user:"
  echo "      token: ${token}"
  echo "contexts:"
  echo "  - name: agw-evaluator"
  echo "    context:"
  echo "      cluster: agw-evaluator"
  echo "      user: agw-evaluator"
  echo "current-context: agw-evaluator"
} > "$OUT"
chmod 600 "$OUT"

echo "Token valid for ${DURATION}. Use it with:"
echo "export KUBECONFIG=${OUT}"
