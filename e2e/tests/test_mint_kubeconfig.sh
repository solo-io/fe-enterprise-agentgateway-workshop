#!/usr/bin/env bash
# Tests for rbac/mint-kubeconfig.sh's cluster CA handling. Offline: kubectl is a
# stub. MINT_CA selects what the admin context carries (none or data) and
# MINT_CM whether kube-root-ca.crt answers. MINT_CA=named adds tls-server-name and
# proxy-url to the context; MINT_VERIFY=fail fails the check of the new kubeconfig.
E2E="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MINT="${E2E}/../rbac/mint-kubeconfig.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "${TMP}/bin"
cat > "${TMP}/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  "config view --raw --minify -o json")
    if [[ "$MINT_CA" == data ]]; then
      echo '{"clusters":[{"cluster":{"server":"https://10.0.0.1:6443","certificate-authority-data":"Q09OVEVYVA=="}}]}'
    elif [[ "$MINT_CA" == named ]]; then
      echo '{"clusters":[{"cluster":{"server":"https://10.0.0.1:6443","certificate-authority-data":"Q09OVEVYVA==","tls-server-name":"api.internal","proxy-url":"socks5://127.0.0.1:1080"}}]}'
    else
      echo '{"clusters":[{"cluster":{"server":"https://10.0.0.1:6443","insecure-skip-tls-verify":true}}]}'
    fi ;;
  "get configmap kube-root-ca.crt -n default"*)
    [[ "$MINT_CM" == yes ]] && printf -- '-----BEGIN CERTIFICATE-----\nTESTPEM\n-----END CERTIFICATE-----' ;;
  "create token"*) echo tok123 ;;
  "--kubeconfig "*" get --raw /version")
    if [[ "${MINT_VERIFY:-}" == fail ]]; then
      echo "Unable to connect to the server: x509: certificate is valid for kubernetes, not 10.0.0.1" >&2; exit 1
    fi ;;
esac
exit 0
STUB
chmod +x "${TMP}/bin/kubectl"
export PATH="${TMP}/bin:${PATH}"

T_PASS=0; T_FAIL=0
ok()   { T_PASS=$((T_PASS+1)); printf 'ok   %s\n' "$1"; }
fail() { T_FAIL=$((T_FAIL+1)); printf 'FAIL %s\n' "$1"; }

OUT_K="${TMP}/k"
mint() { rm -f "$OUT_K"; RC=0; OUT="$("$MINT" --out "$OUT_K" "$@" 2>&1)" || RC=$?; }
ca_of() { awk '/certificate-authority-data:/{print $2}' "$OUT_K" | base64 -d 2>/dev/null; }

MINT_CA=none MINT_CM=yes mint
[[ $RC == 0 ]] && ok "insecure context mints (rc=0)" || fail "rc=$RC: $OUT"
grep -q 'certificate-authority-data:' "$OUT_K" && ok "kubeconfig embeds certificate-authority-data" || fail "no CA in kubeconfig"
[[ "$(ca_of)" == *TESTPEM* ]] && ok "CA comes from kube-root-ca.crt" || fail "CA content: $(ca_of)"
grep -q insecure "$OUT_K" && fail "insecure-skip-tls-verify copied" || ok "insecure-skip-tls-verify not copied"
[[ "$OUT" == *"cluster CA taken from kube-root-ca.crt"* ]] && ok "prints the CA source note" || fail "output: $OUT"

MINT_CA=none MINT_CM=no mint
[[ $RC == 1 && "$OUT" == *"--ca-file"* ]] && ok "no CA anywhere exits 1 naming --ca-file" || fail "rc=$RC: $OUT"
[[ ! -e "$OUT_K" ]] && ok "no kubeconfig written without a CA" || fail "kubeconfig written"

printf 'FILEPEM\n' > "${TMP}/ca.pem"
MINT_CA=none MINT_CM=no mint --ca-file "${TMP}/ca.pem"
[[ $RC == 0 && "$(ca_of)" == FILEPEM ]] && ok "--ca-file is embedded" || fail "rc=$RC ca=$(ca_of): $OUT"

MINT_CA=data MINT_CM=yes mint
[[ $RC == 0 && "$(ca_of)" == CONTEXT ]] && ok "context CA wins over the ConfigMap" || fail "rc=$RC ca=$(ca_of)"
grep -q 'tls-server-name' "$OUT_K" && fail "tls-server-name set for a context CA" || ok "no tls-server-name for a context CA"

MINT_CA=none MINT_CM=yes mint
grep -q 'tls-server-name: kubernetes.default.svc' "$OUT_K" && ok "kube-root-ca.crt CA verifies as kubernetes.default.svc" \
  || fail "kubeconfig: $(cat "$OUT_K")"

MINT_CA=none MINT_CM=yes mint --tls-server-name my.api
grep -q 'tls-server-name: my.api' "$OUT_K" && ok "--tls-server-name wins" || fail "kubeconfig: $(cat "$OUT_K")"

MINT_CA=named MINT_CM=yes mint
grep -q 'tls-server-name: api.internal' "$OUT_K" && grep -q 'proxy-url: socks5://127.0.0.1:1080' "$OUT_K" \
  && ok "context tls-server-name and proxy-url are carried" || fail "kubeconfig: $(cat "$OUT_K")"

MINT_VERIFY=fail MINT_CA=none MINT_CM=yes mint
[[ $RC == 1 && "$OUT" == *x509* && ! -e "$OUT_K" ]] && ok "a kubeconfig that cannot connect is removed and named" \
  || fail "rc=$RC: $OUT"

printf '\n%s passed, %s failed\n' "$T_PASS" "$T_FAIL"
[[ "$T_FAIL" -eq 0 ]]
