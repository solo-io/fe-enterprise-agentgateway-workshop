---
name: evaluate
description: Run a guided Enterprise Agentgateway evaluation on the current cluster, from environment detection through labs, report, and cleanup. Use when the user asks to evaluate, try, or test Enterprise Agentgateway.
---

# Evaluate Enterprise Agentgateway

Work from the repo root. Follow the steps in order. The four hard rules in `AGENTS.md` apply throughout.

## 1. Set expectations and detect the environment

Tell the user, in four sentences:

- This is a dedicated test cluster, and the session installs and removes cluster-scoped resources.
- Every lab cleans up after itself, and the base install is removed at the end unless they say to keep it.
- Nothing about the cluster leaves the machine except through their model provider, and the only service you query is the Solo docs server, with generic product questions.
- If a lab fails, you stop, explain, and they decide what happens next.

Say the recommended identity is the evaluator ServiceAccount from `rbac/README.md`, and that their own admin identity also works.

Run:

```bash
./e2e/lib/detect-env.sh
```

Exit 0: continue. Exit 2: stop and show the message; it names the missing CLI, missing PyYAML, a bash older than 4, an unsupported server version, a missing LoadBalancer, or the permissions to grant with `kubectl apply -f rbac/evaluator.yaml`. Exit 1: show the error; usually the cluster is unreachable, so ask the user to fix their kubeconfig. See `skills/detect-environment/SKILL.md`.

If `existing_install` in `e2e/.work/env.json` is non-empty, say what it names and ask before continuing. Tell the user that the base install in step 4 upgrades that install in place, and that the session will not remove it at the end.

## 2. Choose track and mode

Ask which track to run if the user has not said. Offer this heuristic: the MCP track if their interest is tools and agents talking to internal services, the LLM track if it is model access, cost, and guardrails. Both is allowed; run them one after the other. The second track's `--track` run clears the first track's outcomes, so build the first track's report with the step 8 command before you start the second, and ask keep-or-remove (step 7) only after the last track.

Ask for the mode:

- `enablement` (default): you explain each lab, run it, and pause between use cases.
- `rig`: all labs run unattended and you report at the end.

Start a fresh session log, then record the answer, with `<t>` as `llm` or `mcp` and `<m>` as the mode. Do this again at the start of each track:

```bash
e2e/lib/session-log.sh start
e2e/lib/session-log.sh add decision - "track=<t> mode=<m>"
```

## 3. Resolve credentials and show the plan

Use the `class` field from `e2e/.work/env.json` as `<class>`:

```bash
./e2e/run-e2e.sh --track <t> --env <class> --resolve-creds
python3 e2e/lib/tracks.py list <t> --env <class>
./e2e/run-e2e.sh --list
```

`--resolve-creds` runs nothing and clears outcomes from earlier runs. It does not resolve `SOLO_TRIAL_LICENSE_KEY`; only the base install does. The agent's shell has no terminal, so it cannot prompt for keys. Keys come from the shell environment, a shell rc file, or `KEY=value` lines in `e2e/.env.local`. A paid-lab key that is absent from all three makes those labs skip.

The `tracks.py list` output has one line per lab: `<key>`, `<status>`, `<use-case>`. Show it grouped by use case. Name:

- the labs that need a paid provider key (tier `t1-key` in `--list`), and ask before running them. For each paid lab the user declines, log it and pass `--skip <key>` on every later `run-e2e.sh` call; the report shows it as `SKIP (declined)`:

  ```bash
  e2e/lib/session-log.sh add decision <key> declined
  ```
- the labs that will skip because a credential is missing: join the `⊘ VAR` lines from the `--resolve-creds` output against the REQUIRES column of `--list`, filtered to this track's keys
- the labs with status `duplicate`: listed under two use cases, run once, and shown in both report tables
- the labs marked `no-spec`, reported as `NOT AUTOMATED`; the user can run these by hand from the lab markdown
- the labs marked `not-applicable:<class>`, reported as `NOT APPLICABLE` on this cluster

## 4. Install the base

```bash
./e2e/run-e2e.sh --install-base --only-base
```

If it exits non-zero, stop, show the last 30 lines of its output, and ask the user how to proceed. The usual cause is a missing `SOLO_TRIAL_LICENSE_KEY`.

Otherwise narrate Use Case 1 from `tracks/<t>-track.md`.

## 5. Run the labs

`rig` mode, with one `--skip <key>` per declined lab (leave `--skip` out when there are none):

```bash
./e2e/run-e2e.sh --track <t> --env <class> --no-prompt --skip <key>
```

`enablement` mode: for each `run` entry from the list, in order:

1. If the user declined the lab, run `./e2e/run-e2e.sh <key> --skip <key>` to record it and move on.
2. Invoke `skills/explain-lab/SKILL.md` for the lab.
3. Run `./e2e/run-e2e.sh <key>` and show the result line. Read the outcome line from `e2e/.work/<key>/outcome` and log it:

   ```bash
   e2e/lib/session-log.sh add run <key> "<outcome line>"
   ```

   `status=pass` with no `cleanup_fail=` and no `drift=` field is a pass. `status=skip` with `reason=declined` is a lab the user declined; any other skip names the missing key in `requires=`. Everything else is a failure: `fail`, `timeout`, and a pass that carries `cleanup_fail=` or `drift=`.
4. On a failure, invoke `skills/diagnose-failure/SKILL.md` and do nothing else until the user chooses.

After the last lab of a use case, pause and invite questions. Answer product questions with `skills/ask-the-docs/SKILL.md`.

In both modes, name every `LEFTOVER (warning):`, `LEFTOVER CHECK NOT RUN`, `BASELINE DRIFT`, and `CLEANUP INCOMPLETE` line a run prints, as it happens, instead of waiting for step 7. A `LEFTOVER (warning):` line names an object a shipped lab left behind; it does not fail the lab in this release. Show it to the user and offer to delete it, and delete it only on a yes.

Never edit a shipped lab under `labs/` or a shipped spec under `e2e/specs/`.

## 6. Diagnose rig failures

In `rig` mode, take every lab listed under `Failed:` in the run summary. For each one, follow steps 1 and 2 of `skills/diagnose-failure/SKILL.md`: read `e2e/.work/<key>/`, record the failure, and explain it.

```bash
e2e/lib/session-log.sh add failure <key> "<one-line explanation>"
```

Then offer the four options from that skill once, for the whole set, and do nothing to the cluster until the user picks. Record each choice as that skill says. Skip this step in `enablement` mode, where each failure was handled as it happened.

## 7. Keep or remove the base

If `existing_install` was non-empty in step 1, the install was there before the session, and `--uninstall-base` refuses to remove it. Do not offer removal. Tell the user it stays and log `keep-base` as below.

Otherwise ask whether to keep or remove the base install. If they keep it:

```bash
e2e/lib/session-log.sh add keep-base - "base install kept at the user's request"
```

Otherwise:

```bash
./e2e/run-e2e.sh --uninstall-base
```

It removes the base and the `monitoring.coreos.com` CRDs the base added. It compares the cluster with `e2e/.work/base-install.json`, the record `--install-base` wrote, and prints `LEFTOVER NAMESPACE:` and `LEFTOVER CRD:` for anything the session added that remains. It keeps the Gateway API CRDs because they are cluster-wide, and prints how many it kept. It writes `e2e/.work/env-teardown.json`. Show `existing_install` from that file and confirm it is empty. If the command exits non-zero, show what it names; the report lists the same lines under Leftovers. `BASE NOT REMOVED:` means there was no record or the install predates the session, and nothing was removed.

## 8. Build and show the report

```bash
./e2e/run-e2e.sh --report-only --track <t> --env <class>
```

This builds the report from the work directory and runs nothing. Show the two paths it prints: `reports/<YYYYMMDD-HHMMSS>-<class>-<track>.md` and `.json`. To compare with an earlier report of the same track:

```bash
python3 e2e/lib/report.py compare <older.json> <newer.json>
```

Say the report is a local file and nothing was sent anywhere. If the user wants Solo to see it, they send it themselves.
