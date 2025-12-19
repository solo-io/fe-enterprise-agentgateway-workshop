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

## Deploy metrics + logs

Install Grafana Prometheus and add Jaeger as a data source
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update prometheus-community
helm upgrade --install grafana-prometheus \
  prometheus-community/kube-prometheus-stack \
  --version 76.4.1 \
  --namespace monitoring \
  --create-namespace \
  --values - <<EOF
alertmanager:
  enabled: false
grafana:
  service:
    type: ClusterIP
    port: 3000
  additionalDataSources:
    - name: Jaeger
      type: jaeger
      access: proxy
      url: "http://jaeger-query.observability.svc.cluster.local:16686"
      uid: 'local-jaeger-uid'
nodeExporter:
  enabled: false
prometheus:
  service:
    type: ClusterIP
  prometheusSpec:
    ruleSelectorNilUsesHelmValues: false
    serviceMonitorSelectorNilUsesHelmValues: false
    podMonitorSelectorNilUsesHelmValues: false
EOF
```

Add PodMonitor for scraping metrics from the agentgateway
```bash
kubectl apply -f- <<EOF
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: data-plane-monitoring-gloo-ai-metrics
  namespace: gloo-system
spec:
  namespaceSelector:
    matchNames:
      - gloo-system
  podMetricsEndpoints:
    - port: metrics
  selector:
    matchLabels:
      app.kubernetes.io/name: agentgateway
EOF
```

Check that our observability tools are running:

```bash
kubectl get pods -n monitoring
```

Expected Output:

```bash
NAME                                                     READY   STATUS    RESTARTS   AGE
grafana-prometheus-fbdf9c69f-p9qq5                       3/3     Running   0          2m54s
grafana-prometheus-kube-pr-operator-857d774dbf-djxch     1/1     Running   0          2m54s
grafana-prometheus-kube-state-metrics-7c6d5ff8f6-77hkl   1/1     Running   0          2m54s
prometheus-grafana-prometheus-kube-pr-prometheus-0       2/2     Running   0          2m50s
```

## Next Steps
You should now be able to go back to the root directory and continue with `003`
