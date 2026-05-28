#!/bin/bash

# lib/observability/agentgateway/update-dashboard.sh
# Updates the AgentGateway Grafana dashboard ConfigMap

set -e

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configuration
CONTEXT="${1:-${MGMT_CLUSTER:-cluster1}}"
NAMESPACE="monitoring"
CONFIGMAP_NAME="agentgateway-dashboard"
DASHBOARD_FILE="${SCRIPT_DIR}/agentgateway-grafana-dashboard-v1.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
  echo -e "${GREEN}[INFO]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $1"
}

# Validate dashboard file exists
if [[ ! -f "$DASHBOARD_FILE" ]]; then
  log_error "Dashboard file not found: $DASHBOARD_FILE"
  exit 1
fi

log_info "Updating AgentGateway Grafana dashboard on context: $CONTEXT"
log_info "Dashboard file: $DASHBOARD_FILE"

# Check if ConfigMap exists
if kubectl get configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" --context "$CONTEXT" &>/dev/null; then
  log_info "Deleting existing ConfigMap: $CONFIGMAP_NAME"
  kubectl delete configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" --context "$CONTEXT"
else
  log_warn "ConfigMap $CONFIGMAP_NAME not found, creating new one"
fi

# Create new ConfigMap with updated dashboard
log_info "Creating ConfigMap with updated dashboard..."
kubectl create configmap "$CONFIGMAP_NAME" \
  --from-file=agentgateway-overview.json="$DASHBOARD_FILE" \
  --namespace "$NAMESPACE" \
  --context "$CONTEXT" \
  --dry-run=client -o yaml | \
  kubectl label -f - \
    grafana_dashboard="1" \
    --local --dry-run=client -o yaml | \
  kubectl create -f - --context "$CONTEXT"

log_info "✅ Dashboard ConfigMap updated successfully"
log_info "   Grafana sidecar will automatically reload the dashboard in a few seconds"
log_info ""
log_info "To access Grafana:"
log_info "   kubectl port-forward -n $NAMESPACE svc/grafana-prometheus 3000:3000 --context $CONTEXT"
log_info "   Then open: http://localhost:3000"
