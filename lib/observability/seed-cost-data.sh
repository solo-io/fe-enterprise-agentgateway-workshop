#!/usr/bin/env bash
# Backfill ClickHouse with synthetic LLM spend so the Cost Management dashboard
# renders a full history without waiting for live traffic to accrue.
#
# Rows are inserted into platformdb.agw_spans_typed. The built-in materialized
# views fire on insert and populate the 5-minute rollups the UI reads, so the
# Dashboard tab fills in immediately.
#
# The traffic spans four providers so every dashboard pivot has something to
# separate:
#   openai      - the gpt-5.6 family and gpt-5.5, priced at the rates the LLM
#                 Cost Management lab adds in its overlay catalog
#   anthropic   - Claude models, priced at their base catalog rates
#   gcp.gemini  - Gemini models, priced at their base catalog rates
#   bedrock     - Claude and Llama on AWS Bedrock, at AWS list rates
#
# Re-running adds more spend. Set TRUNCATE=true for a clean slate (this also
# discards real traffic and traces).
set -euo pipefail

NS="${NS:-agentgateway-system}"
CH_POD="${CH_POD:-management-clickhouse-shard0-0}"
CLUSTER="${CLUSTER:-mgmt-cluster}"
ROWS="${ROWS:-300000}"   # approximate request count over the window
DAYS="${DAYS:-30}"

ch() { kubectl exec -n "$NS" "$CH_POD" -- clickhouse-client "$@"; }

if [ "${TRUNCATE:-false}" = "true" ]; then
  echo "Truncating agw_spans_typed + cost rollups (this also discards real traffic) ..."
  for t in agw_spans_typed agw_cost_model_5m agw_cost_dimensions_5m; do
    ch --query "TRUNCATE TABLE platformdb.$t"
  done
fi

echo "Seeding ${ROWS} synthetic spans across ${DAYS}d into platformdb.agw_spans_typed ..."

ch --query "
INSERT INTO platformdb.agw_spans_typed
WITH
  [
    'gpt-5.6-luna','gpt-5.6-terra','gpt-5.6-sol','gpt-5.5',
    'claude-haiku-4-5','claude-sonnet-4-5','claude-opus-4-5',
    'gemini-2.5-flash','gemini-2.5-pro','gemini-3-flash-preview',
    'us.anthropic.claude-haiku-4-5-20251001-v1:0','us.anthropic.claude-sonnet-4-6','meta.llama3-1-8b-instruct-v1:0'
  ] AS models,
  [
    'openai','openai','openai','openai',
    'anthropic','anthropic','anthropic',
    'gcp.gemini','gcp.gemini','gcp.gemini',
    'bedrock','bedrock','bedrock'
  ] AS provs,
  -- USD / 1M input tokens
  [0.20, 2.00, 5.00, 2.50,  1.00, 3.00, 5.00,  0.30, 1.25, 0.50,  1.00, 3.00, 0.22] AS inRate,
  -- USD / 1M output tokens
  [1.20, 12.0, 30.0, 15.0,  5.00, 15.0, 25.0,  2.50, 10.0, 3.00,  5.00, 15.0, 0.22] AS outRate,
  -- Weighted pick: cheap models carry request volume, costly ones carry spend.
  [1,1,1,1,2,2,2,3,4,5,5,6,6,7,8,8,9,10,11,12,12,13,13,13] AS mw,
  -- Identities, as parallel arrays: each user belongs to exactly one group, and
  -- groups are deliberately uneven (1-3 members) so the group pivot varies.
  -- alice and bob keep the virtual keys the lab issues them.
  [
    'alice','carol',
    'bob','dave','erin',
    'frank','grace',
    'heidi',
    'ivan','judy','ken'
  ] AS users,
  [
    'research','research',
    'engineering','engineering','engineering',
    'ml-platform','ml-platform',
    'product',
    'support','support','support'
  ] AS groups,
  [
    'vk-alice-001','vk-carol-001',
    'vk-bob-001','vk-dave-001','vk-erin-001',
    'vk-frank-001','vk-grace-001',
    'vk-heidi-001',
    'vk-ivan-001','vk-judy-001','vk-ken-001'
  ] AS keys,
  number AS n,
  mw[(rand(n*7)%24)+1]          AS mi,   -- 1-based model index
  (rand(n*3)%11)+1              AS ui,   -- 1-based identity index
  now() - toIntervalSecond(toUInt64(rand(n)%(${DAYS}*86400))) AS ts,
  2000 + (rand(n*11)%38000)     AS intok,
  800  + (rand(n*13)%16000)     AS outtok,
  toDecimal128((toFloat64(intok)*inRate[mi] + toFloat64(outtok)*outRate[mi]) / 1000000.0, 18) AS cost
SELECT
  ts                                                    AS Timestamp,
  lower(hex(reinterpretAsFixedString(cityHash64(n))))   AS TraceId,
  lower(hex(reinterpretAsFixedString(cityHash64(n+1)))) AS SpanId,
  ''                                                    AS ParentSpanId,
  'agentgateway-proxy'                                  AS ServiceName,
  'POST /openai/*'                                      AS SpanName,
  200000000 + (rand(n*5)%3000000000)                    AS Duration,
  models[mi]                                            AS RequestModel,
  toUInt64(intok)                                       AS InputTokens,
  toUInt64(outtok)                                      AS OutputTokens,
  '${NS}/openai'                                        AS Route,
  '/openai'                                             AS HttpPath,
  200                                                   AS HttpStatus,
  '' AS MCPTarget, '' AS MCPResourceName, '' AS MCPResourceType, '' AS ToolName, '' AS ErrorMsg,
  1                                                     AS IsRoot,
  1                                                     AS HasAttrs,
  provs[mi]                                             AS Provider,
  models[mi]                                            AS ResponseModel,
  cost                                                  AS CostUsd,
  toDecimal128(0,18)                                    AS CacheReadCostUsd,
  toDecimal128(0,18)                                    AS CacheWriteCostUsd,
  map(
    'group',      groups[ui],
    'user',       users[ui],
    'virtualKey', keys[ui],
    'model',      models[mi],
    'provider',   provs[mi]
  )                                                     AS CustomDimensions,
  '${CLUSTER}'                                          AS Cluster,
  '${NS}'                                               AS Namespace
FROM numbers(${ROWS})
"

echo "Done. Totals now in ClickHouse:"
ch --query "
SELECT 'requests' AS k, formatReadableQuantity(count()) AS v FROM platformdb.agw_spans_typed
UNION ALL SELECT 'tokens', formatReadableQuantity(sum(InputTokens+OutputTokens)) FROM platformdb.agw_spans_typed
UNION ALL SELECT 'spend_usd', concat('\$', toString(round(sum(CostUsd),2))) FROM platformdb.agw_spans_typed
"
