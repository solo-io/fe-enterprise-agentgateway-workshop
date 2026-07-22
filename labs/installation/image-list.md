# Image list for Enterprise Agentgateway

**v2026.7.0**

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
us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2026.7.0
```

### agentgateway proxy

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/agentgateway-enterprise:2026.7.0
```

### ext-cache (redis)

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/redis:8.6.4-alpine
```

### ext-auth-service

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/ext-auth-service:2026.7.0
```

### rate-limiter

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/rate-limiter:2026.7.0
```

### waf-server

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/waf-server:2026.7.0
```

### Image list for Solo UI

**0.5.0**

Helm chart:

```bash
oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management
```

Images:

```bash
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-ui-frontend:0.5.0
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-ui-backend:0.5.0
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
  #  tag: "0.5.0"
clickhouse:
  #--- Image override for ClickHouse (embed registry in repository if using private registry) ---
  #image:
  #  repository: clickhouse/clickhouse-server
  #  tag: "26.1.11.9-alpine"
```

## Air-Gap Mirror Reference (`docker.io/ably7`)

Mirrored copies of every image above, used by the [air-gap install lab](airgap/001-airgap.md). Every image name and tag is unchanged from the list above — only the registry prefix changes to `docker.io/ably7`.

### Enterprise Agentgateway (v2026.7.0)

```
docker.io/ably7/enterprise-agentgateway-controller:2026.7.0
docker.io/ably7/agentgateway-enterprise:2026.7.0
docker.io/ably7/redis:8.6.4-alpine
docker.io/ably7/ext-auth-service:2026.7.0
docker.io/ably7/rate-limiter:2026.7.0
docker.io/ably7/waf-server:2026.7.0
```

### Solo UI (0.5.0)

```
docker.io/ably7/solo-enterprise-ui-frontend:0.5.0
docker.io/ably7/solo-enterprise-ui-backend:0.5.0
docker.io/ably7/solo-enterprise-autoauth:v0.2.2
docker.io/ably7/opentelemetry-collector-contrib:0.153.0
docker.io/ably7/clickhouse-server:26.1.11.9-alpine
```
