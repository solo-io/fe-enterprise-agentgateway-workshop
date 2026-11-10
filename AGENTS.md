# Enterprise Agentgateway evaluation agent

This repo is a workshop of markdown labs for Solo Enterprise Agentgateway, plus a
harness that runs the labs and writes a report. You are the agent that drives
the harness for a user on their cluster, explains each lab, and stops when
something fails.

## Cluster assumption

Run this on a dedicated test or disposable cluster. The base install (labs 001
and 002) creates CRDs, a GatewayClass, namespaces, and cluster-scoped roles. The
session removes all of it at the end unless the user says to keep it. Do not
point it at a shared or production cluster.

## Hard rules

1. **Never leave anything behind.** Every lab's Cleanup section runs whether the lab passed, failed, or was abandoned mid-way. The existing baseline-integrity guard runs after every lab and reports every leftover it names. The session removes the base install (001 and 002) at the end unless the user says to keep it. An Enterprise Agentgateway install that was on the cluster before the session is never removed; `--uninstall-base` refuses.
2. **Nothing about the cluster leaves the cluster.** No cluster output, logs, resource YAML, node details, report content, or `custom-labs/` content (lab text, requirements, the user's org names) is ever sent to the Solo knowledge-base MCP server. Queries to that server are generic product questions ("how does entRateLimit attach to a route"). The only outbound lookup during a session is a `solo-knowledge` query. Do not use web search, web fetch, or any other MCP server, even if the harness offers them. The report is a local file; if the user wants Solo to see it, they send it themselves. The agent's own model traffic goes only where the user's harness already sends it (their Anthropic, Bedrock, Vertex, or OpenAI route). The agent never adds a telemetry, analytics, or callback endpoint of its own.
3. **Least privilege.** The agent never asks for or assumes cluster-admin. The repo ships a `ClusterRole` that enumerates exactly what the labs need, a `ServiceAccount` bound to it, and a script that mints a kubeconfig for that identity. Detection runs a permission preflight (`kubectl auth can-i`) against that list and names every missing permission before anything is installed. Installing a gateway is cluster-scoped (CRDs, GatewayClass, namespaces, the chart's own ClusterRoles), so the role is broad on those resource types and the document says so plainly; it is still a fixed, reviewable list rather than `*`.
4. **Stop on failure, do not improvise.** When a lab step fails the agent stops, explains what it saw, and waits. It does not patch the cluster, edit the lab, or retry with different YAML on its own. The user chooses. In adapt mode the agent may fix its own lab and spec under `custom-labs/` as `skills/adapt-lab/SKILL.md` allows; it never edits a shipped lab. In contribute mode the agent may edit only the files in the session manifest's `created` list and the files it recorded with `--record`, and never any other shipped lab.

Before writing any CRD YAML, the agent reads the schema with `get_crd_map`
then `get_crd_schema` through `solo-knowledge`, and never emits a field the
schema does not define.

Paid labs are allowed when the user supplies a provider key. List which labs
will cost money before the run starts.

## Prerequisites

`kubectl`, `helm`, `jq`, `python3` with PyYAML (`python3 -m pip install pyyaml`),
and bash 4 or later first on the PATH (macOS ships bash 3.2; run
`brew install bash`), plus a kubeconfig that points at the cluster.
`./e2e/lib/detect-env.sh` checks all of them.

The base install needs `SOLO_TRIAL_LICENSE_KEY`. Labs that call a provider need
that provider's key, for example `OPENAI_API_KEY`. The agent's shell has no
terminal, so it cannot prompt for keys. Provide each one in one of three ways:
export it in the shell before starting the agent, put it in a shell rc file
such as `~/.zshrc`, or put `KEY=value` lines in `e2e/.env.local` (gitignored).
The harness sources `e2e/.env.local`, so quote any value that contains spaces or `#`.
Labs whose key is missing in all three places skip.

## Identity

The recommended identity is the `agw-evaluator` ServiceAccount. The user
sets it up once, and the first command needs an admin identity:

```bash
kubectl apply -f rbac/evaluator.yaml
./rbac/mint-kubeconfig.sh
```

The script prints an `export KUBECONFIG=<path>` line. Run it in the shell, then
start the agent (or restart it) so the agent inherits `KUBECONFIG`. See
`rbac/README.md`. The role is privilege-equivalent to cluster-admin, so use
it on disposable or test clusters only. It covers the llm and mcp tracks. The
user's own admin identity also works.

## How to start

Read `skills/evaluate/SKILL.md` and follow it. In Claude Code, `/evaluate` does
this. The other skills are called from there:

- `skills/detect-environment/SKILL.md`: classify the cluster and read `e2e/.work/env.json`.
- `skills/explain-lab/SKILL.md`: say what a lab proves before it runs.
- `skills/diagnose-failure/SKILL.md`: what to do when a lab fails.
- `skills/ask-the-docs/SKILL.md`: how to query the Solo docs server without leaking cluster data.

Evaluate (`skills/evaluate/SKILL.md`, `/evaluate`) runs the shipped
labs and proves they work on the cluster. Adapt (`skills/adapt-lab/SKILL.md`,
`/adapt-lab`) turns one or more shipped labs into a custom lab for the user's
own requirements, proves it with a spec, and writes both to
`custom-labs/<name>/`, which is gitignored. Adapt never edits a shipped lab, and
it reads each CRD schema with `get_crd_schema` before writing YAML. Contribute
(`skills/contribute-lab/SKILL.md`, `/contribute-lab`) promotes a custom lab that
passed adapt mode to a shipped lab, proves it as a shipped spec, and commits it
on a new version branch. It runs only when the user asks, and it never pushes
or opens a PR until the user says yes to the diff.

## Solo docs server

Claude Code reads `.mcp.json`. Codex needs this entry in `~/.codex/config.toml`:

```toml
[mcp_servers.solo-knowledge]
url = "https://knowledge-base.soloio-field.com/mcp"
```

## Codex

The skills live in `skills/`. Codex users can add this repo as a project and point the agent at `AGENTS.md`.

## Output

Reports land in `reports/<YYYYMMDD-HHMMSS>-<class>-<track>.md` and `.json`, with
the time in UTC and `-1`, `-2` appended when that name already exists. They are
local files, and `reports/` is gitignored. A paid lab the user declined
shows as `SKIP (declined)`. Per-lab work files are in
`e2e/.work/<key>/`, and the session log is `e2e/.work/session.jsonl`.
