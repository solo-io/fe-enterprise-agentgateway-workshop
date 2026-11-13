---
name: diagnose-failure
description: Handle a lab run whose outcome is not a pass. Read the failing block, explain it, offer choices, and wait. Use whenever a lab or custom lab run fails.
---

# Diagnose a failed lab

The harness has already stopped the lab. `<key>` is the lab key, for example `mcp/in-cluster-mcp`, or `custom/<name>` for a custom lab. A shipped lab's work folder is `e2e/.work/<key>/`; a custom lab's is `custom-labs/<name>/.work/run/`, and the steps below mean that folder wherever they say `e2e/.work/<key>/`. Do these steps, in order, and nothing else.

1. Read the failing block, its captured output, and the lab's expected output from `e2e/.work/<key>/`: `e2e/.work/<key>/driver.sh` is the generated script, `blocks/NNNN.sh` is each block, `blocks/NNNN.out` is its output, `result` is the pass/fail line, and `outcome` is the recorded outcome. Then record the failure:

   ```bash
   e2e/lib/session-log.sh add failure <key> "<one-line explanation>"
   ```
2. Explain in plain language, by `skills/stop-slop/SKILL.md`: what the lab expected, what the cluster returned, and the most likely cause. A missing or rejected key, a provider outage, or a rate limit at the provider are common causes outside the lab. If the cause is a product question, you may consult `soloio-docs-mcp` with a generic query, following `skills/ask-the-docs/SKILL.md`. Never paste cluster output into that query.
3. Offer exactly these options:

   - Retry the lab
   - Skip it and continue
   - Work around it live
   - Stop the session

   In adapt mode, `skills/adapt-lab/SKILL.md` replaces these with its own choices.

   In contribute mode, the key is `<category>/<name>` and the choices are:

   - Retry step 5 as written: `e2e/lib/session-log.sh add decision <category>/<name> retry`, then `./e2e/run-e2e.sh <category>/<name>`.
   - Stop and go to step 8 (undo) in `skills/contribute-lab/SKILL.md`: `e2e/lib/session-log.sh add decision <category>/<name> stop`.

   Contribute mode has no skip and no live workaround, because a promoted lab must pass as written.

4. Do nothing to the cluster until the user picks.
5. The harness already ran the lab's Cleanup when it exited, whatever the choice. Confirm it from the cleanup phase in `e2e/.work/<key>/blocks/*.out`, show any leftovers, and never run cleanup blocks by hand.

## What each choice does

Retry: record it, then run `./e2e/run-e2e.sh <key>` again. A retry that passes after a workaround is never reported as a clean pass.

```bash
e2e/lib/session-log.sh add decision <key> retry
```

Skip: record it, then continue with the next lab.

```bash
e2e/lib/session-log.sh add decision <key> skip
```

Work around it live: propose the change and say what it does. Apply it only on a yes. Before applying it, record it:

```bash
e2e/lib/session-log.sh add deviation <key> "<what was applied and why>"
```

Then apply it and re-run with `./e2e/run-e2e.sh <key>`. Revert the workaround after the re-run, or say so if the baseline guard will report it as a leftover. The report marks the lab `PASS (deviation)` with the change recorded.

Stop: run nothing further, then record it:

```bash
e2e/lib/session-log.sh add decision <key> stop
```

Then go to the keep-or-remove step in `skills/evaluate/SKILL.md`.

Never edit a shipped lab under `labs/` or a shipped spec under `e2e/specs/`, and never patch the cluster or retry with different YAML on your own.
