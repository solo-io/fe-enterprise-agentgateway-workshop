---
name: adapt-lab
description: Turn one or more shipped labs into a custom lab and spec for the user's own requirements, prove it on their cluster, and hand it off. Use when the user wants to extend, combine, or reskin a lab for their scenario.
---

# Adapt a lab

Work from the repo root. Follow the steps in order. The four hard rules in `AGENTS.md` apply throughout. One session builds one custom lab.

A custom lab lives in `custom-labs/<name>/`, which is gitignored:

```
custom-labs/<name>/
  <name>.md      the lab
  spec.yaml      the e2e spec; its lab: is custom-labs/<name>/<name>.md
  CHANGES.md     source labs, deltas, requirement-to-assertion table
  reports/       written by --report-only
  assets/        optional files the lab applies
```

Never edit a shipped lab under `labs/` or a shipped spec under `e2e/specs/`.

## 1. Set expectations

Tell the user, in four sentences:

- Nothing touches the cluster until they approve a written proposal.
- The result is a lab they can run by hand plus a spec that re-runs the proof, both under `custom-labs/<name>/`.
- Nothing about the cluster or their requirements is sent anywhere; the only outbound call is a generic product or schema question to the Solo docs server.
- At the end they choose whether the custom configuration stays on the cluster.

## 2. Intake

Run:

```bash
./e2e/lib/detect-env.sh
```

Exit 0: continue. Any other exit: handle it as step 1 of `skills/evaluate/SKILL.md` does. If `existing_install` in `e2e/.work/env.json` is empty, the base is missing: offer to install it and run this on a yes:

```bash
./e2e/run-e2e.sh --install-base --only-base
```

Recommend once that the user run the relevant baseline lab or track first, so the custom work starts from a known-good cluster. They may decline.

Start a fresh session log:

```bash
e2e/lib/session-log.sh start
```

The user names one or more source labs or describes the use case. When they describe it, find the closest labs from the tables in `README.md` and `tracks/`, read the candidate labs, and confirm the choice with the user. The request is usually one of these:

- Extend: keep one lab's pattern and scale it, for example tiered rate limits instead of one counter.
- Compose: join two labs, for example JWT auth from an OIDC provider plus rate limits keyed on a token claim.
- Reskin: keep a lab's mechanics and swap in the user's organisation: team names, models, budgets.

Ask only for what you cannot infer from the labs: tiers, claims, team names, models, thresholds. Ask one question at a time. Agree a name for the lab with the user. It must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`, be kebab-case, and not exist yet under `custom-labs/`.

Write every requirement as a numbered, testable statement in the user's terms, for example: "R1: Gold tier gets 1000 input tokens per minute, silver 200, and a token without a tier claim is rejected." If a statement cannot be tested, push it back to the user. Do not accept it.

Record the outcome of intake:

```bash
e2e/lib/session-log.sh add decision custom/<name> "sources=<labs> requirements=<count>"
```

## 3. Proposal

Before you write any CRD YAML, call `get_crd_map` then `get_crd_schema` for each kind through `solo-knowledge`, as `skills/ask-the-docs/SKILL.md` describes. Never emit a field the schema does not define. Queries name the product and the kind only, never the user's requirements, org names, or lab text.

Write `custom-labs/<name>/CHANGES.md` from this template and show it:

```markdown
# <name>

## Source labs

- labs/<category>/<lab>.md: keeps <sections>, changes <sections>, drops <sections>

## Configuration

| Kind | Name | Namespace | What it does |
|------|------|-----------|--------------|

## Requirements

| Id | Requirement | Lab section | Positive assertion | Negative assertion |
|----|-------------|-------------|--------------------|--------------------|
| R1 | <statement> | <heading> | <step and expected result> | <step and expected deny> |

## Interaction check

<For a compose: which source-lab assertions must still hold with both configurations applied.>

## Open questions

<What the labs and the docs server could not confirm, with the user's options.>

## Cluster footprint

- Namespaces: <list>
- Cluster-scoped resources: <list>
- External endpoints: <list, marking each one that costs money>

## Changes during validation

<Filled in at hand-off.>
```

The user approves, edits, or stops. No cluster command runs before the user approves the proposal.

```bash
e2e/lib/session-log.sh add decision custom/<name> "proposal approved"
```

## 4. Build and validate

Write `custom-labs/<name>/<name>.md` following "Writing the lab" and `custom-labs/<name>/spec.yaml` following "Writing the spec". Then loop:

1. Lint. Fix your own authoring until it is clean:

   ```bash
   ./e2e/run-e2e.sh --custom <name> --lint
   ```

2. Run. The harness lifts the lab's bash blocks and runs them as written, then runs Cleanup:

   ```bash
   ./e2e/run-e2e.sh --custom <name>
   ```

   Read the outcome line from `e2e/.work/custom/<name>/outcome` and log it:

   ```bash
   e2e/lib/session-log.sh add run custom/<name> "<outcome line>"
   ```

3. On a failing step, apply `skills/diagnose-failure/SKILL.md` with the key `custom/<name>`. Read the work dir, explain what the cluster did against what the lab expected, and stop until the user picks.
4. Any `LEFTOVER: <kind>/<name>` line is a Cleanup bug in your lab. Fix the Cleanup section and run again. `LEFTOVER CHECK NOT RUN` means the snapshot failed; say so and do not count the run as clean.

You may fix your own authoring: a wrong ordinal, a heading mismatch, a missing `retry`, a field the schema rejects. You may not change a requirement, loosen an assertion, or invent configuration to make a step pass. If a requirement cannot be met with what the labs and the docs server provide, report it to the user as a gap. If the user wants to keep the rest, the lab states the gap in a callout and `CHANGES.md` records it.

Repeat until the spec passes from a clean state: Cleanup ran, then a fresh run passes with the baseline-integrity guard clean. A pass means all of these hold:

- `--custom <name> --lint` is clean.
- Every row of the requirements table in `CHANGES.md` names a positive and a negative step, and both ran.
- Each negative assertion exercises the deny path: the 401, the 429, the blocked request, the unattributed spend. A resource `Accepted` condition is never the assertion for a requirement; a policy can report `Accepted` while the behaviour is wrong.
- For a compose, the interaction check steps are present and pass.
- Every asynchronous step has a `retry` whose `until` is no stricter than its assertion.
- The baseline-integrity guard reports no drift, and the run prints no `LEFTOVER:` line.

Answer product questions during the loop with `skills/ask-the-docs/SKILL.md`.

## 5. Hand-off

Fill in "Changes during validation" in `CHANGES.md`: what changed and why. Build the report:

```bash
./e2e/run-e2e.sh --custom <name> --report-only
```

Show the three paths: `custom-labs/<name>/<name>.md`, `custom-labs/<name>/spec.yaml`, and the report under `custom-labs/<name>/reports/`. Tell the user they can run the lab by hand from the markdown, or re-run the proof with `./e2e/run-e2e.sh --custom <name>`. The folder is theirs and stays on their machine.

Validation ran with Cleanup, so the cluster now holds only the base. Ask whether to leave the custom configuration on the cluster. The default is no. On a yes:

```bash
./e2e/run-e2e.sh --custom <name> --keep
e2e/lib/session-log.sh add keep-custom custom/<name> "custom configuration kept at the user's request"
```

Log the outcome line of that run with `session-log.sh add run` as in step 4. Remind them that the lab's Cleanup section removes it later. The kept configuration depends on the base install, so do not offer base removal, and say why. If they did not keep it, ask the keep-or-remove question from step 7 of `skills/evaluate/SKILL.md`.

## Writing the lab

Follow `style-guide.md` and the stop-slop rules: zero em dashes, no filler, no meta-commentary.

- Clone the skeleton of the closest source lab: title, Pre-requisites boilerplate, Lab Objectives, steps, Observability, Cleanup. Each step is why-prose, a command block, then verification with `Expected output:`.
- Depend on labs 001 and 002 only. Deploy anything else the lab needs, including an in-cluster IdP, and remove it in Cleanup.
- Reference `lib/` assets and link to `labs/` with repo-root-relative paths. Put files the repo does not ship under `custom-labs/<name>/assets/` and reference them by that path.
- Keep headings unique within the lab, case-insensitively.
- Make every bash block executable. Never fence expected-output tables as bash.
- Write YAML in block style, never inline flow style.
- Cleanup deletes in reverse order with `--ignore-not-found`, restores any global state the lab changed, and removes everything deployed beyond 001 and 002.
- Read secrets from environment variables that the Pre-requisites section names. The lab never contains a credential, token, hostname, or IP from the user's cluster. Values the user supplied, such as team names, tiers, and model aliases, belong in the lab.
- Where the spec needs a probe for port-forwards or multi-step token flows, use `e2e_pf`/`e2e_pf_stop` and the shared-shell variables.

Before you write ordinals, print the headings and bash-block counts:

```bash
python3 e2e/lib/labdoc.py sections custom-labs/<name>/<name>.md
```

## Writing the spec

Use the spec DSL from `e2e/README.md`. Declare each requirement with `requirements:` and name it with `proves:` on every step that asserts it:

```yaml
lab: custom-labs/<name>/<name>.md
description: one line
tier: t1-key
requires:
  - OPENAI_API_KEY
timeout: 600

requirements:
  - id: R1
    text: "A request with no bearer token is rejected with 401"
  - id: R3
    text: "A gold token gets 429 on the request that exceeds its budget"

steps:
  - section: "Call the route without a token"
    proves:
      - R1
    retry:
      attempts: 8
      delay: 3
      until: "HTTP/[0-9.]+ 401"
    assert:
      - status: 401

cleanup:
  section: "Cleanup"
```

Lint fails when a requirement has no proving step, a proving step has no `assert`, a `proves` id is undeclared, or a step uses `run: none` without `reason:`. Give each requirement at least one positive and one negative step.

Put a `retry` on every step whose result depends on asynchronous work: route programming, policy attachment, backend discovery, rate-limit counters. Match `until` to what the last block prints, and keep it no stricter than the step's assertion.
