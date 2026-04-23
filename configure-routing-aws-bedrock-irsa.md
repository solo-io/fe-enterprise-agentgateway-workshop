# Configure Routing to AWS Bedrock with IRSA (IAM Roles for Service Accounts)

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

Additionally, this lab requires:
- An **EKS cluster** (IRSA is an EKS-specific feature)
- `eksctl` CLI installed and configured
- AWS IAM permissions to create roles and OIDC providers
- Amazon Bedrock model access enabled in your region

## Lab Objectives
- Configure IRSA so the agentgateway pod can assume an IAM role for Bedrock access without storing AWS credentials as Kubernetes secrets
- Associate the EKS cluster OIDC provider with AWS IAM
- Create an IAM role with a trust policy scoped to the agentgateway service account
- Deploy an `AgentgatewayBackend` for Bedrock that relies on IRSA instead of secret-based auth
- Annotate the data plane service account via `EnterpriseAgentgatewayParameters`
- Verify end-to-end connectivity with Bedrock using temporary IRSA credentials

## Overview

This lab configures Enterprise Agentgateway to access AWS Bedrock **without storing AWS credentials as Kubernetes secrets**. Instead, we use EKS IAM Roles for Service Accounts (IRSA) to let the agentgateway pod assume an IAM role natively.

### Why IRSA?
- No long-lived AWS access keys to manage or rotate
- Credentials are short-lived and automatically refreshed
- Fine-grained IAM permissions scoped to a single service account
- Follows AWS security best practices for EKS workloads

| Approach | Credentials | Rotation | Scope |
|---|---|---|---|
| **Secret-based** ([Bedrock lab](configure-routing-aws-bedrock.md)) | Static access key in K8s Secret | Manual | Any pod with secret access |
| **API Key** ([Bedrock API Key lab](configure-routing-aws-bedrock-apikey.md)) | Bearer token in K8s Secret | Manual (short-term: 12h, long-term: configurable) | Any pod with secret access |
| **IRSA** (this lab) | Temporary STS credentials | Automatic | Single service account |

IRSA is the recommended approach for production EKS deployments accessing AWS services.

### How IRSA Works
1. The EKS cluster's OIDC issuer is associated with AWS IAM
2. An IAM role is created that trusts a specific Kubernetes service account
3. The service account is annotated with the role ARN
4. EKS automatically injects temporary AWS credentials into the pod

## Set Environment Variables

Update these values for your environment:

```bash
export CLUSTER_NAME="<your-eks-cluster-name>"
export CLUSTER_AWS_REGION="us-west-2"
export BEDROCK_REGION="us-west-2"
export NAMESPACE="agentgateway-system"
export SA_NAME="agentgateway-proxy"
export ROLE_NAME="agentgateway-bedrock-irsa"

# Auto-detect account ID
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account ID: $ACCOUNT_ID"
echo "Cluster: $CLUSTER_NAME"
echo "Cluster Region: $CLUSTER_AWS_REGION"
echo "Bedrock Region: $BEDROCK_REGION"
```

## Verify Cluster Access

```bash
kubectl config current-context
kubectl get ns $NAMESPACE
```

## Verify Bedrock Model Access

```bash
aws bedrock get-foundation-model \
  --model-identifier us.amazon.nova-micro-v1:0 \
  --region $BEDROCK_REGION \
  --query 'modelDetails.{modelId:modelId,status:modelLifecycle.status}' \
  --output table
```

## Associate the EKS OIDC Provider with IAM

This registers your cluster's OIDC issuer so IAM can validate service account tokens:

```bash
eksctl utils associate-iam-oidc-provider \
  --cluster $CLUSTER_NAME \
  --approve
```

## Get the OIDC Provider ID

```bash
export OIDC_ISSUER=$(aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --query "cluster.identity.oidc.issuer" \
  --output text)

export OIDC_ID=$(echo $OIDC_ISSUER | sed 's|.*/id/||')

echo "OIDC Issuer: $OIDC_ISSUER"
echo "OIDC ID: $OIDC_ID"
```

## Create the IAM Trust Policy

This policy allows only the agentgateway service account in the `agentgateway-system` namespace to assume the role:

```bash
cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/oidc.eks.${CLUSTER_AWS_REGION}.amazonaws.com/id/${OIDC_ID}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.${CLUSTER_AWS_REGION}.amazonaws.com/id/${OIDC_ID}:sub": "system:serviceaccount:${NAMESPACE}:${SA_NAME}",
          "oidc.eks.${CLUSTER_AWS_REGION}.amazonaws.com/id/${OIDC_ID}:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
EOF

cat /tmp/trust-policy.json | python3 -m json.tool
```

## Create the IAM Role

```bash
if aws iam get-role --role-name $ROLE_NAME &>/dev/null; then
  echo "Role $ROLE_NAME already exists, updating trust policy..."
  aws iam update-assume-role-policy \
    --role-name $ROLE_NAME \
    --policy-document file:///tmp/trust-policy.json
else
  aws iam create-role \
    --role-name $ROLE_NAME \
    --assume-role-policy-document file:///tmp/trust-policy.json \
    --description "IRSA role for agentgateway to access Amazon Bedrock" \
    --query 'Role.Arn' \
    --output text
fi
```

## Attach the Bedrock Access Policy

```bash
aws iam attach-role-policy \
  --role-name $ROLE_NAME \
  --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess

echo "Attached policies:"
aws iam list-attached-role-policies \
  --role-name $ROLE_NAME \
  --query 'AttachedPolicies[].PolicyName' \
  --output table
```

## Create the Bedrock Backend

Note: there is **no** `policies.auth` section. This signals to agentgateway to use the pod's own AWS credentials (provided via IRSA).

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: bedrock
  namespace: agentgateway-system
spec:
  ai:
    provider:
      bedrock:
        model: us.amazon.nova-micro-v1:0
        region: "$BEDROCK_REGION"
EOF
```

## Annotate the Service Account with the IRSA Role

This patches the `EnterpriseAgentgatewayParameters` to add the `eks.amazonaws.com/role-arn` annotation to the agentgateway data plane service account. EKS will then inject AWS credentials into the pod.

```bash
export ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type merge \
  -p '{
    "spec": {
      "serviceAccount": {
        "metadata": {
          "annotations": {
            "eks.amazonaws.com/role-arn": "'$ROLE_ARN'"
          }
        }
      }
    }
  }'

# Verify annotation on service account
kubectl get sa $SA_NAME -n agentgateway-system \
  -o jsonpath='{.metadata.annotations}' | python3 -m json.tool
```

## Create the HTTP Route

Route traffic through the agentgateway to the Bedrock backend:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bedrock
  namespace: agentgateway-system
  labels:
    example: bedrock-route
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
  - matches:
      - path:
          type: PathPrefix
          value: /bedrock
    backendRefs:
      - name: bedrock
        group: agentgateway.dev
        kind: AgentgatewayBackend
    timeouts:
      request: "120s"
EOF
```

## Restart the Agentgateway Deployment

The pods need to be restarted so the EKS mutating webhook can inject the IRSA projected token and environment variables:

```bash
kubectl rollout restart deployment agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment agentgateway-proxy -n agentgateway-system --timeout=120s
```

## Verify IRSA Injection

Confirm that the EKS webhook injected the correct environment variables and projected token volume into the agentgateway pod:

```bash
POD=$(kubectl get pods -n agentgateway-system \
  -l app.kubernetes.io/name=agentgateway-proxy \
  -o jsonpath='{.items[0].metadata.name}')

echo "Pod: $POD"
echo ""
echo "=== IRSA Environment Variables ==="
kubectl get pod $POD -n agentgateway-system \
  -o jsonpath='{range .spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' \
  | grep -E "AWS_ROLE_ARN|AWS_WEB_IDENTITY_TOKEN_FILE|AWS_REGION"

echo ""
echo "=== Projected Token Volume ==="
kubectl get pod $POD -n agentgateway-system \
  -o jsonpath='{.spec.volumes[?(@.name=="aws-iam-token")]}' | python3 -m json.tool
```

You should see:
- `AWS_ROLE_ARN` set to your IAM role ARN
- `AWS_WEB_IDENTITY_TOKEN_FILE` set to `/var/run/secrets/eks.amazonaws.com/serviceaccount/token`
- A projected volume named `aws-iam-token` with audience `sts.amazonaws.com`

## Test Bedrock Access

### Get the Gateway Address
```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')
```

### curl AWS Bedrock via IRSA
```bash
curl -i "$GATEWAY_IP:8080/bedrock" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {
        "role": "user",
        "content": "Say hello in one sentence."
      }
    ]
  }'
```

### Test with a more complex prompt
```bash
curl -i "$GATEWAY_IP:8080/bedrock" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant. Be concise."
      },
      {
        "role": "user",
        "content": "What are 3 benefits of using IRSA over static AWS credentials?"
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

For metrics, use the AgentGateway Grafana dashboard set up in the [monitoring tools lab](002-set-up-ui-and-monitoring-tools.md). For traces, use the AgentGateway UI.

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

Example output shows comprehensive request details including model information, token usage, and trace IDs for correlation with distributed traces in Grafana.

### (Optional) View Traces in Jaeger

If you installed Jaeger in lab `/install-on-openshift/002-set-up-monitoring-tools-ocp.md` instead of Tempo, you can view traces in the UI:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to see traces with LLM-specific spans including `gen_ai.completion`, `gen_ai.prompt`, `llm.request.model`, `llm.request.tokens`, and more

## Cleanup
```bash
# Remove Kubernetes resources
kubectl delete httproute -n agentgateway-system bedrock
kubectl delete agentgatewaybackend -n agentgateway-system bedrock

# Remove IRSA annotation from EnterpriseAgentgatewayParameters
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type json \
  -p '[{"op": "remove", "path": "/spec/serviceAccount"}]'

# Restart pods to drop IRSA credentials
kubectl rollout restart deployment agentgateway-proxy -n agentgateway-system

# Remove Bedrock policy from the IRSA role (keeps the role for re-use)
aws iam detach-role-policy \
  --role-name $ROLE_NAME \
  --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess

# (Optional) Delete the IAM role entirely if no longer needed
# aws iam delete-role --role-name $ROLE_NAME

echo "Cleanup complete."
```
