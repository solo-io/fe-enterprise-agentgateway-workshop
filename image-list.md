# Image list for Enterprise Agentgateway

**v2026.6.0**

## Helm Charts

### Enterprise Agentgateway CRD Helm chart

```bash
helm pull oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds --version $ENTERPRISE_AGW_VERSION
```

### Enterprise Agentgateway Helm Chart

```bash
helm pull oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway --version $ENTERPRISE_AGW_VERSION
```

## Images

### controller

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2026.6.0
```

### agentgateway proxy

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/agentgateway-enterprise:2026.6.0
```

### ext-cache (redis)

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/redis:2026.6.0
```

### ext-auth-service

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/ext-auth-service:2026.6.0
```

### rate-limiter

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/rate-limiter:2026.6.0
```

### Image list for Solo UI

**0.4.5**

> The Solo UI (`management` chart) versions independently of Enterprise
> Agentgateway and is not installed in this cluster. Versions below are the
> v2026.6.0 companions from the air-gapped install guide, not a live read.

Helm chart:

```bash
oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management
```

Images:

```bash
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-ui-frontend:0.4.5
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-ui-backend:0.4.5
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-autoauth:v0.2.2
docker.io/otel/opentelemetry-collector-contrib:0.153.0
docker.io/clickhouse/clickhouse-server:26.1.11.9-alpine
```

Helm values overrides:

```yaml
global:
  #--- imagePullSecrets for private registry (propagated to all subcharts) ---
  #imagePullSecrets:
  #- name: my-registry-secret
  #--- Image overrides for all Solo-owned images (UI frontend, backend, IDP/autoauth) ---
  #image:
  #  registry: my-registry.example.com
  #  repository: solo-enterprise
  #  tag: "0.4.5"
clickhouse:
  #--- Image override for ClickHouse (embed registry in repository if using private registry) ---
  #image:
  #  repository: clickhouse/clickhouse-server
  #  tag: "26.1.11.9-alpine"
```
