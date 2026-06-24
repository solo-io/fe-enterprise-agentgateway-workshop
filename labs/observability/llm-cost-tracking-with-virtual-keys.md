# LLM Cost Tracking with Virtual Keys

## Pre-requisites
This lab assumes that you have completed `001`, `002`, and `virtual-keys`. Lab `002` is required for the Prometheus metrics sections.

The following resources from the `virtual-keys` lab should still be running:
- Per-user API key Secrets labeled `app: llm-virtual-keys` (e.g. `alice-key`, `bob-key`)
- `api-key-auth` EnterpriseAgentgatewayPolicy (API key authentication)
- `token-budget-policy` EnterpriseAgentgatewayPolicy (token budget enforcement)
- `token-budgets` RateLimitConfig (budget configuration)

This lab also relies on the `user_id` metric label configured in `001` (`metrics.fields.add.user_id: default(apiKey.user_id, "")`). Without it, the per-user queries below return no data.

## Lab Objectives
- Inspect per-request token usage in Agentgateway access logs
- Query per-user token consumption from Prometheus
- Calculate cumulative cost per user using PromQL

## Access logs

Agentgateway logs token usage for every request to stdout in JSON format. Check the last few entries after running the budget tests:

```bash
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway-proxy --prefix --tail 20
```

Each log entry is a JSON object. Look for `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`:

```json
{
  "http.status": 200,
  "gen_ai.usage.input_tokens": 14,
  "gen_ai.usage.output_tokens": 8,
  "gen_ai.request.model": "gpt-4o-mini",
  "gen_ai.provider.name": "openai",
  ...
}
```

## Prometheus metrics

Port-forward to the Prometheus service:

```bash
kubectl port-forward svc/grafana-prometheus-kube-pr-prometheus -n monitoring 9090:9090
```

Open [http://localhost:9090](http://localhost:9090) and search for `agentgateway_gen_ai_client_token_usage` to see the raw histogram with labels for model, provider, and token type.

### Token usage per user

Total input and output tokens broken down by user. The `user_id` label is the custom metric field configured in `001`, populated from the validated API key credential via the CEL expression `apiKey.user_id` (the key's `metadata` block is flattened onto `apiKey`, so it is `apiKey.user_id`, not `apiKey.metadata.user_id`):

```promql
sum by (user_id, gen_ai_token_type) (
  agentgateway_gen_ai_client_token_usage_sum{user_id!=""}
)
```

### Token usage over the last hour per user

```promql
sum by (user_id, gen_ai_token_type) (
  increase(agentgateway_gen_ai_client_token_usage_sum{user_id!=""}[1h])
)
```

> **Note:** `increase()` extrapolates between scrape intervals, so fractional token values are expected and normal. The `user_id!=""` filter drops requests that carried no API key (the label defaults to an empty string), so only per-virtual-key traffic is counted.

### Cumulative cost per user

Total cost per user since the last proxy restart, for `gpt-4o-mini` ($0.15 per 1M input tokens, $0.60 per 1M output tokens). Each token type is summed separately before adding to avoid label conflicts:

```promql
sum by (user_id) (agentgateway_gen_ai_client_token_usage_sum{gen_ai_token_type="input"} * 0.15 / 1000000)
+
sum by (user_id) (agentgateway_gen_ai_client_token_usage_sum{gen_ai_token_type="output"} * 0.60 / 1000000)
```
