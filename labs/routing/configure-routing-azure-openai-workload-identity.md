# Configure Routing to Azure OpenAI using Azure Workload Identity

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

This lab also requires:
- An **AKS cluster** with the OIDC issuer and Microsoft Entra Workload ID enabled (Workload Identity is an AKS-specific feature analogous to EKS IRSA)
- `az` CLI installed and logged in (`az login`)
- An Azure OpenAI resource
- Permission to create a user-assigned managed identity and role assignments in your subscription

## Lab Objectives
- Configure Azure Workload Identity so the agentgateway pod can obtain Microsoft Entra ID tokens for a user-assigned managed identity, without storing an API key as a Kubernetes secret
- Create a federated identity credential linking the AKS OIDC issuer and the agentgateway service account to the managed identity
- Deploy an `EnterpriseAgentgatewayBackend` for Azure OpenAI that relies on Workload Identity instead of secret-based auth
- Annotate the data plane service account and pod template via `EnterpriseAgentgatewayParameters`
- Verify end-to-end connectivity with Azure OpenAI using federated Workload Identity credentials

## Overview

This lab configures Enterprise Agentgateway to access Azure OpenAI through AKS Workload Identity. AKS Workload Identity federates a Kubernetes service account with a user-assigned managed identity, so the agentgateway pod obtains Microsoft Entra ID tokens natively, with no API key stored as a Kubernetes secret.

### Why Use Workload Identity
- No API keys to manage or rotate
- Credentials are short-lived Entra ID tokens, automatically refreshed
- Fine-grained RBAC scoped to a single service account via a dedicated managed identity
- Follows Azure security best practices for AKS workloads

| Approach | Credentials | Rotation | Scope |
|---|---|---|---|
| **Secret-based** ([Azure OpenAI lab](configure-routing-azure-openai.md)) | Static API key in K8s Secret | Manual | Any pod with secret access |
| **Workload Identity** (this lab) | Federated Entra ID token | Automatic | Single service account |

Workload Identity is the recommended approach for production AKS deployments accessing Azure services.

### How Workload Identity Works
1. You enable the AKS cluster's OIDC issuer
2. You create a federated identity credential on the managed identity, trusting the agentgateway service account's token subject
3. You annotate the service account with the managed identity's client ID and label the pod template `azure.workload.identity/use: "true"`
4. AKS's workload identity webhook injects a projected token and environment variables into the pod; agentgateway exchanges the token for an Entra ID access token via `DefaultAzureCredential`

## Set Environment Variables

Update these values for your environment:

```bash
export AKS_CLUSTER_NAME="<your-aks-cluster-name>"
export RESOURCE_GROUP="<your-resource-group>"
export LOCATION="<your-azure-region>"
export NAMESPACE="agentgateway-system"
export SA_NAME="agentgateway-proxy"
export UMI_NAME="agentgateway-azure-openai-umi"
export ENDPOINT="<AZURE-OPENAI-ENDPOINT>"  # Just the hostname, no https://
export AZURE_OPENAI_RESOURCE_NAME="<your-azure-openai-resource-name>"

export SUBSCRIPTION_ID=$(az account show --query id --output tsv)
echo "Subscription ID: $SUBSCRIPTION_ID"
echo "Cluster: $AKS_CLUSTER_NAME"
echo "Resource Group: $RESOURCE_GROUP"
```

## Verify Cluster Access

```bash
kubectl config current-context
kubectl get ns $NAMESPACE
```

## Enable the AKS OIDC Issuer and Workload Identity

Skip this step if your cluster already has both enabled.

```bash
az aks update \
  --name $AKS_CLUSTER_NAME \
  --resource-group $RESOURCE_GROUP \
  --enable-oidc-issuer \
  --enable-workload-identity
```

## Get the OIDC Issuer URL

```bash
export OIDC_ISSUER=$(az aks show \
  --name $AKS_CLUSTER_NAME \
  --resource-group $RESOURCE_GROUP \
  --query "oidcIssuerProfile.issuerUrl" \
  --output tsv)

echo "OIDC Issuer: $OIDC_ISSUER"
```

## Create the User-Assigned Managed Identity

```bash
az identity create \
  --name $UMI_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION

export UMI_CLIENT_ID=$(az identity show \
  --name $UMI_NAME \
  --resource-group $RESOURCE_GROUP \
  --query "clientId" \
  --output tsv)

export UMI_PRINCIPAL_ID=$(az identity show \
  --name $UMI_NAME \
  --resource-group $RESOURCE_GROUP \
  --query "principalId" \
  --output tsv)

echo "UMI Client ID: $UMI_CLIENT_ID"
```

## Grant the Managed Identity Access to Azure OpenAI

```bash
export AOAI_RESOURCE_ID=$(az cognitiveservices account show \
  --name $AZURE_OPENAI_RESOURCE_NAME \
  --resource-group $RESOURCE_GROUP \
  --query "id" \
  --output tsv)

az role assignment create \
  --assignee-object-id $UMI_PRINCIPAL_ID \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" \
  --scope $AOAI_RESOURCE_ID
```

**Note:** Azure RBAC role assignments can take a few minutes to propagate.

## Create the Federated Identity Credential

The command below trusts only the agentgateway service account in the `agentgateway-system` namespace to federate as the managed identity:

```bash
az identity federated-credential create \
  --name agentgateway-workload-identity \
  --identity-name $UMI_NAME \
  --resource-group $RESOURCE_GROUP \
  --issuer $OIDC_ISSUER \
  --subject "system:serviceaccount:${NAMESPACE}:${SA_NAME}" \
  --audience api://AzureADTokenExchange
```

## Create the Azure OpenAI Backend

Note: the missing `policies.auth` section tells agentgateway to use the pod's own Entra ID credentials, provided via Workload Identity and resolved through `DefaultAzureCredential`.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: azure-openai-wi
  namespace: agentgateway-system
spec:
  ai:
    provider:
      azureopenai:
        endpoint: "${ENDPOINT}"
EOF
```

**Note:** if you'd rather not rely on `DefaultAzureCredential`'s fallback chain (for example, the node also has a system-assigned managed identity attached and you want to pin the credential source unambiguously), specify Workload Identity explicitly instead:

```yaml
  policies:
    auth:
      azure:
        workloadIdentity: {}
```

## Annotate the Service Account and Pod Template

The patch below adds the `azure.workload.identity/client-id` annotation to the agentgateway data plane service account, and the `azure.workload.identity/use: "true"` label to the pod template. AKS's workload identity webhook only mutates pods carrying that label.

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type merge \
  -p '{
    "spec": {
      "serviceAccount": {
        "metadata": {
          "annotations": {
            "azure.workload.identity/client-id": "'$UMI_CLIENT_ID'"
          }
        }
      },
      "deployment": {
        "spec": {
          "template": {
            "metadata": {
              "labels": {
                "azure.workload.identity/use": "true"
              }
            }
          }
        }
      }
    }
  }'

# Verify annotation on service account
kubectl get sa $SA_NAME -n agentgateway-system -o json | jq '.metadata.annotations'
```

## Create the HTTP Route

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: azure-openai-wi
  namespace: agentgateway-system
  labels:
    example: azure-openai-workload-identity-route
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
  - matches:
      - path:
          type: PathPrefix
          value: /azure
    backendRefs:
      - name: azure-openai-wi
        group: enterpriseagentgateway.solo.io
        kind: EnterpriseAgentgatewayBackend
    timeouts:
      request: "120s"
EOF
```

## Restart the Agentgateway Deployment

The pods need to be restarted so the AKS workload identity webhook can inject the projected token, environment variables, and pod label:

```bash
kubectl rollout restart deployment agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment agentgateway-proxy -n agentgateway-system --timeout=120s
```

## Verify Workload Identity Injection

Confirm that the AKS webhook injected the correct environment variables and projected token volume into the agentgateway pod:

```bash
POD=$(kubectl get pods -n agentgateway-system \
  -l app.kubernetes.io/name=agentgateway-proxy \
  -o jsonpath='{.items[0].metadata.name}')

echo "Pod: $POD"
echo ""
echo "=== Workload Identity Environment Variables ==="
kubectl get pod $POD -n agentgateway-system \
  -o jsonpath='{range .spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' \
  | grep -E "AZURE_CLIENT_ID|AZURE_TENANT_ID|AZURE_FEDERATED_TOKEN_FILE|AZURE_AUTHORITY_HOST"

echo ""
echo "=== Projected Token Volume ==="
kubectl get pod $POD -n agentgateway-system -o json \
  | jq '.spec.volumes[] | select(.name=="azure-identity-token")'
```

You should see:
- `AZURE_CLIENT_ID` set to your managed identity's client ID
- `AZURE_FEDERATED_TOKEN_FILE` set to `/var/run/secrets/azure/tokens/azure-identity-token`
- A projected volume named `azure-identity-token` supplying the federated token

## Test Azure OpenAI Access

### Get the Gateway Address
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### curl Azure OpenAI via Workload Identity
```bash
curl -i "$GATEWAY_IP:8080/azure" \
  -H "content-type: application/json" \
  -d '{
    "model": "<YOUR-MODEL-NAME>",
    "messages": [
      {
        "role": "user",
        "content": "Say hello in one sentence."
      }
    ]
  }'
```

## Observability

### View Metrics Endpoint

AgentGateway exposes Prometheus-compatible metrics at the `/metrics` endpoint. You can curl this endpoint directly:

```bash
kubectl port-forward -n agentgateway-system deployment/agentgateway-proxy 15020:15020 & \
sleep 1 && curl -s http://localhost:15020/metrics && kill $!
```

### View Metrics and Traces in Grafana

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](../../002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

1. Port-forward to the Grafana service:
```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

2. Open http://localhost:3000 in your browser

3. Login with credentials:
   - Username: `admin`
   - Password: Value of `$GRAFANA_ADMIN_PASSWORD` (default: `prom-operator`)

4. Navigate to **Dashboards > AgentGateway Dashboard** to view metrics

The dashboard provides real-time visualization of:
- Core GenAI metrics (request rates, token usage by model)
- Streaming metrics (TTFT, TPOT)
- MCP metrics (tool calls, server requests)
- Connection and runtime metrics

### View Traces in Grafana

To view distributed traces with LLM-specific spans:

1. In Grafana, navigate to **Home > Explore**
2. Select **Tempo** from the data source dropdown
3. Click **Search** to see all traces
4. Filter traces by service, operation, or trace ID to find AgentGateway requests

Traces include LLM-specific spans with information like `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more.

### View Access Logs

AgentGateway automatically logs detailed information about LLM requests to stdout:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Example output includes the model name, token usage, and trace ID for correlating with distributed traces in Grafana.

## Cleanup
```bash
# Remove Kubernetes resources
kubectl delete httproute -n agentgateway-system azure-openai-wi
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system azure-openai-wi

# Remove Workload Identity annotation and label from EnterpriseAgentgatewayParameters
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type json \
  -p '[{"op": "remove", "path": "/spec/serviceAccount"}, {"op": "remove", "path": "/spec/deployment"}]'

# Restart pods to drop Workload Identity credentials
kubectl rollout restart deployment agentgateway-proxy -n agentgateway-system

# Remove the federated identity credential (keeps the managed identity for re-use)
az identity federated-credential delete \
  --name agentgateway-workload-identity \
  --identity-name $UMI_NAME \
  --resource-group $RESOURCE_GROUP \
  --yes

# (Optional) Delete the managed identity entirely if no longer needed
# az identity delete --name $UMI_NAME --resource-group $RESOURCE_GROUP

echo "Cleanup complete."
```
