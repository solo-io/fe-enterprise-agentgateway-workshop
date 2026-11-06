---
name: evaluate
description: Run a guided Enterprise Agentgateway evaluation on the current cluster, from environment detection through labs, report, and cleanup. Use when the prospect asks to evaluate, try, or test Enterprise Agentgateway.
---

# Evaluate Enterprise Agentgateway

Work from the repo root. Follow the steps in order. The four hard rules in `AGENTS.md` apply throughout.

## 1. Set expectations and detect the environment

Tell the prospect, in four sentences:

- This is a dedicated test cluster, and the session installs and removes cluster-scoped resources.
- Every lab cleans up after itself, and the base install is removed at the end unless they say to keep it.
- Nothing about the cluster is sent anywhere; the only outbound call is a generic product question to the Solo docs server.
- If a lab fails, you stop, explain, and they decide what happens next.

Say the recommended identity is the evaluator ServiceAccount from `rbac/README.md`, and that their own admin identity also works.

Run:

```bash
./e2e/lib/detect-env.sh
```

Exit 0: continue. Exit 2: stop and show the message; it names the missing CLI, unsupported server version, missing LoadBalancer, or the permissions to grant with `kubectl apply -f rbac/evaluator.yaml`. Exit 1: the cluster is unreachable, so ask the prospect to fix their kubeconfig. See `skills/detect-environment/SKILL.md`.

If `existing_install` in `e2e/.work/env.json` is non-empty, say what it names and ask before continuing.

## 2. Choose track and mode

Ask which track to run if the prospect has not said. Offer this heuristic: the MCP track if their interest is tools and agents talking to internal services, the LLM track if it is model access, cost, and guardrails. Both is allowed; run them one after the other. The second track's `--track` run clears the first track's outcomes, so build the first track's report (step 6) before you start the second.

Ask for the mode:

- `enablement` (default): you explain each lab, run it, and pause between use cases.
- `rig`: all labs run unattended and you report at the end.

Record the answer, with `<t>` as `llm` or `mcp` and `<m>` as the mode:

```bash
e2e/lib/session-log.sh add decision - "track=<t> mode=<m>"
```

## 3. Resolve credentials and show the plan

Use the `class` field from `e2e/.work/env.json` as `<class>`:

```bash
./e2e/run-e2e.sh --track <t> --env <class> --resolve-creds
python3 e2e/lib/tracks.py list <t> --env <class>
./e2e/run-e2e.sh --list
```

`--resolve-creds` runs nothing and clears outcomes from earlier runs. It does not resolve `SOLO_TRIAL_LICENSE_KEY`; only the base install does. The agent's shell has no terminal, so it cannot prompt for keys. Keys come from the shell environment, a shell rc file, or `KEY=value` lines in `e2e/.env.local`. A paid-lab key that is absent from all three makes those labs skip, which is fine.

The `tracks.py list` output has one line per lab: `<key>`, `<status>`, `<use-case>`. Show it grouped by use case. Name:

- the labs that need a paid provider key (tier `t1-key` in `--list`), and ask before running them
- the labs that will skip because a credential is missing: join the `⊘ VAR` lines from the `--resolve-creds` output against the REQUIRES column of `--list`, filtered to this track's keys
- the labs with status `duplicate`: listed under two use cases, run once, and shown in both report tables
- the labs marked `no-spec`, reported as `NOT AUTOMATED`; the prospect can run these by hand from the lab markdown
- the labs marked `not-applicable:<class>`, reported as `NOT APPLICABLE` on this cluster

## 4. Install the base

```bash
./e2e/run-e2e.sh --install-base --only-base
```

If it exits non-zero, stop, show the last 30 lines of its output, and ask the prospect how to proceed. The usual cause is a missing `SOLO_TRIAL_LICENSE_KEY`.

Otherwise narrate Use Case 1 from `tracks/<t>-track.md`.

## 5. Run the labs

`rig` mode:

```bash
./e2e/run-e2e.sh --track <t> --env <class> --no-prompt --report
```

Then go to step 7.

`enablement` mode: for each `run` entry from the list, in order:

1. Invoke `skills/explain-lab/SKILL.md` for the lab.
2. Run `./e2e/run-e2e.sh <key>` and show the result line.
3. On any non-zero exit, invoke `skills/diagnose-failure/SKILL.md` and do nothing else until the prospect chooses.

After the last lab of a use case, pause and invite questions. Answer product questions with `skills/ask-the-docs/SKILL.md`.

## 6. Build the report

```bash
./e2e/run-e2e.sh --report-only --track <t> --env <class>
```

This builds the report from the work directory and runs nothing. Skip it in `rig` mode, where step 5 already wrote the report.

## 7. Keep or remove the base

Ask whether to keep or remove the base install. If they keep it:

```bash
e2e/lib/session-log.sh add keep-base - "base install kept at the prospect's request"
```

Otherwise:

```bash
./e2e/run-e2e.sh --uninstall-base
./e2e/lib/detect-env.sh
```

Show `existing_install` from `e2e/.work/env.json` and confirm it is empty.

## 8. Show the report

Show the paths: `reports/<YYYYMMDD-HHMM>-<class>-<track>.md` and `.json`. Say the report is a local file and nothing was sent anywhere. If the prospect wants Solo to see it, they send it themselves.
