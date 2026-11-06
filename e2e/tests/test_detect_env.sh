#!/usr/bin/env bash
# Tests for lib/detect-env.sh. Offline: kubectl and helm are stubs that answer
# from the fixture directory named by DETECT_FIXTURE.
E2E="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DETECT="${E2E}/lib/detect-env.sh"
FX="${E2E}/tests/fixtures/detect"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export PATH="${FX}/bin:${PATH}"
export DETECT_LOG="${TMP}/calls.log"

T_PASS=0; T_FAIL=0
ok()   { T_PASS=$((T_PASS+1)); printf 'ok   %s\n' "$1"; }
fail() { T_FAIL=$((T_FAIL+1)); printf 'FAIL %s\n' "$1"; }

OUT=""; RC=0; JSON="${TMP}/env.json"
detect() {  # detect <fixture> <args...>; sets OUT, RC
  local fixture="$1"; shift
  : > "$DETECT_LOG"; rm -f "$JSON"
  RC=0
  OUT="$(DETECT_FIXTURE="${FX}/${fixture}" "$DETECT" --out "$JSON" "$@" 2>&1)" || RC=$?
}
detect_dir() {  # detect_dir <fixture path> <args...>; like detect with an explicit dir
  local fx="$1"; shift
  : > "$DETECT_LOG"; rm -f "$JSON"; RC=0
  OUT="$(DETECT_FIXTURE="$fx" "$DETECT" --out "$JSON" "$@" 2>&1)" || RC=$?
}
field() { jq -r "$1" "$JSON" 2>/dev/null; }
check() {  # check <desc> <actual> <expected>
  if [[ "$2" == "$3" ]]; then ok "$1"; else fail "$1 (got '$2', wanted '$3')"; fi
}

test_classifies_eks_gke_vind() {
  local c
  for c in eks gke vind; do
    detect "$c" --skip-rbac-check
    check "classifies $c (rc=$RC)" "$(field .class)" "$c"
  done
  detect kind-no-lb --skip-rbac-check --lb-timeout 1
  check "classifies kind" "$(field .class)" kind
}

test_lb_timeout_exits_2_and_deletes_probe() {
  detect kind-no-lb --skip-rbac-check --lb-timeout 1
  check "no LB exits 2" "$RC" 2
  [[ "$OUT" == *cloud-provider-kind* ]] && ok "no LB names cloud-provider-kind" || fail "no LB message: $OUT"
  grep -q 'delete.*e2e-lb-probe' "$DETECT_LOG" && ok "probe Service deleted" || fail "probe not deleted: $(cat "$DETECT_LOG")"
  check "loadbalancer none" "$(field .loadbalancer)" none
  # Probe skipped because a stop reason already exists: still "none".
  local fx="${TMP}/skip"; cp -R "${FX}/eks" "$fx"; echo "delete namespaces" > "${fx}/can-i-no"
  detect_dir "$fx"
  check "skipped probe exits 2" "$RC" 2
  check "skipped probe loadbalancer is none" "$(field .loadbalancer)" none
  [[ "$(field '.signals|index("lb-probe-skipped")')" != null ]] && ok "lb-probe-skipped signal" || fail "lb-probe-skipped missing"
  grep -q 'create' "$DETECT_LOG" && fail "probe created despite stop" || ok "no probe created when skipped"
}

test_helm_failure_is_unknown_not_clean() {
  local fx="${TMP}/helmfail"; cp -R "${FX}/eks" "$fx"; touch "${fx}/helm-fail"
  detect_dir "$fx" --skip-rbac-check
  check "helm failure exits 0" "$RC" 0
  check "existing_install unknown" "$(field .existing_install)" unknown
  [[ "$(field '.signals|index("helm-list-failed")')" != null ]] && ok "helm-list-failed signal" || fail "signal missing"
}

test_can_i_api_error_fails_closed() {
  local fx="${TMP}/cierr"; cp -R "${FX}/eks" "$fx"; echo "create namespaces" > "${fx}/can-i-error"
  detect_dir "$fx"
  check "can-i error exits 2" "$RC" 2
  check "errored pair is listed" "$(field '.permissions|index("create namespaces")|type')" number
  [[ "$(field '.signals|index("can-i-error:create namespaces")')" != null ]] && ok "can-i-error signal" || fail "signal missing"
  check "other pairs not listed" "$(field '.permissions|length')" 1
}

test_openshift_wins_over_cloud_providerid() {
  local fx="${TMP}/ocp"; cp -R "${FX}/eks" "$fx"; touch "${fx}/openshift"
  detect_dir "$fx" --skip-rbac-check
  check "openshift on aws" "$(field .class)" openshift
}

test_empty_node_list_is_own_error() {
  local fx="${TMP}/nonodes"; cp -R "${FX}/eks" "$fx"; echo '{"items": []}' > "${fx}/nodes.json"
  detect_dir "$fx" --skip-rbac-check
  check "empty nodes exits 1" "$RC" 1
}

test_reports_existing_install_namespace() {
  detect existing-install --skip-rbac-check
  check "existing_install namespace" "$(field .existing_install)" agw-other
  detect eks --skip-rbac-check
  check "no install is empty string" "$(field .existing_install)" ""
  local fx="${TMP}/crd"; cp -R "${FX}/eks" "$fx"; touch "${fx}/crd"
  detect_dir "$fx" --skip-rbac-check
  check "crds-only install namespace" "$(field .existing_install)" ""
  [[ "$(field '.signals|index("crds-only")')" != null ]] && ok "crds-only signal" || fail "crds-only signal missing"
}

test_signals_lists_the_heuristic() {
  detect eks --skip-rbac-check
  [[ "$(field '.signals|index("providerID=aws")')" != null ]] && ok "signals has providerID=aws" || fail "signals: $(field .signals)"
  detect vind --skip-rbac-check
  [[ "$(field '.signals|index("providerID=vcluster")')" != null ]] && ok "signals has providerID=vcluster" || fail "signals: $(field .signals)"
}

test_json_fields_and_summary() {
  detect eks --skip-rbac-check
  local f
  for f in class server_version version_supported nodes cpu_allocatable memory_allocatable \
           meets_sizing loadbalancer existing_install identity permissions clis detected_at signals; do
    [[ "$(jq "has(\"$f\")" "$JSON")" == true ]] && ok "field $f" || fail "field $f missing"
  done
  check "version_supported" "$(field .version_supported)" true
  check "meets_sizing" "$(field .meets_sizing)" true
  check "identity" "$(field .identity)" tester
  check "permissions skipped" "$(field .permissions)" skipped
  check "loadbalancer ok" "$(field .loadbalancer)" ok
  [[ "$OUT" == *"existing_install:"* && "$OUT" == *"permissions:"* ]] && ok "summary lines" || fail "summary: $OUT"
}

test_version_out_of_range_exits_2() {
  local fx="${TMP}/oldver"; cp -R "${FX}/eks" "$fx"
  echo '{"serverVersion": {"gitVersion": "v1.28.0"}}' > "${fx}/version.json"
  : > "$DETECT_LOG"; RC=0
  OUT="$(DETECT_FIXTURE="$fx" "$DETECT" --out "$JSON" --skip-rbac-check 2>&1)" || RC=$?
  check "old server exits 2" "$RC" 2
  check "version_supported false" "$(field .version_supported)" false
}

test_permission_preflight_names_missing_verb_resource() {
  local fx="${TMP}/perm" rbac="${TMP}/rbac.yaml"; cp -R "${FX}/eks" "$fx"
  echo "delete podmonitors.monitoring.coreos.com" > "${fx}/can-i-no"
  cat > "$rbac" <<'Y'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: t
rules:
  - apiGroups:
      - monitoring.coreos.com
    resources:
      - podmonitors
    verbs:
      - get
      - delete
  - apiGroups:
      - ""
    resources:
      - namespaces
    verbs:
      - get
Y
  : > "$DETECT_LOG"; RC=0
  OUT="$(DETECT_FIXTURE="$fx" "$DETECT" --out "$JSON" --rbac-file "$rbac" 2>&1)" || RC=$?
  check "missing permission exits 2" "$RC" 2
  [[ "$OUT" == *"delete podmonitors.monitoring.coreos.com"* ]] && ok "names missing verb resource" || fail "output: $OUT"
  [[ "$OUT" != *"get podmonitors"* ]] && ok "granted verbs not listed" || fail "listed granted verb"
  [[ "$OUT" == *"Apply rbac/evaluator.yaml"* ]] && ok "remedy message" || fail "remedy missing"
  [[ "$(field '.permissions|type')" == array ]] && ok "permissions is a list" || fail "permissions not a list"

  detect eks   # placeholder rbac, stub answers yes
  check "placeholder rbac passes" "$(field .permissions)" ok
  check "placeholder rbac exit" "$RC" 0
}

test_classifies_eks_gke_vind
test_lb_timeout_exits_2_and_deletes_probe
test_reports_existing_install_namespace
test_signals_lists_the_heuristic
test_json_fields_and_summary
test_version_out_of_range_exits_2
test_permission_preflight_names_missing_verb_resource
test_helm_failure_is_unknown_not_clean
test_openshift_wins_over_cloud_providerid
test_can_i_api_error_fails_closed
test_empty_node_list_is_own_error

printf '\n%d passed, %d failed\n' "$T_PASS" "$T_FAIL"
(( T_FAIL == 0 ))
