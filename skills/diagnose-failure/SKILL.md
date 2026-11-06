---
name: diagnose-failure
description: Handle a lab that exited non-zero. Read the failing block, explain it, offer four options, and wait. Use whenever a lab run fails.
---

# Diagnose a failed lab

The harness has already stopped the lab. `<key>` is the lab key, for example `mcp/in-cluster-mcp`. Do exactly these five steps.

1. Read the failing block, its captured output, and the lab's expected output from `e2e/.work/<key>/`: `e2e/.work/<key>/driver.sh` is the generated script, `blocks/NNNN.sh` is each block, `blocks/NNNN.out` is its output, `result` is the pass/fail line, and `outcome` is the recorded outcome.
2. Explain in plain language: what the lab expected, what the cluster returned, and the most likely cause. If the cause is a product question, you may consult `solo-knowledge` with a generic query, following `skills/ask-the-docs/SKILL.md`. Never paste cluster output into that query.
3. Offer exactly these options:

   - Retry the lab
   - Skip it and continue
   - Work around it live
   - Stop the session

4. Do nothing to the cluster until the prospect picks. Record the failure first:

   ```bash
   e2e/lib/session-log.sh add failure <key> "<one line: block number and what failed>"
   ```

5. Run the lab's Cleanup regardless of the choice, including on stop.

## What each choice does

Retry: run `./e2e/run-e2e.sh <key>` again. A retry that passes after a workaround is never reported as a clean pass.

Skip: continue with the next lab.

Work around it live: propose the change and say what it does. Apply it only on a yes. Before applying it, record it:

```bash
e2e/lib/session-log.sh add deviation <key> "<what was applied and why>"
```

Then apply it and re-run with `./e2e/run-e2e.sh <key>`. The report marks the lab `PASS (deviation)` with the change recorded.

Stop: run nothing further except Cleanup, then record it:

```bash
e2e/lib/session-log.sh add decision <key> stop
```

Then go to the keep-or-remove step in `skills/evaluate/SKILL.md`.

Do not edit the lab, patch the cluster, or retry with different YAML on your own.
