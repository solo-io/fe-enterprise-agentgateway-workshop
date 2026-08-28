# Observability assets

Shared assets for the observability labs. Run everything from the workshop root.

## `agentgateway-grafana-dashboard-v1.json`

The AgentGateway Grafana dashboard installed by `002`: GenAI metrics, cost tracking, infrastructure, streaming, and MCP panels. `update-dashboard.sh` reloads it into a running Grafana without reinstalling the chart.

Per-model token prices are hardcoded in this dashboard's PromQL and pricing tables. Change them through the `/update-dashboard-pricing` skill rather than by hand.

## `seed-cost-data.sh`

Backfills ClickHouse with synthetic LLM spend so the Cost Management dashboard renders a full history instead of the single spike a few live requests produce.

Rows are inserted into `platformdb.agw_spans_typed`; the built-in materialized views fire on insert and populate the 5-minute rollups the UI reads. The traffic covers four providers — OpenAI (the `gpt-5.6` family at the rates the cost lab's overlay catalog sets), Anthropic, Gemini, and AWS Bedrock — across five teams of one to three members each, keeping alice's and bob's virtual keys so seeded history lines up with live requests.

```bash
./lib/observability/seed-cost-data.sh
ROWS=50000 DAYS=7 ./lib/observability/seed-cost-data.sh   # smaller sample, shorter window
TRUNCATE=true ./lib/observability/seed-cost-data.sh       # clean slate first
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROWS` | `300000` | Approximate request count over the window |
| `DAYS` | `30` | Length of the backfilled window |
| `NS` | `agentgateway-system` | Namespace holding the ClickHouse pod |
| `CH_POD` | `management-clickhouse-shard0-0` | ClickHouse pod name |
| `CLUSTER` | `mgmt-cluster` | `Cluster` value on seeded spans; must match live traffic or the UI's **Scope** filter splits them |

`TRUNCATE=true` clears the spans table and the cost rollups, which discards real traffic and traces along with previous seeds.

Seeding fills the **Dashboard** tab only. The **Budgets** tab reads live rate-limiter counters rather than ClickHouse, so budget usage moves only with real requests through the gateway.

## Used by

- [`labs/observability/llm-cost-management.md`](../../labs/observability/llm-cost-management.md) — `seed-cost-data.sh`
- [`002-set-up-ui-and-monitoring-tools.md`](../../002-set-up-ui-and-monitoring-tools.md) — the Grafana dashboard JSON
