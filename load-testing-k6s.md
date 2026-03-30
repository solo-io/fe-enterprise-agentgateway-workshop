# Load Testing with k6s

In this lab, you'll learn how to perform load testing on the AgentGateway using k6s, a modern load testing tool. You'll deploy mock OpenAI services, configure routing, and generate sustained load with ramping patterns to validate performance and observe metrics.

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`.

## Lab Objectives
- Deploy mock OpenAI services for load testing
- Configure path-based routing to multiple backends
- Deploy k6s load generator with ramping load patterns
- Monitor load test results using Grafana and Prometheus
- Understand load testing best practices for long-running tests

## Deploy Mock OpenAI Services

Deploy two mock OpenAI services that will serve as our test backends:

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-4o
  template:
    metadata:
      labels:
        app: mock-gpt-4o
    spec:
      containers:
      - args:
        - --model
        - mock-gpt-4o
        - --port
        - "8000"
        - --max-loras
        - "2"
        - --lora-modules
        - '{"name": "food-review-1"}'
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        name: vllm-sim
        ports:
        - containerPort: 8000
          name: http
          protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpt-4o-svc
  namespace: agentgateway-system
spec:
  selector:
    app: mock-gpt-4o
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
      name: http
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-5-2
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-5-2
  template:
    metadata:
      labels:
        app: mock-gpt-5-2
    spec:
      containers:
      - args:
        - --model
        - mock-gpt-5.2
        - --port
        - "8000"
        - --max-loras
        - "2"
        - --lora-modules
        - '{"name": "food-review-1"}'
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        name: vllm-sim
        ports:
        - containerPort: 8000
          name: http
          protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpt-5-2-svc
  namespace: agentgateway-system
spec:
  selector:
    app: mock-gpt-5-2
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
      name: http
  type: ClusterIP
EOF
```

Wait for the deployments to be ready:

```bash
kubectl rollout status deployment/mock-gpt-4o -n agentgateway-system
kubectl rollout status deployment/mock-gpt-5-2 -n agentgateway-system
```

## Configure Routing to Mock Backends

Create AgentgatewayBackend resources for both mock services:

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-4o"
      host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth:
      passthrough: {}
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mock-gpt-5-2
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-5.2"
      host: mock-gpt-5-2-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth:
      passthrough: {}
EOF
```

Create HTTPRoute with path-based routing:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-openai
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-4o
      backendRefs:
        - name: mock-gpt-4o
          group: agentgateway.dev
          kind: AgentgatewayBackend
    - matches:
        - path:
            type: PathPrefix
            value: /openai/gpt-5.2
      backendRefs:
        - name: mock-gpt-5-2
          group: agentgateway.dev
          kind: AgentgatewayBackend
EOF
```

## Test the Routes

Verify both routes are working:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

# Test mock-gpt-4o route
curl -i "$GATEWAY_IP:8080/openai/gpt-4o" \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Say hello"
      }
    ]
  }'

# Test mock-gpt-5.2 route
curl -i "$GATEWAY_IP:8080/openai/gpt-5.2" \
  -H "content-type: application/json" \
  -d '{
    "model": "mock-gpt-5.2",
    "messages": [
      {
        "role": "user",
        "content": "Say hello"
      }
    ]
  }'
```

## Reduce Log Verbosity (Optional)

For load testing, reduce AgentGateway log verbosity to prevent disk pressure:

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type=merge \
  -p '{"spec":{"logging":{"level":"warn"}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system
```

## Deploy k6s Load Generator

Create the loadgenerator namespace:

```bash
kubectl create namespace loadgenerator
```

Create ServiceAccount for k6s:

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: k6s-loadgen
  namespace: loadgenerator
EOF
```

Create k6s test script ConfigMap:

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: k6s-test-script
  namespace: loadgenerator
data:
  http.js: |-
    import http from 'k6/http';
    import { check } from 'k6';
    import { sleep } from 'k6';

    // Core test parameters (configurable via environment variables)
    const model = __ENV.MODEL || "mock-gpt-4o";
    const endpoint = __ENV.ENDPOINT || `https://localhost:8080/openai/${model}`;
    const timeout = parseInt(__ENV.TIMEOUT) || 30000;
    const rps = parseInt(__ENV.RPS) || 50;
    const duration = __ENV.DURATION || "2m";
    const preAllocatedVUs = parseInt(__ENV.PRE_ALLOCATED_VUS) || 25;
    const maxVUs = parseInt(__ENV.MAX_VUS) || 100;
    const jwtToken = __ENV.JWT_TOKEN || "";
    const promptText = __ENV.PROMPT || "Describe an API Gateway in 50 words or less";
    const loadPattern = __ENV.LOAD_PATTERN || "constant";

    // Ramping pattern configuration
    const rampMinMultiplier = parseFloat(__ENV.RAMP_MIN_MULTIPLIER) || 0.5;
    const rampMaxMultiplier = parseFloat(__ENV.RAMP_MAX_MULTIPLIER) || 1.5;
    const rampStageDuration = __ENV.RAMP_STAGE_DURATION || "2m";
    const rampCycles = __ENV.RAMP_CYCLES ? parseInt(__ENV.RAMP_CYCLES) : null;

    // Memory optimization for long-running tests
    const disableTrendStats = __ENV.DISABLE_TREND_STATS === 'true';
    const disableChecks = __ENV.DISABLE_CHECKS === 'true';

    // OpenAI Chat Completion payload
    const payload = JSON.stringify({
      model: model,
      messages: [
        {
          role: "user",
          content: promptText
        }
      ]
    });

    // Build headers
    const headers = {
      'Content-Type': 'application/json'
    };

    if (jwtToken) {
      headers['Authorization'] = `Bearer ${jwtToken}`;
    }

    // Helper to parse duration string to seconds
    function parseDuration(durationStr) {
      const match = durationStr.match(/^(\d+(?:\.\d+)?)(ms|s|m|h)$/);
      if (!match) return 0;
      const value = parseFloat(match[1]);
      const unit = match[2];
      const multipliers = { 'ms': 0.001, 's': 1, 'm': 60, 'h': 3600 };
      return value * multipliers[unit];
    }

    // Build scenario config based on load pattern
    const scenarioConfig = loadPattern === 'ramping' ? (() => {
      const minRate = Math.floor(rps * rampMinMultiplier);
      const maxRate = Math.floor(rps * rampMaxMultiplier);
      const stages = [];

      // Calculate cycles
      let cycles;
      if (rampCycles !== null) {
        cycles = rampCycles;
      } else {
        const totalDurationSec = parseDuration(duration);
        const stageDurationSec = parseDuration(rampStageDuration);
        cycles = Math.max(1, Math.floor((totalDurationSec / stageDurationSec - 1) / 2));
      }

      // Generate ramping stages
      for (let i = 0; i < cycles; i++) {
        stages.push({ duration: rampStageDuration, target: maxRate });
        stages.push({ duration: rampStageDuration, target: minRate });
      }

      stages.push({ duration: rampStageDuration, target: rps });

      return {
        executor: 'ramping-arrival-rate',
        startRate: minRate,
        timeUnit: '1s',
        preAllocatedVUs: preAllocatedVUs,
        maxVUs: maxVUs,
        stages: stages,
      };
    })() : {
      executor: 'constant-arrival-rate',
      rate: rps,
      timeUnit: '1s',
      duration: duration,
      preAllocatedVUs: preAllocatedVUs,
      maxVUs: maxVUs,
    };

    // Global k6 test options
    export const options = {
      insecureSkipTLSVerify: true,
      discardResponseBodies: true,
      scenarios: {
        load_test: scenarioConfig,
      },
      ...(disableTrendStats ? { summaryTrendStats: [] } : {}),
    };

    // Allow startup time before running requests
    sleep(4);

    // Main request logic
    export default function () {
      const res = http.post(endpoint, payload, { headers, timeout });

      if (!disableChecks) {
        check(res, {
          'status is 2xx': (r) => r.status >= 200 && r.status < 300,
        });
      }
    }
EOF
```

Deploy k6s job for mock-gpt-4o:

```bash
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: k6s-mock-gpt-4o
  namespace: loadgenerator
spec:
  completions: 1
  parallelism: 1
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: k6s-loadgen
      containers:
      - name: k6
        image: grafana/k6:0.54.0
        command:
        - "sh"
        - "-c"
        - |
          ARGS="run"
          if [ "\$DISABLE_TREND_STATS" != "true" ]; then
            ARGS="\$ARGS --summary-trend-stats min,avg,med,max,p(95),p(99)"
          fi
          if [ "\$QUIET_MODE" = "true" ]; then
            ARGS="\$ARGS --quiet"
          fi
          ARGS="\$ARGS --discard-response-bodies --tag test_run_id=mock-gpt-4o --tag service=agentgateway-mock-gpt-4o /scripts/http.js"
          k6 \$ARGS
        env:
        - name: ENDPOINT
          value: "http://agentgateway.agentgateway-system.svc.cluster.local:8080/openai/gpt-4o"
        - name: MODEL
          value: "mock-gpt-4o"
        - name: TIMEOUT
          value: "30000"
        - name: DURATION
          value: "5m"
        - name: RPS
          value: "50"
        - name: PRE_ALLOCATED_VUS
          value: "5"
        - name: MAX_VUS
          value: "100"
        - name: PROMPT
          value: "Describe strategies on how enterprises can effectively implement AI technologies."
        - name: DISABLE_TREND_STATS
          value: "false"
        - name: QUIET_MODE
          value: "false"
        - name: LOAD_PATTERN
          value: "ramping"
        - name: RAMP_MIN_MULTIPLIER
          value: "0.8"
        - name: RAMP_MAX_MULTIPLIER
          value: "1.2"
        - name: RAMP_STAGE_DURATION
          value: "1m"
        volumeMounts:
        - name: k6-script
          mountPath: /scripts
          readOnly: true
        securityContext:
          runAsUser: 1000
          runAsGroup: 1000
          runAsNonRoot: true
      volumes:
      - name: k6-script
        configMap:
          name: k6s-test-script
EOF
```

Deploy k6s job for mock-gpt-5.2:

```bash
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: k6s-mock-gpt-5-2
  namespace: loadgenerator
spec:
  completions: 1
  parallelism: 1
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: k6s-loadgen
      containers:
      - name: k6
        image: grafana/k6:0.54.0
        command:
        - "sh"
        - "-c"
        - |
          ARGS="run"
          if [ "\$DISABLE_TREND_STATS" != "true" ]; then
            ARGS="\$ARGS --summary-trend-stats min,avg,med,max,p(95),p(99)"
          fi
          if [ "\$QUIET_MODE" = "true" ]; then
            ARGS="\$ARGS --quiet"
          fi
          ARGS="\$ARGS --discard-response-bodies --tag test_run_id=mock-gpt-5.2 --tag service=agentgateway-mock-gpt-5.2 /scripts/http.js"
          k6 \$ARGS
        env:
        - name: ENDPOINT
          value: "http://agentgateway.agentgateway-system.svc.cluster.local:8080/openai/gpt-5.2"
        - name: MODEL
          value: "mock-gpt-5.2"
        - name: TIMEOUT
          value: "30000"
        - name: DURATION
          value: "5m"
        - name: RPS
          value: "35"
        - name: PRE_ALLOCATED_VUS
          value: "5"
        - name: MAX_VUS
          value: "100"
        - name: PROMPT
          value: "Explain the benefits of using service mesh architecture in cloud-native applications"
        - name: DISABLE_TREND_STATS
          value: "false"
        - name: QUIET_MODE
          value: "false"
        - name: LOAD_PATTERN
          value: "ramping"
        - name: RAMP_MIN_MULTIPLIER
          value: "0.5"
        - name: RAMP_MAX_MULTIPLIER
          value: "1.5"
        - name: RAMP_STAGE_DURATION
          value: "45s"
        volumeMounts:
        - name: k6-script
          mountPath: /scripts
          readOnly: true
        securityContext:
          runAsUser: 1000
          runAsGroup: 1000
          runAsNonRoot: true
      volumes:
      - name: k6-script
        configMap:
          name: k6s-test-script
EOF
```

## Monitor the Load Test

Check the job status:

```bash
kubectl get jobs -n loadgenerator
kubectl get pods -n loadgenerator
```

View k6s logs:

```bash
# View mock-gpt-4o logs
kubectl logs -f job/k6s-mock-gpt-4o -n loadgenerator

# View mock-gpt-5.2 logs
kubectl logs -f job/k6s-mock-gpt-5-2 -n loadgenerator
```

### Access Grafana Dashboard

Port-forward to Grafana:

```bash
kubectl port-forward svc/grafana-prometheus -n monitoring 3000:3000
```

Then access at: http://localhost:3000
- Username: `admin`
- Password: `prom-operator`

Navigate to: **Dashboards > AgentGateway Overview**

### Access Prometheus Metrics

Port-forward to Prometheus:

```bash
kubectl port-forward svc/grafana-prometheus-kube-pr-prometheus -n monitoring 9090:9090
```

Then access at: http://localhost:9090

Useful metrics to query:
- `agentgateway_gen_ai_client_token_usage`
- `agentgateway_gen_ai_server_request_duration`
- `agentgateway_requests_total`
- `agentgateway_request_duration_seconds`

### View AgentGateway Logs

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system -f
```

## Understanding the Load Patterns

This lab deploys two different load generators with distinct patterns so you can observe different behaviors in Grafana:

### mock-gpt-4o Load Pattern
- **Base RPS**: 50 requests per second
- **Min RPS**: 40 RPS (80% of base, configured by `RAMP_MIN_MULTIPLIER: 0.8`)
- **Max RPS**: 60 RPS (120% of base, configured by `RAMP_MAX_MULTIPLIER: 1.2`)
- **Stage Duration**: 1 minute per ramp up/down
- **Pattern**: Slow, gentle oscillations between 40 RPS and 60 RPS every minute

### mock-gpt-5.2 Load Pattern
- **Base RPS**: 35 requests per second
- **Min RPS**: 17.5 RPS (50% of base, configured by `RAMP_MIN_MULTIPLIER: 0.5`)
- **Max RPS**: 52.5 RPS (150% of base, configured by `RAMP_MAX_MULTIPLIER: 1.5`)
- **Stage Duration**: 45 seconds per ramp up/down
- **Pattern**: Faster, more dramatic oscillations between 17.5 RPS and 52.5 RPS every 45 seconds

The different patterns create distinct lines in your Grafana dashboard, making it easy to distinguish between the two backends and observe how the system handles varying load profiles.

## Advanced Configuration

You can customize the k6s load test by modifying these environment variables:

**Load Pattern Options:**
- `LOAD_PATTERN`: "constant" or "ramping"
- `RPS`: Base requests per second
- `DURATION`: Test duration (e.g., "5m", "1h", "12h")

**Ramping Pattern Options:**
- `RAMP_MIN_MULTIPLIER`: Minimum RPS as multiplier of base RPS (default: 0.5)
- `RAMP_MAX_MULTIPLIER`: Maximum RPS as multiplier of base RPS (default: 1.5)
- `RAMP_STAGE_DURATION`: Duration of each ramp stage (e.g., "30s", "1m", "2m")
- `RAMP_CYCLES`: Number of complete up/down cycles (optional)

**Memory Optimization:**
- `DISABLE_TREND_STATS`: Set to "true" for long-running tests
- `QUIET_MODE`: Set to "true" to reduce log output

## Cleanup

Delete the k6s jobs and load generator resources:

```bash
kubectl delete job k6s-mock-gpt-4o -n loadgenerator
kubectl delete job k6s-mock-gpt-5-2 -n loadgenerator
kubectl delete configmap k6s-test-script -n loadgenerator
kubectl delete serviceaccount k6s-loadgen -n loadgenerator
kubectl delete namespace loadgenerator
```

Delete the routing configuration:

```bash
kubectl delete httproute -n agentgateway-system mock-openai
kubectl delete agentgatewaybackend -n agentgateway-system mock-gpt-4o
kubectl delete agentgatewaybackend -n agentgateway-system mock-gpt-5-2
```

Delete the mock services:

```bash
kubectl delete deployment -n agentgateway-system mock-gpt-4o
kubectl delete deployment -n agentgateway-system mock-gpt-5-2
kubectl delete service -n agentgateway-system mock-gpt-4o-svc
kubectl delete service -n agentgateway-system mock-gpt-5-2-svc
```

Restore AgentGateway logging level (if you changed it):

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config -n agentgateway-system \
  --type=merge \
  -p '{"spec":{"logging":{"level":"info"}}}'

kubectl rollout restart deployment/agentgateway-proxy -n agentgateway-system
```
