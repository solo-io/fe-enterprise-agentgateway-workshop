# Image list for Enterprise Agentgateway

**2.3.2**

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
us-docker.pkg.dev/solo-public/enterprise-agentgateway/enterprise-agentgateway-controller:2.3.2
```

### agentgateway proxy

```bash
us-docker.pkg.dev/solo-public/enterprise-agentgateway/agentgateway-enterprise:2.3.2
```

### ext-cache (redis)

```bash
docker.io/redis:7.2.13-alpine
```

### ext-auth-service

```bash
gcr.io/gloo-mesh/ext-auth-service:0.79.1
```

### rate-limiter

```bash
gcr.io/gloo-mesh/rate-limiter:0.18.2
```
