# Enterprise Agentgateway evaluation agent (Alpha)

This repo is a workshop of markdown labs for Solo Enterprise Agentgateway, plus a
harness that runs the labs and writes a report. The agent layer is Alpha. You are
the agent that drives the harness for a user on their cluster, explains each lab,
and stops when something fails.

## Cluster assumption

Run this on a dedicated test or disposable cluster. The base install (labs 001
and 002) creates CRDs, a GatewayClass, namespaces, and cluster-scoped roles. The
session removes all of it at the end unless the user says to keep it. Do not
point it at a shared or production cluster.

## Hard rules

1. **Never leave anything behind.** Every lab's Cleanup section runs whether the lab passed, failed, or was abandoned mid-way. After every lab the runner checks `agentgateway-system` against its baseline and lists every object the lab left behind. A custom lab with a leftover fails. A shipped lab's leftovers print as `LEFTOVER (warning):` lines in this release. Report every leftover and every baseline difference to the user. The session removes the base install (001 and 002) at the end unless the user says to keep it. An Enterprise Agentgateway install that was on the cluster before the session is never removed; `--uninstall-base` refuses.
2. **No cluster data leaves the machine except through the user's model provider.** No cluster output, logs, resource YAML, node details, report content, or `custom-labs/` content (lab text, requirements, the user's org names) goes to the public Solo docs MCP server or to any other service. Queries to that server are generic product questions ("how does entRateLimit attach to a route"). Do not use web search, web fetch, or any other MCP server, even if the harness offers them. The agent's own model traffic goes only where the user's harness already sends it (their Anthropic, Bedrock, Vertex, or OpenAI route), and it carries what the agent reads. The report is a local file; if the user wants Solo to see it, they send it themselves. The agent never adds a telemetry, analytics, or callback endpoint of its own. "Outbound calls" below lists every other connection a session makes.
3. **Scoped, reviewable identity.** The repo ships a `ClusterRole` that enumerates what the labs need, with no wildcards, a `ServiceAccount` bound to it, and a script that mints a kubeconfig for that identity. Detection runs a permission preflight (`kubectl auth can-i`) against that list and names every missing permission before anything is installed. The agent never asks the user to grant more than the role lists. The role guards against accidents, not against a hostile agent: it can create ClusterRoles and bindings and read Secrets cluster-wide, so it is privilege-equivalent to cluster-admin. That is why the cluster must be disposable.
4. **Stop on failure, do not improvise.** When a lab step fails the agent stops, explains what it saw, and waits. It does not patch the cluster, edit the lab, or retry with different YAML on its own. The user chooses. Never edit a shipped lab under `labs/` or a shipped spec under `e2e/specs/`. In adapt mode the agent may fix its own lab and spec under `custom-labs/` as `skills/adapt-lab/SKILL.md` allows. In contribute mode the agent may edit only the files in the session manifest's `created` list and the files it recorded with `--record`.

Before you state how a product feature behaves, ask the Solo docs server with a
generic query, as `skills/ask-the-docs/SKILL.md` describes. Before writing any
CRD YAML, read the local `openAPIV3Schema` for the target kind and served version,
as `skills/ask-the-docs/SKILL.md` describes. Never emit a field the schema does not define.

Write all prose by `skills/stop-slop/SKILL.md`: what you tell the user, lab
text, `CHANGES.md`, commit messages, and PR bodies.

Paid labs are allowed when the user supplies a provider key. List which labs
will cost money before the run starts.

## Outbound calls

Besides the model provider, a session connects to these:

| Mode | From | To | Why |
|------|------|----|-----|
| All | the agent | `search.solo.io` | generic product questions to `soloio-docs-mcp` |
| Evaluate, adapt | the harness and the cluster | `github.com`, `us-docker.pkg.dev`, `prometheus-community.github.io` | Gateway API CRDs, the Enterprise Agentgateway charts and images, the monitoring chart |
| Evaluate, adapt | the cluster | the images and endpoints each lab names | lab workloads, and the provider APIs that paid labs call |
| Contribute | the agent | `github.com` | `git fetch`, `git ls-remote`, `gh`, and, after the user's yes, `git push` and `gh pr create` |

## Prerequisites

`kubectl`, `helm`, `jq`, `python3` with PyYAML,
and bash 4 or later first on the PATH (macOS ships bash 3.2), plus a kubeconfig
that points at the cluster. See [evaluation prerequisite setup](e2e/README.md#install-evaluation-prerequisites-on-macos)
for Homebrew and local Bash installation options, and PyYAML in a virtual
environment selected with `E2E_PYTHON`.
`./e2e/lib/detect-env.sh` checks all of them. Contribute mode also needs `gh`,
logged in.

`SOLO_TRIAL_LICENSE_KEY` is required for `evaluate`; the agent stops until it is
set. The agent asks about `OPENAI_API_KEY`; if you continue without it, the labs
that need it skip. Other provider keys, such as `FRED_API_KEY`, are optional. The agent's shell has no
terminal, so it cannot prompt for keys. Provide each one in one of three ways:
export it in the shell before starting the agent, put it in a shell rc file
such as `~/.zshrc`, or put `KEY=value` lines in `e2e/.env.local` (gitignored).
The harness sources `e2e/.env.local`, so quote any value that contains spaces or `#`.
Copy `e2e/env.local.example` to start.
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
`rbac/README.md`. It covers the llm and mcp tracks. The user's own admin
identity also works.

## Modes

Evaluate (`skills/evaluate/SKILL.md`, `/evaluate`) runs the shipped labs of a
track and proves they work on the cluster. Enablement mode explains each lab
before it runs and stops on the first failure. Rig mode runs the whole track
unattended and reports every failure at the end.

Adapt (`skills/adapt-lab/SKILL.md`, `/adapt-lab`) turns one or more shipped labs
into a custom lab for the user's own requirements, proves it with a spec, and
writes both to `custom-labs/<name>/`, which is gitignored. Adapt never edits a
shipped lab, and it reads each local CRD `openAPIV3Schema` before writing YAML.

Contribute (`skills/contribute-lab/SKILL.md`, `/contribute-lab <name>`) promotes
a custom lab that passed adapt mode to a shipped lab, proves it as a shipped spec,
and commits it on a new version branch. It works from a clone of the main repo or
of a fork. It runs only when the user asks, and it never pushes or opens a PR
until the user says yes to the diff.

## How to start

In Claude Code, run `/evaluate`, `/adapt-lab`, or `/contribute-lab <name>` from
the repo root. Each command reads the matching skill. The other skills are
called from those three:

- `skills/detect-environment/SKILL.md`: classify the cluster and read `e2e/.work/env.json`.
- `skills/explain-lab/SKILL.md`: say what a lab proves before it runs.
- `skills/diagnose-failure/SKILL.md`: what to do when a lab fails.
- `skills/stop-slop/SKILL.md`: the rules for every piece of prose you write.
- `skills/ask-the-docs/SKILL.md`: how to query the Solo docs server without leaking cluster data.

## Claude Code settings

`.mcp.json` adds the Solo docs server. `.claude/settings.json` enables only that
server from `.mcp.json`, denies web search, web fetch, `sudo`, and
`kubectl delete crd`, and asks before every `git push` and `gh pr create`. MCP
servers from the user's own Claude Code configuration still load; turn them off
for this project with `/mcp`.

## Codex

Codex reads this file. It has no slash commands, so tell it which mode to run,
for example "read AGENTS.md and follow skills/evaluate/SKILL.md". The skills
live in `skills/`. The repo's `.codex/config.toml` configures Solo docs for this
project. For other repos, the same entry can go in `~/.codex/config.toml`:

```toml
[mcp_servers.soloio-docs-mcp]
url = "https://search.solo.io/mcp"
```

At the start of every mode, follow the connection check in
`skills/ask-the-docs/SKILL.md`. Report missing tools or failed connections before
installing anything. A configured server is not proof of a working connection.

Leave Codex web search off: do not start it with `--search`. Codex has no
equivalent of `.claude/settings.json` in this repo, so rule 2 rests on the
agent following it.

## Output

- Track reports: `reports/<YYYYMMDD-HHMMSS>-<class>-<track>.md` and `.json`, time in UTC, with `-1`, `-2` appended when the name exists. `reports/` is gitignored. A paid lab the user declined shows as `SKIP (declined)`.
- Custom labs: `custom-labs/<name>/` with `<name>.md`, `spec.yaml`, `CHANGES.md`, and reports in `custom-labs/<name>/reports/`. `custom-labs/` is gitignored. A custom lab's run files stay in its folder, in `custom-labs/<name>/.work/run/`, so deleting `custom-labs/<name>/` removes all of it.
- Per-lab work files for a shipped lab: `e2e/.work/<key>/`. Contribute: `e2e/.work/contribute/<name>/`, which stays after the lab folder is deleted, since its manifest tracks the repo changes.
- Session log: `e2e/.work/session.jsonl`. Environment: `e2e/.work/env.json`.
