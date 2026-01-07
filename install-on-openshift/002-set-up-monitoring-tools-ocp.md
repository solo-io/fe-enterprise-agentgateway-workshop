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

## Access Jaeger UI

To view traces throughout the workshop:

```bash
kubectl port-forward svc/jaeger -n observability 16686:16686
```

Navigate to http://localhost:16686 in your browser to view traces with LLM-specific spans including:
- `gen_ai.completion` - The completion response from the LLM
- `gen_ai.prompt` - The prompt sent to the LLM
- `gen_ai.request.model` - The requested model
- `gen_ai.response.model` - The actual model that responded
- `gen_ai.usage.prompt_tokens` - Input token count
- `gen_ai.usage.completion_tokens` - Output token count
- `llm.provider` - The LLM provider (OpenAI, Bedrock, etc.)

You can filter traces by service name, operation, and tags to find specific requests.

## Next Steps
You should now be able to go back to the root directory and continue with `003`
