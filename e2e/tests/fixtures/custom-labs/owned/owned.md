# Owned Objects Custom Lab

## Pre-requisites
This lab assumes that you have completed the setup in `001`.

## Leave only owned and controller objects
```bash
printf '%s\n' 'Deployment/agentgateway-proxy-extra Gateway' 'Secret/sh.helm.release.v1.enterprise-agentgateway.v9' 'ConfigMap/enterprise-jwks-store-1a2b' >> "$E2E_TEST_SNAP"
echo "left owned objects"
```

## Cleanup
```bash
echo "cleanup ran"
```
