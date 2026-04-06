# LLM Cost Tracking

## Pre-requisites
This lab assumes that you have completed `001`, `002`, and `virtual-keys`. Lab `002` is required for the Prometheus metrics sections.

The following resources from the `virtual-keys` lab should still be running:
- Per-user API key secrets (`user-alice-key`, `user-bob-key`)
- `apikey-auth` AuthConfig with label selector
- `virtual-key-budget-policy` EnterpriseAgentgatewayPolicy
- `virtual-key-budgets` RateLimitConfig

## Lab Objectives
- Inspect per-request token usage in Agentgateway access logs
- Query per-user token consumption from Prometheus
- Calculate cumulative cost per user using PromQL

## Access logs

Agentgateway logs token usage for every request to stdout in JSON format. Check the last few entries after running the budget tests:

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 10
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

> **Note:** The `X-User-ID` header is not captured in access logs by default. To add it, extend the `logging.fields.add` block in `EnterpriseAgentgatewayParameters` (configured in `001`):
> ```yaml
> logging:
>   fields:
>     add:
>       x-user-id: 'request.headers["x-user-id"]'
> ```

## Prometheus metrics

Port-forward to the Prometheus service:

```bash
kubectl port-forward svc/grafana-prometheus-kube-pr-prometheus -n monitoring 9090:9090
```

Open [http://localhost:9090](http://localhost:9090) and search for `agentgateway_gen_ai_client_token_usage` to see the raw histogram with labels for model, provider, and token type.

### Token usage per user

Total input and output tokens broken down by user. The `user_id` label is populated from the `X-User-ID` header via the `EnterpriseAgentgatewayParameters` metrics config in `001`:

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

> **Note:** `increase()` extrapolates between scrape intervals, so fractional token values are expected and normal. Series without a `user_id` label are from requests made before the `user_id` metric field was enabled in `001` — they age out after the budget window passes.

### Cumulative cost per user

Total cost per user since the last proxy restart, for `gpt-4o-mini` ($0.15 per 1M input tokens, $0.60 per 1M output tokens). Each token type is summed separately before adding to avoid label conflicts:

```promql
sum by (user_id) (agentgateway_gen_ai_client_token_usage_sum{gen_ai_token_type="input"} * 0.15 / 1000000)
+
sum by (user_id) (agentgateway_gen_ai_client_token_usage_sum{gen_ai_token_type="output"} * 0.60 / 1000000)
```
