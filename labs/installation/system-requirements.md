# AgentGateway Enterprise — System Requirements

## Kubernetes & Tooling Versions (v2026.7.0)

| Component | Requirement |
|---|---|
| Kubernetes | 1.32 – 1.36 |
| Helm | ≥ 3.12 |
| Gateway API CRDs | 1.3 – 1.5 |
| Istio (if using waypoint/ambient features) | 1.26 – 1.29 |
| Solo UI | 0.5.0 |

Source: Version Support Matrix

## Cluster Sizing (POC)

| Resource | Recommendation |
|---|---|
| Node count | 2–3 nodes (for resilience) |
| Node size | 2–4 vCPU, 8–16 GiB RAM per node |
| Control plane pods | 1 replica (default) |
| Control plane pod resources | CPU Request: 500m<br>CPU Limit: 1<br>MEM Request: 512Mi<br>MEM Limit: 1Gi |
| Proxy pods | 2 replicas minimum (for availability) |
| Proxy pod resources | CPU Request: 100m<br>CPU Limit: 500m<br>MEM Request: 128Mi<br>MEM Limit: 512Mi |

## Cluster Sizing (Prod)

| Resource | Recommendation |
|---|---|
| Node count | 2–3 nodes (for resilience) |
| Node size | 2–4 vCPU, 8–16 GiB RAM per node |
| Control plane pods | 1+ (with PDB) |
| Control plane pod resources | CPU Request: 1<br>CPU Limit: 2<br>MEM Request: 1Gi<br>MEM Limit: 2Gi |
| Proxy pods | 2-3+ (with HPA) |
| Proxy pod resources | CPU Request: 200m - 500m<br>CPU Limit: 1-2<br>MEM Request: 512Mi - 1Gi<br>MEM Limit: 1Gi - 2Gi |

## Example of Setting Container Resource Requests/Limits (Data Plane)

Configure via `EnterpriseAgentgatewayParameters`:

```yaml
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayParameters
metadata:
  name: agentgateway-config
  namespace: agentgateway-system
spec:
  replicas: 2
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

## Example of Setting Gateway with 2 Proxy Replicas

Reference the `EnterpriseAgentgatewayParameters` from your Gateway:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: agentgateway-system
spec:
  gatewayClassName: enterprise-agentgateway
  infrastructure:
    parametersRef:
      name: agentgateway-config
      group: enterpriseagentgateway.solo.io
      kind: EnterpriseAgentgatewayParameters
  listeners:
    - protocol: HTTP
      port: 80
      name: http
      allowedRoutes:
        namespaces:
          from: All
```

## Shared Extension Servers

Enterprise AgentGateway automatically deploys shared extension servers for ext-auth, rate limiting, and caching. For a POC, these add the following pods to your cluster:

- **ext-auth-service-enterprise-agentgateway** — 1 replica (typical: 100m–500m CPU, 128Mi–512Mi memory)
- **rate-limiter-enterprise-agentgateway** — 1 replica (typical: 100m–500m CPU, 128Mi–512Mi memory)
- **ext-cache-enterprise-agentgateway** (Redis) — 1 replica (typical: 100m CPU, 128Mi–256Mi memory)

## Other Prerequisites

- **Solo Enterprise license key** — contact Sales if needed
- **kubectl** configured for your Kubernetes cluster
- **LoadBalancer support** — required for exposing the gateway (provided natively by most managed Kubernetes services; on-prem clusters may need MetalLB or similar)

## Reference Links

- Version Support Matrix (v2026.7.0)
- Installation Guide
- Changelog
- API Reference
