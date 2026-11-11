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
  grep -q 'delete svc -n default -l agw-e2e/lb-probe=true' "$DETECT_LOG" \
    && ok "probes from killed runs deleted first" || fail "no stale-probe delete: $(cat "$DETECT_LOG")"
  check "loadbalancer none" "$(field .loadbalancer)" none
  # Probe skipped because a stop reason already exists: still "none".
  local fx="${TMP}/skip"; cp -R "${FX}/eks" "$fx"; echo "delete namespaces" > "${fx}/can-i-no"
  detect_dir "$fx"
  check "skipped probe exits 2" "$RC" 2
  check "skipped probe loadbalancer is none" "$(field .loadbalancer)" none
  [[ "$(field '.signals|index("lb-probe-skipped")')" != null ]] && ok "lb-probe-skipped signal" || fail "lb-probe-skipped missing"
  grep -q 'create' "$DETECT_LOG" && fail "probe created despite stop" || ok "no probe created when skipped"
}

test_preflight_subresource_resourcenames_and_absent_crd() {
  local fx="${TMP}/shape" rbac="${TMP}/shape.yaml"; cp -R "${FX}/eks" "$fx"
  printf '%s\n' "create pods --subresource=portforward" "escalate clusterroles.rbac.authorization.k8s.io/foo" > "${fx}/can-i-no"
  echo "get widgets.example.io" > "${fx}/can-i-notype"
  cat > "$rbac" <<'Y'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: t
rules:
  - apiGroups:
      - ""
    resources:
      - pods/portforward
    verbs:
      - create
  - apiGroups:
      - rbac.authorization.k8s.io
    resources:
      - clusterroles
    resourceNames:
      - foo
      - bar
    verbs:
      - escalate
  - apiGroups:
      - example.io
    resources:
      - widgets
    verbs:
      - get
Y
  : > "$DETECT_LOG"; RC=0
  OUT="$(DETECT_FIXTURE="$fx" "$DETECT" --out "$JSON" --rbac-file "$rbac" 2>&1)" || RC=$?
  check "subresource and resourceNames denials exit 2" "$RC" 2
  [[ "$OUT" == *"create pods/portforward"* ]] && ok "subresource checked with --subresource" || fail "output: $OUT"
  [[ "$OUT" == *"escalate clusterroles.rbac.authorization.k8s.io"* ]] && ok "resourceNames rule checked by name" || fail "output: $OUT"
  [[ "$(field '.permissions|tostring')" != *widgets* ]] && ok "absent CRD type is not reported missing" || fail "absent type reported: $OUT"
  [[ "$(field '.signals|index("can-i-unchecked:get widgets.example.io")')" != null ]] && ok "absent CRD type raises can-i-unchecked signal" || fail "signals: $(field .signals)"
  # a denial on the second resourceName alone is still a missing permission
  echo "escalate clusterroles.rbac.authorization.k8s.io/bar" > "${fx}/can-i-no"
  : > "$DETECT_LOG"; RC=0
  OUT="$(DETECT_FIXTURE="$fx" "$DETECT" --out "$JSON" --rbac-file "$rbac" 2>&1)" || RC=$?
  check "denial on second resourceName exits 2" "$RC" 2
  [[ "$(field '.permissions|tostring')" == *"escalate clusterroles.rbac.authorization.k8s.io"* ]] && ok "second resourceName checked" || fail "output: $OUT"
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

test_namespaces_and_skip_lb_probe() {
  detect eks --skip-rbac-check --skip-lb-probe
  check "skip-lb-probe exits 0" "$RC" 0
  check "loadbalancer skipped" "$(field .loadbalancer)" skipped
  grep -q 'create' "$DETECT_LOG" && fail "probe created despite --skip-lb-probe" || ok "no probe with --skip-lb-probe"
  check "namespaces snapshot sorted, prefix stripped" "$(field '.namespaces|join(",")')" "agw-evaluator,default,kube-system"
}

test_old_bash_on_path_stops() {
  local bin="${TMP}/oldbash"; mkdir -p "$bin"
  # Answers the version probe as bash 3.2; everything else (the script's own
  # shebang, the kubectl stub) runs under the real bash.
  printf '#!/bin/sh\n[ "$1" = -c ] && { echo 3.2; exit 0; }\nexec "%s" "$@"\n' "$(command -v bash)" > "${bin}/bash"
  chmod +x "${bin}/bash"
  RC=0; OUT="$(PATH="${bin}:${PATH}" DETECT_FIXTURE="${FX}/eks" "$DETECT" --out "$JSON" --skip-rbac-check 2>&1)" || RC=$?
  check "bash 3 on PATH exits 2" "$RC" 2
  [[ "$OUT" == *"install bash 4+"* ]] && ok "bash 3 names the fix" || fail "output: $OUT"
}

test_unparsable_version_range_is_null() {
  local repo="${TMP}/repo"
  mkdir -p "${repo}/e2e/lib" "${repo}/labs/installation" "${repo}/rbac"
  cp "$DETECT" "${repo}/e2e/lib/"
  cp "${E2E}/../rbac/evaluator.yaml" "${repo}/rbac/"
  sed -E 's/1\.[0-9]+ ?[-–] ?1\.[0-9]+/see the release notes/g' "${E2E}/../labs/installation/system-requirements.md" \
    > "${repo}/labs/installation/system-requirements.md"
  RC=0; OUT="$(DETECT_FIXTURE="${FX}/eks" "${repo}/e2e/lib/detect-env.sh" --out "$JSON" --skip-rbac-check 2>&1)" || RC=$?
  check "unparsable range exits 0" "$RC" 0
  check "version_supported null" "$(field .version_supported)" null
  [[ "$OUT" == *"was not checked"* ]] && ok "unparsable range prints a note" || fail "output: $OUT"
}

test_missing_permission_under_system_bash() {
  [[ -x /bin/bash ]] || { ok "no /bin/bash (skipped)"; return; }
  local fx="${TMP}/sysbash"; cp -R "${FX}/eks" "$fx"; echo "delete namespaces" > "${fx}/can-i-no"
  RC=0; OUT="$(DETECT_FIXTURE="$fx" /bin/bash "$DETECT" --out "$JSON" 2>&1)" || RC=$?
  check "missing permission under $(/bin/bash -c 'echo $BASH_VERSION') exits 2" "$RC" 2
  [[ "$OUT" != *"unbound variable"* ]] && ok "no unbound variable under /bin/bash" || fail "output: $OUT"
  [[ "$OUT" == *"delete namespaces"* ]] && ok "/bin/bash names the missing permission" || fail "output: $OUT"
}

test_classifies_eks_gke_vind
test_lb_timeout_exits_2_and_deletes_probe
test_reports_existing_install_namespace
test_signals_lists_the_heuristic
test_json_fields_and_summary
test_version_out_of_range_exits_2
test_permission_preflight_names_missing_verb_resource
test_preflight_subresource_resourcenames_and_absent_crd
test_helm_failure_is_unknown_not_clean
test_openshift_wins_over_cloud_providerid
test_can_i_api_error_fails_closed
test_empty_node_list_is_own_error
test_namespaces_and_skip_lb_probe
test_old_bash_on_path_stops
test_unparsable_version_range_is_null
test_missing_permission_under_system_bash

printf '\n%d passed, %d failed\n' "$T_PASS" "$T_FAIL"
(( T_FAIL == 0 ))
