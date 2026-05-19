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
