#!/usr/bin/env bash
# Mint a kubeconfig for the agw-evaluator ServiceAccount.
# Run as an admin identity after applying rbac/evaluator.yaml.
#   ./rbac/mint-kubeconfig.sh [--duration 8h] [--out <path>] [--ca-file <path>]
#                             [--tls-server-name <name>]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DURATION=8h
OUT="${ROOT}/e2e/.work/evaluator.kubeconfig"
CA_FILE=""
TLS_NAME=""

while (( $# )); do
  case "$1" in
    --duration) DURATION="${2:?--duration needs a value}"; shift 2 ;;
    --out)      OUT="${2:?--out needs a path}"; shift 2 ;;
    --ca-file)  CA_FILE="${2:?--ca-file needs a path}"; shift 2 ;;
    --tls-server-name) TLS_NAME="${2:?--tls-server-name needs a name}"; shift 2 ;;
    -h|--help)  sed -n '2,6p' "${BASH_SOURCE[0]}"; exit 0 ;;
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
proxy_url="$(jq -r '.clusters[0].cluster["proxy-url"] // empty' <<<"$view")"
[[ -n "$TLS_NAME" ]] || TLS_NAME="$(jq -r '.clusters[0].cluster["tls-server-name"] // empty' <<<"$view")"
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
    # The server address such a context uses (a forwarded localhost port, a
    # host IP) is often missing from the apiserver certificate. Pods verify the
    # apiserver as kubernetes.default.svc with this same CA, so that name is in
    # the certificate.
    if [[ -z "$TLS_NAME" ]]; then
      TLS_NAME=kubernetes.default.svc
      echo "note: verifying the apiserver certificate as ${TLS_NAME}" >&2
    fi
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
  [[ -n "$TLS_NAME" ]] && echo "      tls-server-name: ${TLS_NAME}"
  [[ -n "$proxy_url" ]] && echo "      proxy-url: ${proxy_url}"
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

# Prove the kubeconfig works before telling the user to export it.
if ! err="$(kubectl --kubeconfig "$OUT" get --raw /version 2>&1 >/dev/null)"; then
  rm -f "$OUT"
  echo "the minted kubeconfig cannot reach the cluster:" >&2
  echo "  $(head -n1 <<<"$err")" >&2
  echo "for a certificate error, pass --ca-file <path> or --tls-server-name <name in the certificate>" >&2
  exit 1
fi

echo "Token valid for ${DURATION}. Use it with:"
echo "export KUBECONFIG=${OUT}"
