#!/usr/bin/env bash
# e2e/lib/detect-env.sh: classify the target cluster and decide whether an
# evaluation session can run. Writes JSON (default e2e/.work/env.json, the file
# run-e2e.sh reads its --env default from) and prints one `key: value` line per
# field.
#
# Exit 0 runnable, 2 must stop (missing CLI, unsupported server version, missing
# permissions, no LoadBalancer), 1 own error.
#
# Usage: detect-env.sh [--out PATH] [--lb-timeout SECS] [--skip-rbac-check]
#                      [--rbac-file PATH]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
OUT="${REPO}/e2e/.work/env.json"
LB_TIMEOUT=120
SKIP_RBAC=0
RBAC_FILE="${REPO}/rbac/evaluator.yaml"
SYSREQ="${REPO}/labs/installation/system-requirements.md"
PROBE=e2e-lb-probe
SIGNALS=()
STOP=()          # reasons the session must stop
CLASS=unknown

die() { echo "detect-env: $*" >&2; exit 1; }

while (( $# )); do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --lb-timeout) LB_TIMEOUT="$2"; shift 2 ;;
    --skip-rbac-check) SKIP_RBAC=1; shift ;;
    --rbac-file) RBAC_FILE="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

probe_clis() {  # fills CLIS_JSON; a missing CLI stops the session
  CLIS_JSON='{}'
  local name ver
  for name in kubectl helm jq python3; do
    if command -v "$name" >/dev/null 2>&1; then
      case "$name" in
        kubectl) ver="$(kubectl version --client -o json 2>/dev/null | jq -r '.clientVersion.gitVersion // "unknown"')" ;;
        helm)    ver="$(helm version --short 2>/dev/null)" ;;
        jq)      ver="$(jq --version 2>/dev/null)" ;;
        python3) ver="$(python3 --version 2>&1 | awk '{print $2}')" ;;
      esac
    else
      ver=missing; STOP+=("missing CLI: ${name}")
    fi
    CLIS_JSON="$(jq -c --arg k "$name" --arg v "${ver:-unknown}" '.[$k]=$v' <<<"$CLIS_JSON")"
  done
}

classify() {  # sets CLASS, appends SIGNALS
  local pid ctx host
  pid="$(jq -r '.items[0].spec.providerID // ""' <<<"$NODES_JSON")"
  host="$(jq -r '.items[0].metadata.labels["kubernetes.io/hostname"] // ""' <<<"$NODES_JSON")"
  ctx="$(kubectl config current-context 2>/dev/null)"
  case "$pid" in
    aws:///*)   CLASS=eks; SIGNALS+=("providerID=aws"); return ;;
    gce://*)    CLASS=gke; SIGNALS+=("providerID=gce"); return ;;
    azure:///*) CLASS=aks; SIGNALS+=("providerID=azure"); return ;;
  esac
  if [[ -n "$(kubectl api-resources --api-group=config.openshift.io -o name 2>/dev/null)" ]]; then
    CLASS=openshift; SIGNALS+=("api-group=config.openshift.io"); return
  fi
  if [[ "$pid" == vcluster://* ]]; then   # vind nodes carry vcluster:// providerIDs
    CLASS=vind; SIGNALS+=("providerID=vcluster"); return
  fi
  if [[ "$pid" == kind://* && "$host" == *-control-plane ]]; then
    CLASS=kind; SIGNALS+=("providerID=kind" "hostname=*-control-plane")
    if [[ "$ctx" == *vcluster* || "$ctx" == *vind* ]]; then
      CLASS=vind; SIGNALS+=("context=${ctx}")
    fi
    return
  fi
  SIGNALS+=("no-heuristic-matched")
}

check_version() {  # sets SERVER_VERSION, VERSION_SUPPORTED (true/false)
  SERVER_VERSION="$(kubectl version -o json 2>/dev/null | jq -r '.serverVersion.gitVersion // ""')"
  [[ -n "$SERVER_VERSION" ]] || die "cannot read the server version (is the cluster reachable?)"
  local range lo hi minor
  range="$(grep -m1 -E '1\.[0-9]+ ?[-–] ?1\.[0-9]+' "$SYSREQ" | grep -oE '1\.[0-9]+ ?[-–] ?1\.[0-9]+' | head -1)"
  VERSION_SUPPORTED=true
  if [[ -z "$range" ]]; then SIGNALS+=("version-range-unparsed"); return; fi
  lo="$(sed -E 's/^1\.([0-9]+).*/\1/' <<<"$range")"
  hi="$(sed -E 's/.*1\.([0-9]+)$/\1/' <<<"$range")"
  minor="$(sed -E 's/^v?1\.([0-9]+).*/\1/' <<<"$SERVER_VERSION")"
  if (( minor < lo || minor > hi )); then
    VERSION_SUPPORTED=false
    STOP+=("server ${SERVER_VERSION} is outside the supported range ${range}")
  fi
}

read_nodes() {  # sets NODE_COUNT CPU MEM_GIB
  read -r NODE_COUNT CPU MEM_GIB < <(python3 -c '
import json, sys, re
u = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "": 1}
cpu = mem = 0
items = json.load(sys.stdin)["items"]
for n in items:
    a = n["status"]["allocatable"]
    c = a["cpu"]; cpu += int(c[:-1]) / 1000 if c.endswith("m") else float(c)
    m = re.match(r"(\d+)(Ki|Mi|Gi)?", a["memory"]); mem += int(m[1]) * u[m[2] or ""]
print(len(items), round(cpu, 2), round(mem / 2**30, 1))' <<<"$NODES_JSON")
}

check_sizing() {  # floor = min nodes x min node size from the POC sizing table
  local nodes_min cpu_min mem_min size_row
  nodes_min="$(grep -m1 '^| Node count' "$SYSREQ" | grep -oE '[0-9]+' | head -1)"
  size_row="$(awk '/Cluster Sizing \(POC\)/{f=1} f&&/Node size/{print; exit}' "$SYSREQ")"
  cpu_min="$(grep -oE '[0-9]+' <<<"$size_row" | sed -n 1p)"
  mem_min="$(grep -oE '[0-9]+' <<<"$size_row" | sed -n 3p)"
  if [[ -z "$nodes_min" || -z "$cpu_min" || -z "$mem_min" ]]; then
    MEETS_SIZING=null; SIGNALS+=("sizing-table-unparsed"); return
  fi
  MEETS_SIZING="$(python3 -c "print(str(${CPU} >= ${nodes_min}*${cpu_min} and ${MEM_GIB} >= ${nodes_min}*${mem_min}).lower())")"
}

probe_install() {  # sets EXISTING_INSTALL
  EXISTING_INSTALL="$(helm list -A -o json 2>/dev/null \
    | jq -r '[.[] | select(.chart | startswith("enterprise-agentgateway"))
              | select(.chart | startswith("enterprise-agentgateway-crds") | not)][0].namespace // ""')"
  if [[ -z "$EXISTING_INSTALL" ]] && kubectl get crd \
      enterpriseagentgatewayparameters.enterpriseagentgateway.solo.io -o name >/dev/null 2>&1; then
    SIGNALS+=("crds-only")
  fi
}

preflight_permissions() {  # sets PERMISSIONS (json: "ok", "skipped" or a list)
  if (( SKIP_RBAC )); then PERMISSIONS='"skipped"'; return; fi
  local missing=() line verb res
  while IFS= read -r line; do
    verb="${line%% *}"; res="${line#* }"
    kubectl auth can-i "$verb" "$res" >/dev/null 2>&1 || missing+=("$line")
  done < <(python3 -c '
import sys, yaml
seen = set()
for doc in yaml.safe_load_all(open(sys.argv[1])):
    if not doc or doc.get("kind") not in ("ClusterRole", "Role"):
        continue
    for r in doc.get("rules") or []:
        for g in r.get("apiGroups") or [""]:
            for res in r.get("resources") or []:
                for v in r.get("verbs") or []:
                    s = f"{v} {res}.{g}" if g else f"{v} {res}"
                    if s not in seen:
                        seen.add(s); print(s)' "$RBAC_FILE") || die "cannot parse ${RBAC_FILE}"
  if (( ${#missing[@]} )); then
    PERMISSIONS="$(printf '%s\n' "${missing[@]}" | jq -R . | jq -sc .)"
    STOP+=("$(printf 'Apply rbac/evaluator.yaml with an admin identity, or run as an identity that holds these permissions:\n  %s' \
      "$(printf '%s\n  ' "${missing[@]}" | sed '$d')")")
  else
    PERMISSIONS='"ok"'
  fi
}

probe_lb() {  # sets LOADBALANCER; the probe Service is removed by the EXIT trap
  trap 'kubectl delete svc "$PROBE" -n default --ignore-not-found >/dev/null 2>&1' EXIT
  kubectl create -f - >/dev/null <<Y || die "cannot create Service/${PROBE}"
apiVersion: v1
kind: Service
metadata:
  name: ${PROBE}
  namespace: default
spec:
  type: LoadBalancer
  ports:
    - port: 80
      targetPort: 80
Y
  local deadline=$((SECONDS + LB_TIMEOUT)) ing
  LOADBALANCER=none
  while :; do
    ing="$(kubectl get svc "$PROBE" -n default -o json 2>/dev/null | jq -r '.status.loadBalancer.ingress[0] // empty')"
    if [[ -n "$ing" ]]; then LOADBALANCER=ok; return; fi
    (( SECONDS >= deadline )) && break
    sleep $(( deadline - SECONDS < 5 ? deadline - SECONDS : 5 ))
  done
  STOP+=("No LoadBalancer address after ${LB_TIMEOUT}s. Start cloud-provider-kind (kind/vind) or use a cluster with a cloud LoadBalancer.")
}

write_json() {
  mkdir -p "$(dirname "$OUT")"
  jq -n --arg class "$CLASS" --arg sv "$SERVER_VERSION" --argjson vs "$VERSION_SUPPORTED" \
    --argjson nodes "$NODE_COUNT" --argjson cpu "$CPU" --argjson mem "$MEM_GIB" \
    --argjson ms "$MEETS_SIZING" --arg lb "$LOADBALANCER" --arg inst "$EXISTING_INSTALL" \
    --arg ident "$IDENTITY" --argjson perms "$PERMISSIONS" --argjson clis "$CLIS_JSON" \
    --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson sig "$(printf '%s\n' "${SIGNALS[@]}" | jq -R . | jq -sc .)" \
    '{class:$class, server_version:$sv, version_supported:$vs, nodes:$nodes, cpu_allocatable:$cpu,
      memory_allocatable:$mem, meets_sizing:$ms, loadbalancer:$lb, existing_install:$inst,
      identity:$ident, permissions:$perms, clis:$clis, detected_at:$at, signals:$sig}' >"$OUT"
}

probe_clis
if (( ${#STOP[@]} )); then   # kubectl/jq may be absent: report and stop before using them
  printf 'detect-env: %s\n' "${STOP[@]}" >&2; exit 2
fi
NODES_JSON="$(kubectl get nodes -o json 2>/dev/null)" || die "cannot list nodes (is the cluster reachable?)"
classify
check_version
read_nodes
check_sizing
probe_install
IDENTITY="$(kubectl auth whoami -o json 2>/dev/null | jq -r '.status.userInfo.username // empty')"
[[ -n "$IDENTITY" ]] || { IDENTITY=unknown; SIGNALS+=("whoami-unsupported"); }
preflight_permissions
if (( ${#STOP[@]} )); then
  LOADBALANCER=unchecked; SIGNALS+=("lb-probe-skipped")
else
  probe_lb
fi
write_json
jq -r 'to_entries[] | "\(.key): \(.value|if type=="string" then . else tojson end)"' "$OUT"
if (( ${#STOP[@]} )); then printf 'detect-env: %s\n' "${STOP[@]}" >&2; exit 2; fi
exit 0
