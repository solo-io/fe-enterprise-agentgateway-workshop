# Image list for Enterprise Agentgateway

**2.1.0**

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
us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2.1.0
```

### agentgateway proxy

```bash
ghcr.io/solo-io/agentgateway-enterprise:0.11.1-patch1
```

### ext-cache (redis)

```bash
docker.io/redis:7.2.12-alpine
```

### ext-auth-service

```bash
gcr.io/gloo-mesh/ext-auth-service:0.71.4
```

### rate-limiter

```bash
gcr.io/gloo-mesh/rate-limiter:0.17.2
```
