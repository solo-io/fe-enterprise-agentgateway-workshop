# Production Observability, Alerting, and Scaling

This guide covers production-readiness for Enterprise AgentGateway: a complete metrics reference, recommended Prometheus alerting rules, horizontal pod autoscaling, graceful shutdown for long-lived AI connections, pod spreading, disruption budgets, and zero-downtime upgrades.

---

## Metrics Reference

Enterprise AgentGateway exposes metrics from three components.

### Data Plane — `agentgateway-proxy` (port 15020)

The data plane is a Rust-based proxy built on Tokio. It handles all request routing, LLM traffic, MCP calls, and guardrail enforcement.

#### Build Info

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agentgateway_build_info` | Info | `tag` | AgentGateway version (e.g. `v2.3.2`). Use to confirm all pods are on the same version after an upgrade. |

#### HTTP Request Metrics

These are the primary metrics for monitoring gateway health and performance. Every request flowing through the gateway is counted and timed here.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agentgateway_requests_total` | Counter | `backend`, `protocol`, `method`, `status`, `reason`, `bind`, `gateway`, `listener`, `route`, `route_rule` | Total HTTP requests processed. The `status` label is the HTTP status code (200, 429, 500, etc.). The `reason` label classifies why the response was generated (see reason table below). |
| `agentgateway_request_duration_seconds` | Histogram | _(same)_ | End-to-end request latency from the proxy's perspective, including upstream LLM processing time. Buckets: 1ms to 80s. For LLM traffic, p99 will be dominated by model inference time. |
| `agentgateway_response_bytes_total` | Counter | _(same)_ | Total response bytes received from upstream backends. Useful for tracking bandwidth and detecting unusually large responses. |

**HTTP Label Reference:**

| Label | Values | Description |
|---|---|---|
| `backend` | Backend name or `"unknown"` | The upstream AgentgatewayBackend that handled the request. `"unknown"` means no route matched. |
| `protocol` | `http`, `https`, `tls`, `tcp`, `hbone` | Transport protocol to the upstream. |
| `method` | `GET`, `POST`, `CONNECT`, etc. | HTTP method. LLM chat completions are always `POST`. |
| `status` | HTTP status code (200, 404, 429, 500, etc.) | Response status code. |
| `reason` | See table below | Why the proxy generated or forwarded this response. |
| `bind` | e.g. `8080/agentgateway-system/agentgateway-proxy` | The listener bind address. |
| `gateway` | e.g. `agentgateway-system/agentgateway-proxy` | The Gateway resource name. |
| `listener` | e.g. `http` | Listener name within the Gateway. |
| `route` | HTTPRoute name or `"unknown"` | Which HTTPRoute matched. |
| `route_rule` | Rule index or `"unknown"` | Which rule within the HTTPRoute matched. |

**Response `reason` Values:**

The `reason` label tells you _why_ a response was generated — critical for distinguishing between "upstream returned an error" vs. "the gateway itself rejected the request":

| Reason | Meaning | Typical Status Codes |
|---|---|---|
| `Upstream` | Response came from the upstream LLM/MCP backend | Any (200, 429, 500, etc.) |
| `DirectResponse` | Proxy generated the response directly (no upstream call) | Varies |
| `NotFound` | No matching listener, route, or backend found | 404 |
| `NoHealthyBackend` | All providers in the backend are unhealthy, DNS failed, or backend doesn't exist | 503 |
| `RateLimit` | Request rejected by local or global rate limiter | 429 |
| `Timeout` | Request or upstream call timed out | 504 |
| `JwtAuth` | JWT authentication failed | 401 |
| `BasicAuth` | Basic authentication failed | 401 |
| `APIKeyAuth` | API key authentication failed | 401 |
| `ExtAuth` | External authorization service rejected the request | 403 |
| `Authorization` | Authorization or CSRF validation failed | 403 |
| `UpstreamFailure` | Upstream connection failed, TCP proxy error, or backend auth error | 502, 503 |
| `Internal` | Internal proxy error (invalid request, filter error, processing error) | 500 |
| `MCP` | MCP protocol-level error | Varies |
| `ExtProc` | External processing failure | 500 |

#### GenAI (LLM) Metrics

These follow the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/). They are only populated for requests routed to LLM backends (AgentgatewayBackend with `ai` spec).

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agentgateway_gen_ai_client_token_usage` | Histogram | `gen_ai_token_type`, `gen_ai_operation_name`, `gen_ai_system`, `gen_ai_request_model`, `gen_ai_response_model`, `route` | Tokens consumed per request. Two observations per request: one with `gen_ai_token_type="input"` (prompt tokens) and one with `gen_ai_token_type="output"` (completion tokens). Buckets are exponential: 1, 4, 16, 64, 256, 1024 ... up to 67M. |
| `agentgateway_gen_ai_server_request_duration` | Histogram | `gen_ai_operation_name`, `gen_ai_system`, `gen_ai_request_model`, `gen_ai_response_model`, `route` | Total time the upstream LLM took to process the request (seconds). For streaming, this is time from first byte sent to last byte received. Buckets: 10ms to 82s. |
| `agentgateway_gen_ai_server_time_to_first_token` | Histogram | _(same)_ | Time from request start to the first token generated (TTFT). Critical SLI for streaming user experience. Buckets: 1ms to 10s. |
| `agentgateway_gen_ai_server_time_per_output_token` | Histogram | _(same)_ | Average inter-token latency (TPOT). Measures generation throughput. Buckets: 1ms to 2.5s. |

**GenAI Label Reference:**

| Label | Values | Description |
|---|---|---|
| `gen_ai_token_type` | `input`, `output` | Whether this observation counts prompt tokens or completion tokens. Only on `token_usage`. |
| `gen_ai_operation_name` | `chat`, `embeddings` | The type of LLM operation. |
| `gen_ai_system` | `openai`, `anthropic`, `bedrock`, `vertexai`, `azureopenai`, etc. | The LLM provider type configured in the backend. |
| `gen_ai_request_model` | e.g. `gpt-4o`, `claude-sonnet-4-20250514` | The model name sent in the request. |
| `gen_ai_response_model` | e.g. `gpt-4o-2024-08-06` | The model name returned in the response (may differ from request). |

#### MCP (Model Context Protocol) Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agentgateway_mcp_requests` | Counter | `method`, `resource_type`, `server`, `resource`, `route` | Total MCP tool/resource/prompt calls. Not incremented for raw HTTP transport requests (only JSON-RPC method calls). |

**MCP Label Reference:**

| Label | Values | Description |
|---|---|---|
| `method` | `tools/call`, `tools/list`, `prompts/get`, `resources/read`, etc. | The MCP JSON-RPC method name. |
| `resource_type` | `Tool`, `Prompt`, `Resource`, `ResourceTemplates` | Category of MCP operation. |
| `server` | Target MCP server name | Which MCP server was called. |
| `resource` | Tool/resource name | The specific tool or resource accessed. |

MCP requests also flow through the general `agentgateway_request_duration_seconds` histogram for latency tracking.

**Example — MCP tool call rate per route and server:**
```promql
sum(rate(agentgateway_mcp_requests{method="tools/call"}[5m])) by (route, server, resource)
```

#### Guardrail Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agentgateway_guardrail_checks` | Counter | `phase`, `action` | Total guardrail evaluations across all guardrail types (regex, webhook, OpenAI Moderation, Bedrock Guardrails, Google Model Armor). |

| Label | Values | Description |
|---|---|---|
| `phase` | `Request`, `Response` | Whether the guardrail fired on the inbound request or the outbound LLM response. |
| `action` | `Allow`, `Mask`, `Reject` | The outcome. `Reject` = request/response blocked, `Mask` = content redacted, `Allow` = passed. |

#### Connection & Transport Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agentgateway_downstream_connections_total` | Counter | `bind`, `gateway`, `listener`, `protocol` | Total client-to-proxy connections established. Includes short-lived and long-lived (streaming, MCP/SSE) connections. |
| `agentgateway_downstream_received_bytes_total` | Counter | _(same)_ | Total bytes received from clients. |
| `agentgateway_downstream_sent_bytes_total` | Counter | _(same)_ | Total bytes sent to clients. |
| `agentgateway_upstream_connect_duration_seconds` | Histogram | `transport` | Time to establish upstream connections. `transport` is `plaintext` or `tls`. High values indicate network issues or DNS problems to LLM providers. Buckets: 0.5ms to 8s. |
| `agentgateway_tls_handshake_duration_seconds` | Histogram | `bind`, `gateway`, `listener`, `protocol` | Inbound TLS handshake duration. Only populated if TLS termination is configured on the gateway. Buckets: 0.5ms to 8s. |

#### xDS (Control Plane Communication) Metrics

These track the connection between the data plane proxy and the control plane. Problems here mean the proxy isn't receiving configuration updates.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agentgateway_xds_connection_terminations` | Counter | `reason` | xDS stream disconnections. `reason` is `ConnectionError` (network failure), `Error` (gRPC error), `Reconnect` (planned), or `Complete` (clean close). Frequent `ConnectionError` or `Error` values indicate control plane instability. |
| `agentgateway_xds_message_total` | Counter | `url` | Number of xDS config messages received. The `url` label is the resource type URL (e.g. `type.googleapis.com/agentgateway.dev.resource.Resource`). A sudden stop means the proxy is no longer receiving config updates. |
| `agentgateway_xds_message_bytes_total` | Counter | `url` | Bytes received from xDS. Large spikes may indicate excessive configuration churn. |

#### Tokio Runtime Metrics

The proxy runs on a Tokio async runtime. These metrics indicate proxy-level health independently of request metrics.

| Metric | Type | Description |
|---|---|---|
| `agentgateway_tokio_num_workers` | Gauge | Number of Tokio worker threads. Defaults to the number of CPU cores (or the value of `CPU_LIMIT`). Should be stable. |
| `agentgateway_tokio_num_alive_tasks` | Gauge | Number of currently active async tasks. Each in-flight request and connection is a task. A sustained upward trend may indicate task leaks or connection backlog. |
| `agentgateway_tokio_global_queue_depth` | Gauge | Tasks waiting to be picked up by a worker thread. Sustained values > 0 mean worker threads are saturated — a strong scale-up signal. |

---

### Control Plane — `enterprise-agentgateway` (port 9092)

The control plane is a Go-based Kubernetes controller. It watches Gateway API and AgentGateway CRDs and pushes configuration to data plane proxies via xDS.

**Note:** The `kgateway_` metric prefix may be renamed to `agentgateway_` in a future release. Check your cluster's actual metric names with `curl localhost:9092/metrics`.

#### Controller Reconciliation Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `kgateway_controller_reconciliations_total` | Counter | `controller`, `name`, `namespace`, `result` | Total reconciliation loops. The `controller` label identifies which controller ran (e.g. `gateway`, `gatewayclass`). The `result` label is `success` or `error`. A rising `error` count means CRD changes are not being applied. |
| `kgateway_controller_reconciliations_running` | Gauge | `controller`, `name`, `namespace` | Currently in-flight reconciliations. Sustained high values indicate controller backlog. |
| `kgateway_controller_reconcile_duration_seconds` | Histogram | `controller`, `name`, `namespace` | Time per reconciliation loop. Increasing durations may indicate growing cluster complexity or API server slowness. |
| `enterprise_kgateway_controller_reconciliations_total` | Counter | `controller`, `name`, `namespace`, `result` | Same as above but for enterprise-specific controllers: `agw-ext-auth`, `agw-ext-cache`, `agw-rate-limiter`. |
| `enterprise_kgateway_controller_reconciliations_running` | Gauge | _(same)_ | In-flight enterprise reconciliations. |
| `enterprise_kgateway_controller_reconcile_duration_seconds` | Histogram | _(same)_ | Enterprise reconciliation duration. |

#### xDS Authentication

| Metric | Type | Description |
|---|---|---|
| `kgateway_xds_auth_rq_total` | Counter | Total xDS authentication requests from data plane proxies. Each proxy connection must authenticate. |
| `kgateway_xds_auth_rq_success_total` | Counter | Successful xDS auth requests. If `total - success > 0`, proxy pods are failing to authenticate with the control plane. |

#### Go Runtime & Process Metrics

| Metric | Type | Description |
|---|---|---|
| `go_goroutines` | Gauge | Number of active goroutines. Sustained growth indicates leaks. Baseline is ~900 for a healthy controller. |
| `go_memstats_alloc_bytes` | Gauge | Current heap allocation. Monitor for memory leaks. |
| `process_resident_memory_bytes` | Gauge | RSS of the control plane process. Use for capacity planning. |
| `process_cpu_seconds_total` | Counter | CPU time consumed. Use `rate()` for CPU utilization. |
| `process_open_fds` | Gauge | Open file descriptors. Approaching `process_max_fds` causes failures. |

---

### Rate Limiter — `rate-limiter-enterprise-agentgateway` (port 9091)

| Metric | Type | Labels | Description |
|---|---|---|---|
| `ratelimit_solo_io_total_hits` | Counter | `descriptor` | Total rate limit evaluation requests. The `descriptor` label encodes the rate limit policy (e.g. `solo.io\|generic_key^namespace.policyname`). |
| `ratelimit_solo_io_over_limit` | Counter | `descriptor` | Requests that exceeded the configured limit and were rejected (429). |
| `ratelimit_solo_io_near_limit` | Counter | `descriptor` | Requests that were within 80% of the limit — an early warning signal. |

---

## Scraping Metrics with Prometheus

The data plane proxy exposes metrics on a named port `metrics` (15020). If you're using the Prometheus Operator, create PodMonitors to scrape each component:

**Data plane:**
```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: data-plane-monitoring-agentgateway-metrics
  namespace: agentgateway-system
spec:
  namespaceSelector:
    matchNames:
      - agentgateway-system
  podMetricsEndpoints:
    - port: metrics
  selector:
    matchLabels:
      app.kubernetes.io/name: agentgateway-proxy
```

**Control plane:**
```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: control-plane-monitoring-agentgateway-metrics
  namespace: agentgateway-system
spec:
  namespaceSelector:
    matchNames:
      - agentgateway-system
  podMetricsEndpoints:
    - port: metrics
  selector:
    matchLabels:
      app.kubernetes.io/name: enterprise-agentgateway
```

**Rate limiter** (uses a ServiceMonitor since the pod doesn't expose a named port):
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: rate-limiter-monitoring-agentgateway-metrics
  namespace: agentgateway-system
spec:
  namespaceSelector:
    matchNames:
      - agentgateway-system
  selector:
    matchLabels:
      app: rate-limiter
  endpoints:
    - port: debug
      interval: 15s
```

---

## Recommended Prometheus Alerting Rules

The following `PrometheusRule` provides a starting set of alerts across all three components. Adjust thresholds to match your SLOs.

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: agentgateway-alerts
  namespace: monitoring
  labels:
    release: prometheus
spec:
  groups:
    # ──────────────────────────────────────────────
    # Data Plane Alerts
    # ──────────────────────────────────────────────
    - name: agentgateway-dataplane
      rules:

        # High error rate: >5% of requests returning 5xx
        - alert: AgentGatewayHighErrorRate
          expr: |
            (
              sum(rate(agentgateway_requests_total{status=~"5.."}[5m])) by (gateway)
              /
              sum(rate(agentgateway_requests_total[5m])) by (gateway)
            ) > 0.05
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "AgentGateway {{ $labels.gateway }} has >5% error rate"
            description: "{{ $value | humanizePercentage }} of requests are returning 5xx errors."

        # High rate limit rejection rate
        - alert: AgentGatewayHighRateLimitRate
          expr: |
            (
              sum(rate(agentgateway_requests_total{reason="RateLimit"}[5m])) by (gateway)
              /
              sum(rate(agentgateway_requests_total[5m])) by (gateway)
            ) > 0.10
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "AgentGateway {{ $labels.gateway }} is rate-limiting >10% of requests"
            description: "Consider increasing rate limits or scaling the gateway."

        # No healthy backends available
        - alert: AgentGatewayNoHealthyBackends
          expr: |
            sum(rate(agentgateway_requests_total{reason="NoHealthyBackend"}[5m])) by (gateway, route) > 0
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: "Route {{ $labels.route }} on {{ $labels.gateway }} has no healthy backends"
            description: "All LLM providers in this route's backend are unhealthy. Requests are failing with 503."

        # Slow LLM responses: p99 > 30s
        - alert: AgentGatewaySlowLLMResponses
          expr: |
            histogram_quantile(0.99,
              sum(rate(agentgateway_gen_ai_server_request_duration_bucket[5m])) by (le, gen_ai_system, gen_ai_request_model)
            ) > 30
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "LLM p99 latency >30s for {{ $labels.gen_ai_system }}/{{ $labels.gen_ai_request_model }}"
            description: "p99 LLM response time is {{ $value | humanizeDuration }}. Check provider health."

        # High TTFT (time to first token) - streaming UX degradation
        # Note: Reasoning models (o1, o3, Claude with extended thinking) can have
        # TTFT of 30s+ by design. Adjust threshold or exclude models as needed.
        - alert: AgentGatewayHighTTFT
          expr: |
            histogram_quantile(0.95,
              sum(rate(agentgateway_gen_ai_server_time_to_first_token_bucket[5m])) by (le, gen_ai_request_model)
            ) > 30
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "TTFT p95 >30s for model {{ $labels.gen_ai_request_model }}"
            description: "Users are waiting {{ $value | humanizeDuration }} for the first token. If this is a reasoning model, this may be expected."

        # Upstream connection failures
        - alert: AgentGatewayUpstreamConnectFailures
          expr: |
            sum(rate(agentgateway_requests_total{reason="UpstreamFailure"}[5m])) by (gateway, backend) > 0.5
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "Upstream connection failures to {{ $labels.backend }}"
            description: "The proxy cannot connect to the upstream backend. Check DNS, network policies, and provider status."

        # Guardrail rejection spike
        - alert: AgentGatewayGuardrailRejectionSpike
          expr: |
            sum(rate(agentgateway_guardrail_checks{action="Reject"}[5m])) by (phase)
            /
            sum(rate(agentgateway_guardrail_checks[5m])) by (phase) > 0.20
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "Guardrail rejection rate >20% on {{ $labels.phase }} phase"
            description: "{{ $value | humanizePercentage }} of {{ $labels.phase | toLower }} guardrail checks are being rejected."

        # Tokio runtime saturation — tasks queuing up
        - alert: AgentGatewayRuntimeSaturation
          expr: |
            agentgateway_tokio_global_queue_depth > 10
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "AgentGateway proxy runtime is saturated (queue depth {{ $value }})"
            description: "Tokio worker threads cannot keep up. This is a strong signal to scale up the proxy."

        # Task accumulation — potential leak or connection backlog
        - alert: AgentGatewayTaskAccumulation
          expr: |
            agentgateway_tokio_num_alive_tasks > 10000
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "AgentGateway has {{ $value }} active tasks"
            description: "Sustained high task count may indicate connection leaks or backlog. Investigate long-lived connections."

        # xDS disconnection from control plane
        - alert: AgentGatewayXDSDisconnected
          expr: |
            sum(rate(agentgateway_xds_connection_terminations{reason=~"ConnectionError|Error"}[5m])) by (pod) > 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "Proxy pod is disconnecting from the control plane"
            description: "xDS connection errors detected. The proxy may not be receiving configuration updates."

        # Version mismatch after upgrade
        - alert: AgentGatewayVersionMismatch
          expr: |
            count(count by (tag) (agentgateway_build_info)) > 1
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "Multiple AgentGateway versions running simultaneously"
            description: "Not all proxy pods are on the same version. This may indicate a stalled rollout."

    # ──────────────────────────────────────────────
    # Control Plane Alerts
    # ──────────────────────────────────────────────
    - name: agentgateway-controlplane
      rules:

        # Reconciliation errors
        - alert: AgentGatewayReconcileErrors
          expr: |
            sum(rate(kgateway_controller_reconciliations_total{result="error"}[5m])) by (controller) > 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "Controller {{ $labels.controller }} has reconciliation errors"
            description: "CRD changes may not be applied to the data plane. Check controller logs."

        # Slow reconciliation
        - alert: AgentGatewaySlowReconcile
          expr: |
            histogram_quantile(0.99,
              sum(rate(kgateway_controller_reconcile_duration_seconds_bucket[5m])) by (le, controller)
            ) > 5
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "Controller {{ $labels.controller }} p99 reconcile time >5s"
            description: "Reconciliation is slow ({{ $value | humanizeDuration }}). Check API server performance and cluster size."

        # xDS auth failures — proxies can't connect to control plane
        - alert: AgentGatewayXDSAuthFailures
          expr: |
            (
              rate(kgateway_xds_auth_rq_total[5m]) - rate(kgateway_xds_auth_rq_success_total[5m])
            ) > 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "Proxy pods are failing xDS authentication"
            description: "Data plane proxies cannot authenticate with the control plane. New config will not be pushed."

        # Control plane memory growth
        - alert: AgentGatewayControlPlaneMemory
          expr: |
            process_resident_memory_bytes{job=~".*enterprise-agentgateway.*"} > 512 * 1024 * 1024
          for: 15m
          labels:
            severity: warning
          annotations:
            summary: "Control plane using >512MB memory"
            description: "Current RSS: {{ $value | humanize1024 }}B. Investigate for memory leaks."

        # Goroutine leak
        - alert: AgentGatewayGoroutineLeak
          expr: |
            go_goroutines{job=~".*enterprise-agentgateway.*"} > 5000
          for: 15m
          labels:
            severity: warning
          annotations:
            summary: "Control plane has {{ $value }} goroutines"
            description: "Sustained goroutine growth may indicate a leak. Baseline is ~900."

    # ──────────────────────────────────────────────
    # Rate Limiter Alerts
    # ──────────────────────────────────────────────
    - name: agentgateway-ratelimiter
      rules:

        # Rate limiter rejecting a high percentage of requests
        - alert: AgentGatewayRateLimiterOverLimit
          expr: |
            (
              sum(rate(ratelimit_solo_io_over_limit[5m])) by (descriptor)
              /
              sum(rate(ratelimit_solo_io_total_hits[5m])) by (descriptor)
            ) > 0.25
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "Rate limiter rejecting >25% of requests for {{ $labels.descriptor }}"
            description: "Consider raising rate limits or investigating traffic patterns."

        # Near-limit warning — approaching quota
        - alert: AgentGatewayRateLimiterNearLimit
          expr: |
            sum(rate(ratelimit_solo_io_near_limit[5m])) by (descriptor) > 1
          for: 10m
          labels:
            severity: info
          annotations:
            summary: "Traffic approaching rate limit for {{ $labels.descriptor }}"
            description: "Requests are within 80% of the configured limit."
```

---

## Scaling

### What to Scale Against

**CPU is the primary scaling signal** for the vast majority of deployments. There is a strong correlation between CPU utilization and tail latency — when CPU gets too high, p99 latency increases. This makes CPU-based HPA the recommended default.

Memory is the next signal to consider, but in practice the proxy's memory footprint stays low. Memory scaling is only relevant for niche traffic patterns, such as a large number of low-activity, long-lived streams (e.g., thousands of idle MCP/SSE connections).

The proxy is extremely lightweight. In [Gateway API benchmarks](https://github.com/howardjohn/gateway-api-bench/blob/v2/README-v2.md), agentgateway used 4-40MB of memory and under 1% CPU with 5,000 configured routes, with memory growing sub-linearly as route count increased. Avoid over-provisioning — start with modest resource requests and scale based on observed metrics.

### Scaling Signals

| Priority | Signal | Metric | When to use |
|---|---|---|---|
| **1** | **CPU utilization** | `container_cpu_usage_seconds_total` | Primary signal for all deployments. Target 60-70% average. Directly correlates with tail latency. |
| **2** | **Memory** | `container_memory_working_set_bytes` | Secondary signal. Only relevant with many long-lived, low-activity streams. |
| 3 | Runtime saturation | `agentgateway_tokio_global_queue_depth` | Niche. Non-zero means worker threads are fully occupied. Useful for confirming CPU saturation. |
| 3 | Active tasks | `agentgateway_tokio_num_alive_tasks` | Niche. Proportional to concurrent connections. Useful for diagnosing connection backlog. |

### Horizontal Pod Autoscaler

`EnterpriseAgentgatewayParameters` has native `spec.horizontalPodAutoscaler` and `spec.resources` fields. Add the following to your `EnterpriseAgentgatewayParameters`:

```yaml
spec:
  # Resource requests are required for CPU-based HPA
  resources:
    requests:
      cpu: "500m"
      memory: "256Mi"
  horizontalPodAutoscaler:
    spec:
      minReplicas: 2
      maxReplicas: 10
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 60        # React quickly to load spikes
          policies:
            - type: Pods
              value: 2
              periodSeconds: 60                 # Add up to 2 pods per minute
        scaleDown:
          stabilizationWindowSeconds: 300       # Wait 5 min before scaling down (protect long-lived connections)
          policies:
            - type: Pods
              value: 1
              periodSeconds: 120                # Remove at most 1 pod every 2 min
      metrics:
        - type: Resource
          resource:
            name: cpu
            target:
              type: Utilization
              averageUtilization: 65
```

**Note on CPU limits:** We intentionally do not set CPU limits here. CPU limits impose hard throttling that can cause latency spikes on bursty AI workloads (e.g., a sudden batch of streaming completions). CPU requests are sufficient for scheduling and HPA calculations. If your cluster enforces `LimitRange` policies, set the CPU limit generously (e.g., 4x the request) to avoid throttling.

### AI Traffic Considerations

AI/LLM traffic differs from traditional HTTP:

- **Long-lived connections**: A streaming chat completion can last 30-120 seconds. The proxy holds an async task for the entire duration.
- **Low RPS, high connection time**: 100 concurrent streaming users at 60s average = only ~1.7 rps but 100 concurrent tasks.
- **Body buffering**: For LLM requests, the proxy buffers request and response bodies to extract token counts and apply guardrails. This contributes to both CPU (JSON parsing) and memory usage per request.
- **Tokio worker threads**: Default to `CPU_LIMIT` cores. Each worker thread can handle many concurrent async tasks, but CPU-bound work (TLS, JSON parsing) blocks the thread.
- **Scale-down risk**: Aggressive scale-down can terminate pods with active streaming connections. The 300s `stabilizationWindowSeconds` above protects against this.

**Recommendation:** Load test your specific workload and observe the `agentgateway_tokio_global_queue_depth` metric. When queue depth starts consistently rising above 0, you've found the saturation point for that pod. Throughput varies significantly based on payload size, TLS overhead, guardrails enabled, and whether responses are streaming.

---

## Graceful Shutdown

LLM streaming responses, MCP/SSE connections, and agent workloads can run for minutes. The proxy has built-in graceful shutdown that must be configured to match.

### How It Works

When a pod receives `SIGTERM`:

1. **Minimum drain period** (`spec.shutdown.min`, default: `10s`) — the proxy continues accepting connections for this duration but signals clients to migrate: `connection: close` for HTTP/1 and `GOAWAY` for HTTP/2. This gives load balancers and clients time to shift traffic to other pods.
2. **Drain in-flight requests** — after the minimum period, the proxy stops accepting new connections and waits for all active request handlers to complete.
3. **Maximum drain period** (`spec.shutdown.max`, default: `60s`) — hard deadline. Any connections still active after this are forcefully terminated.
4. **Kubernetes SIGKILL** — sent at `terminationGracePeriodSeconds`. This must be greater than `spec.shutdown.max` to allow the proxy to finish draining before being killed.

### Configuration

For workloads with long-lived streaming connections, increase the shutdown periods. Add the following to your `EnterpriseAgentgatewayParameters`:

```yaml
spec:
  shutdown:
    min: 15        # Seconds to keep accepting connections while signaling clients to migrate
    max: 110       # Hard deadline for draining (must be < terminationGracePeriodSeconds)
  deployment:
    spec:
      template:
        spec:
          terminationGracePeriodSeconds: 120   # Kubernetes-level: time before SIGKILL
```

| Setting | Default | Recommended for AI | Description |
|---|---|---|---|
| `spec.shutdown.min` | 10 | 15 | Seconds to continue accepting connections while signaling clients to migrate |
| `spec.shutdown.max` | 60 | 110 | Hard deadline for draining all connections (must be < Kubernetes grace period) |
| `terminationGracePeriodSeconds` | 60 | 120 | Kubernetes-level: time before SIGKILL (must be > `spec.shutdown.max`) |

---

## Pod Disruption Budgets

PDBs ensure minimum availability during voluntary disruptions (node drains, upgrades, cluster autoscaler). `EnterpriseAgentgatewayParameters` has a native `spec.podDisruptionBudget` field — the operator creates and manages the PDB. Add the following to your `EnterpriseAgentgatewayParameters`:

```yaml
spec:
  podDisruptionBudget:
    spec:
      minAvailable: 1
```

**Guidance for choosing `minAvailable` vs `maxUnavailable`:**

| Replicas | Recommended PDB | Effect |
|---|---|---|
| 2 | `minAvailable: 1` | 1 pod can be disrupted at a time |
| 3-5 | `maxUnavailable: 1` | Same effect, but works better with rolling updates |
| 5+ | `maxUnavailable: 2` | Allows faster rolling updates while maintaining capacity |

Verify:
```bash
kubectl get pdb -n agentgateway-system
```

---

## Topology Spread and Anti-Affinity

Spread proxy pods across nodes and zones to survive node failures and zonal outages. Add the following to your `EnterpriseAgentgatewayParameters`:

```yaml
spec:
  deployment:
    spec:
      template:
        spec:
          topologySpreadConstraints:
            - maxSkew: 1
              topologyKey: topology.kubernetes.io/zone
              whenUnsatisfiable: ScheduleAnyway
              labelSelector:
                matchLabels:
                  app.kubernetes.io/name: agentgateway-proxy
            - maxSkew: 1
              topologyKey: kubernetes.io/hostname
              whenUnsatisfiable: ScheduleAnyway
              labelSelector:
                matchLabels:
                  app.kubernetes.io/name: agentgateway-proxy
          affinity:
            podAntiAffinity:
              preferredDuringSchedulingIgnoredDuringExecution:
                - weight: 100
                  podAffinityTerm:
                    labelSelector:
                      matchExpressions:
                        - key: app.kubernetes.io/name
                          operator: In
                          values:
                            - agentgateway-proxy
                    topologyKey: kubernetes.io/hostname
```

**Why `ScheduleAnyway` instead of `DoNotSchedule`:**
- `DoNotSchedule` can prevent scaling if no valid node/zone is available
- `ScheduleAnyway` is a best-effort spread — the scheduler tries to spread but won't block scheduling
- Use `DoNotSchedule` only if you have nodes in 3+ zones and can guarantee capacity in each

Verify pods are spread:
```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy \
  -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,ZONE:.metadata.labels.topology\\.kubernetes\\.io/zone
```

---

## Rolling Upgrades

With PDBs, graceful shutdown, and topology spread configured, rolling upgrades can be performed with minimal disruption. AgentGateway's config propagation is fast — [benchmarks show ~30ms route propagation](https://github.com/howardjohn/gateway-api-bench/blob/v2/README-v2.md) even under concurrent traffic load — but real-world upgrades involve pod restarts, connection draining, and load balancer health checks, so brief interruptions are possible depending on your environment.

### Pre-Upgrade Checklist

```bash
# 1. Verify PDBs are in place
kubectl get pdb -n agentgateway-system

# 2. Verify current replica count (>= 2 for zero-downtime)
kubectl get deploy agentgateway-proxy -n agentgateway-system -o jsonpath='{.spec.replicas}'

# 3. Check current version
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy \
  -o jsonpath='{.items[0].spec.containers[0].image}'

# 4. Verify all pods are healthy
kubectl get pods -n agentgateway-system

# 5. Check no reconciliation errors
kubectl port-forward -n agentgateway-system deploy/enterprise-agentgateway 9092:9092 &
sleep 2
curl -s http://localhost:9092/metrics
kill %1 2>/dev/null
```

### Upgrade

```bash
# 1. Upgrade Helm release — rolling update respects PDBs and graceful shutdown
helm upgrade enterprise-agentgateway solo/enterprise-agentgateway \
  --namespace agentgateway-system \
  --version <new-version> \
  --reuse-values

# 2. Watch the rollout
kubectl rollout status deployment/enterprise-agentgateway -n agentgateway-system --timeout=300s
kubectl rollout status deployment/agentgateway-proxy -n agentgateway-system --timeout=300s

# 3. Verify all pods are on the new version
kubectl port-forward -n agentgateway-system deploy/agentgateway-proxy 15020:15020 &
sleep 2
curl -s http://localhost:15020/metrics
kill %1 2>/dev/null
```

### What to Monitor During Upgrade

Key metrics to watch in Grafana:
- `agentgateway_build_info` — should show old and new version during rollout, then only new version
- `agentgateway_requests_total{status=~"5.."}` — error rate should not spike
- `agentgateway_xds_connection_terminations` — expect `Reconnect` reasons as proxies restart, but no `ConnectionError`
- `kgateway_controller_reconciliations_total{result="error"}` — should remain at 0