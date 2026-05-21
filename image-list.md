# Image list for Enterprise Agentgateway

**v2026.5.0**

## Helm Charts

### Enterprise Agentgateway CRD Helm chart

```bash
oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds
```

### Enterprise Agentgateway Helm Chart

```bash
oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway
```

## Images

### controller

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2026.5.0
```

### agentgateway proxy

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/agentgateway-enterprise:2026.5.0
```

### ext-cache (redis)

```bash
docker.io/redis:8.6.2-alpine
```

### ext-auth-service

```bash
gcr.io/gloo-mesh/ext-auth-service:0.81.1
```

### rate-limiter

```bash
gcr.io/gloo-mesh/rate-limiter:0.18.6
```

### Image list for Solo UI

**0.4.2**

Helm chart:

```bash
oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management
```

Images:

```bash
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-ui-frontend:0.4.2
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-ui-backend:0.4.2
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-autoauth:v0.2.1
docker.io/otel/opentelemetry-collector-contrib:0.150.1
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
  #  tag: "0.4.2"
clickhouse:
  #--- Image override for ClickHouse (embed registry in repository if using private registry) ---
  #image:
  #  repository: clickhouse/clickhouse-server
  #  tag: "26.1.11.9-alpine"
```
