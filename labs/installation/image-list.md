# Image list for Enterprise Agentgateway

**v2026.10.1**

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
us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2026.10.1
```

### agentgateway proxy

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/agentgateway-enterprise:2026.10.1
```

### ext-cache (redis)

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/redis:8.6.7-alpine
```

### ext-auth-service

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/ext-auth-service:2026.10.1
```

### rate-limiter

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/rate-limiter:2026.10.1
```

### waf-server

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/waf-server:2026.10.1
```

### Solo UI

The Solo UI ships as the `ui` subchart of the Enterprise Agentgateway Helm chart (`ui.enabled: true`), at the same version.

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/solo-enterprise-ui-backend:2026.10.1
us-docker.pkg.dev/solo-public/solo-enterprise/solo-enterprise-autoauth:v0.2.4
docker.io/otel/opentelemetry-collector-contrib:0.161.0
docker.io/clickhouse/clickhouse-server:26.3.35-alpine
```

Helm values overrides:

```yaml
ui:
  global:
    #--- imagePullSecrets for the UI images ---
    #imagePullSecrets:
    #- name: my-registry-secret
    #--- Registry for every UI image except ClickHouse. Keeps each image's repository path ---
    #imageRegistry: my-registry.example.com
  clickhouse:
    #--- Image override for ClickHouse (embed registry in repository if using private registry) ---
    #image:
    #  repository: clickhouse/clickhouse-server
    #  tag: "26.3.35-alpine"
```

## Air-Gap Mirror Reference (`docker.io/ably7`)

Mirrored copies of every image above, used by the [air-gap install lab](airgap/001-airgap.md). Every image name and tag is unchanged from the list above; only the registry prefix changes to `docker.io/ably7`.

### Enterprise Agentgateway (v2026.10.1)

```
docker.io/ably7/enterprise-agentgateway-controller:2026.10.1
docker.io/ably7/agentgateway-enterprise:2026.10.1
docker.io/ably7/redis:8.6.7-alpine
docker.io/ably7/ext-auth-service:2026.10.1
docker.io/ably7/rate-limiter:2026.10.1
docker.io/ably7/waf-server:2026.10.1
```

### Solo UI (v2026.10.1)

```
docker.io/ably7/solo-enterprise-ui-backend:2026.10.1
docker.io/ably7/solo-enterprise-autoauth:v0.2.4
docker.io/ably7/opentelemetry-collector-contrib:0.161.0
docker.io/ably7/clickhouse-server:26.3.35-alpine
```
