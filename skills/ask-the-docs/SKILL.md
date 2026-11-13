---
name: ask-the-docs
description: Query the public Solo docs MCP server (soloio-docs-mcp) for product questions without sending any cluster data. Use for product, CRD, or version questions during a session.
---

# Ask the docs

`soloio-docs-mcp` is an HTTP MCP server run by Solo. Whatever you send is a query to a third party, so it carries product concepts only.

## Check the connection at startup

Do this at the start of evaluate, adapt, and contribute, before cluster changes:

1. Inspect this session's tools for `search`, `get_chunks`, and `get_full_page`
   from `soloio-docs-mcp`.
2. Call `search` with `product: solo-enterprise-for-agentgateway` and a generic
   query such as "agentgateway installation prerequisites". Record the generic
   question and answer after the session log starts.
3. If tools are absent or fail, run `python3 e2e/lib/check-solo-docs.py`.
   It sends only MCP handshake and tool-inventory requests to the fixed public
   endpoint, `https://search.solo.io/mcp`. Use normal network approval if blocked.
   It reads no cluster data, credentials, reports, or custom labs.
4. Report separately whether the agent exposes Solo tools and whether the
   endpoint check succeeded. Configuration alone does not prove a connection.

The public server requires no authentication. Its setup and tool reference is
at `https://search.solo.io/`. For Codex, `.codex/config.toml` supplies the project
connection. Check recognition with `codex mcp get soloio-docs-mcp`. If needed,
the global setup command is:

```sh
codex mcp add soloio-docs-mcp --url https://search.solo.io/mcp
```

Adding a global entry needs permission when only project changes are authorized.
After configuration changes, check the current session's tools again. If absent,
ask the user to open a new session and repeat the connection check.

An unchanged shipped evaluation may continue with a clear notice when docs are
disconnected. Product explanations still require successful docs queries.
Adapt stops before writing YAML if the matching local schemas cannot be read.

## Product questions

1. Use `search` with `product: solo-enterprise-for-agentgateway` and a generic
   product query. Select another collection only for questions about that
   product, such as standalone Agentgateway or Gateway API.
2. For incomplete relevant snippets, call `get_chunks` using the collection,
   source URL, and chunk indices from search. Use `get_full_page` if the complete
   page is needed and chunk retrieval is insufficient.
3. For version claims, search for the target version's release notes and read
   the relevant source. If the docs do not establish the claim, say so.

## CRD schemas before YAML

The public server provides search and page retrieval, not schema tools.
Documentation examples do not replace schema checks.

Read the target CRD locally with `kubectl get crd <crd-name> -o json`.
Identify the kind by `spec.group` and `spec.names.kind`. Select the requested
API version in `spec.versions[]`, check `served: true`, and read that version's
`schema.openAPIV3Schema`. Follow nested properties, item schemas, and validation
rules for every field you write. Keep the output local; never send it to MCP.

If the CRD is not installed, read a local CRD artifact for the exact target
release, such as the chart or manifest used by the workshop. Do not install or
change cluster resources just to obtain a schema. Stop YAML authoring if the
matching schema is unavailable or incomplete, and name the missing kind and
version. Never emit a field the schema does not define.

## The leak rule

`soloio-docs-mcp` is the only service you query for product information. Do not use web search, web fetch, or any other MCP server, even if the harness offers them. `.claude/settings.json` denies web search and web fetch in Claude Code.

No cluster output, logs, resource YAML, node details, report content, or `custom-labs/` content goes into a query. Before you send one, check that it contains none of these:

- IP addresses
- hostnames and URLs
- the user's company, team, cloud account, project, subscription, or ARN identifiers
- any namespace other than the workshop's own: `agentgateway-system`, `agw-evaluator`, `keycloak`, `loadgenerator`, `mcp`, `monitoring`, `observability`, `postgres`, `procurement`, `semantic-router-config`, `stripe-mcp`, `team-research`
- the `metadata.name` of any object
- names the user chose: model aliases, tier names, team names, user names, route names
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

Use the lab key, or `custom/<name>`, instead of `-` when the question came from a lab.
