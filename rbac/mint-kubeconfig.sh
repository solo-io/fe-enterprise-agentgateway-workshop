#!/usr/bin/env bash
# Mint a kubeconfig for the agw-evaluator ServiceAccount.
# Run as an admin identity after applying rbac/evaluator.yaml.
#   ./rbac/mint-kubeconfig.sh [--duration 8h] [--out <path>] [--ca-file <path>]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DURATION=8h
OUT="${ROOT}/e2e/.work/evaluator.kubeconfig"
CA_FILE=""

while (( $# )); do
  case "$1" in
    --duration) DURATION="${2:?--duration needs a value}"; shift 2 ;;
    --out)      OUT="${2:?--out needs a path}"; shift 2 ;;
    --ca-file)  CA_FILE="${2:?--ca-file needs a path}"; shift 2 ;;
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
if [[ -n "$CA_FILE" ]]; then
  [[ -r "$CA_FILE" ]] || { echo "cannot read --ca-file ${CA_FILE}" >&2; exit 1; }
  ca_data="$(base64 < "$CA_FILE" | tr -d '\n')"
elif [[ -z "$ca_data" && -n "$ca_file" ]]; then
  ca_data="$(base64 < "$ca_file" | tr -d '\n')"
fi
if [[ -z "$ca_data" ]]; then
  # Contexts that skip TLS verification (vind, some kind setups) carry no CA.
  # Every namespace holds the cluster CA in this ConfigMap.
  pem="$(kubectl get configmap kube-root-ca.crt -n default -o jsonpath='{.data.ca\.crt}' 2>/dev/null || true)"
  if [[ -n "$pem" ]]; then
    ca_data="$(printf '%s\n' "$pem" | base64 | tr -d '\n')"
    echo "note: cluster CA taken from kube-root-ca.crt" >&2
  else
    echo "no cluster CA available; pass one with --ca-file <path>" >&2
    exit 1
  fi
fi

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
  echo "      certificate-authority-data: ${ca_data}"
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
