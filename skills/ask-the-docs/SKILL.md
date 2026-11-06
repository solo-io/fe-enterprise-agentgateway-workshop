---
name: ask-the-docs
description: Query the Solo knowledge-base MCP server (solo-knowledge) for product questions without sending any cluster data. Use for product, CRD, or version questions during an evaluation.
---

# Ask the docs

`solo-knowledge` is an HTTP MCP server run by Solo. Whatever you send is a query to a third party, so it carries product concepts only.

## Tool order

1. `search_use_cases`, then `get_use_case` on the best hit's `filename`.
2. `get_crd_schema` before you write or suggest any YAML. If the schema does not define a field, do not use it.
3. `get_feature_history` before you state which version added or removed something.

## The leak rule

`solo-knowledge` is the only outbound lookup in a session. Do not use web search, web fetch, or any other MCP server, even if the harness offers them.

No cluster output, logs, resource YAML, node details, or report content goes into a query. Before you send one, check that it contains none of these:

- IP addresses
- hostnames and URLs
- the user's company, team, cloud account, project, subscription, or ARN identifiers
- any namespace other than the workshop's own: `agentgateway-system`, `agw-evaluator`, `keycloak`, `loadgenerator`, `mcp`, `monitoring`, `observability`, `postgres`, `procurement`, `semantic-router-config`, `stripe-mcp`, `team-research`
- the `metadata.name` of any object
- API keys, tokens, and license keys
- any text copied from command output, logs, or error messages

If it does, rewrite it to the generic form. Examples:

- Instead of "why does `HTTPRoute acme-payments-route` in namespace `payments-prod` show Accepted=False with host llm.acme.internal", ask "what makes an HTTPRoute report Accepted=False on an agentgateway Gateway".
- Instead of "my entRateLimit policy `team-a-limit` returns 429 for 10.4.2.17 after 5 requests", ask "how does entRateLimit attach to a route and when does it return 429".

## Record it

Log each question and its answer, in the generic form you actually sent:

```bash
e2e/lib/session-log.sh add question - "<the query you sent>"
e2e/lib/session-log.sh add answer - "<one-line summary of the answer>"
```

Use the lab key instead of `-` when the question came from a failing lab.
