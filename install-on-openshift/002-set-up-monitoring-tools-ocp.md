# Set up monitoring tools on OpenShift
Agentgateway emits OpenTelemetry-compatible metrics, logs, and traces out of the box. In this lab, we'll deploy Jaeger, Prometheus, and Grafana to collect, store, and visualize this observability data in later labs

## Pre-requisites
This lab assumes that you have completed the setup in `001`

## Lab Objectives
- Deploy tracing (Jaeger)
- Deploy metrics (Prometheus, Grafana)
- Configure Prometheus to scrape Agentgateway

## Deploy tracing

OpenShift SCC for Jaeger:
```bash
oc adm policy add-scc-to-group anyuid system:serviceaccounts:observability
```

Install Jaeger on the cluster:
```bash
helm repo add jaegertracing https://jaegertracing.github.io/helm-charts
helm repo update jaegertracing
helm upgrade -i jaeger jaegertracing/jaeger \
    -n observability \
    --create-namespace \
    -f - <<EOF
provisionDataStore:
  cassandra: false
allInOne:
  enabled: true
storage:
  type: memory
agent:
  enabled: false
collector:
  enabled: false
query:
  enabled: false
EOF
```

Check that Jaeger is running:

```bash
kubectl get pods -n observability
```

Expected Output:

```bash
NAME                      READY   STATUS    RESTARTS   AGE
jaeger-54b6c8b5d5-8s74n   1/1     Running   0          18m
```

## Next Steps
You should now be able to go back to the root directory and continue with `003`
