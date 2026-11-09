---
name: contribute-lab
description: Promote a custom lab that passed adapt mode to a shipped lab, conform it to the style guide, prove it as a shipped spec, commit it on a new version branch, and open a PR only after the user approves the diff. Use only when the user runs /contribute-lab or asks to contribute a custom lab.
---

# Contribute a lab

Work from the repo root. Follow the steps in order. The four hard rules in `AGENTS.md` apply throughout. One session promotes one custom lab from `custom-labs/<name>/` into one PR on `solo-io/fe-enterprise-agentgateway-workshop`.

Run this only when the user asks for it. Adapt mode never calls it. The user's request covers the local commit in step 6. Nothing is pushed until the user says yes to the diff.

The user's folder `custom-labs/<name>/` is never edited. You scrub a staging copy, and the promoter copies from that staging copy into the repo. The folder stays re-runnable with `./e2e/run-e2e.sh --custom <name>`.

Session files live under `e2e/.work/contribute/<name>/`:

```
e2e/.work/contribute/<name>/
  stage/          the staging copy you scrub
  manifest.json   files the promoter created and repo files you recorded
  before/         copies of recorded repo files, for --undo
  pr-body.md      the CHANGELOG block, used as the PR body
```

Contribute mode makes no call to the Solo docs server. The scrub list, the lab text, and the diff stay on the machine.

## 1. Preflight

Run these checks in order. Stop on the first failure with the message shown, and do nothing else.

1. The custom lab is complete and passed. `custom-labs/<name>/` holds `<name>.md`, `spec.yaml`, `CHANGES.md`, and at least one report under `reports/` whose custom lab table has the single row `PASS`:

   ```bash
   ls custom-labs/<name>/<name>.md custom-labs/<name>/spec.yaml custom-labs/<name>/CHANGES.md
   grep -l "| custom/<name> | PASS |" custom-labs/<name>/reports/*.md
   ```

   Stop message: "custom-labs/<name>/ has no passing report. Run /adapt-lab to finish and validate it first."

2. No unfinished session exists. Check this before the working tree, because a half-promoted session leaves the tree dirty:

   ```bash
   ls e2e/.work/contribute/<name>/manifest.json
   ```

   If the file exists, a previous session stopped partway. Show the manifest and offer the undo from step 8. On a yes, run the undo, then continue with check 3. On a no, stop the session and print the manifest path, `e2e/.work/contribute/<name>/manifest.json`, so the user can finish or revert by hand. Never start a new run on top of an old manifest.

3. `gh` is logged in and points at the main repo with default branch `main`:

   ```bash
   gh auth status
   gh repo view --json nameWithOwner,defaultBranchRef -q '.nameWithOwner + " " + .defaultBranchRef.name'
   ```

   - `gh auth status` fails. Stop message: "gh is not logged in to github.com. Run gh auth login, then start again."
   - The second command prints anything other than `solo-io/fe-enterprise-agentgateway-workshop main`. Stop message: "gh resolves this checkout to <printed value>, not solo-io/fe-enterprise-agentgateway-workshop with default branch main. Contribute from a clone of the main repo."

4. The working tree is clean, the branch is `main` or a version branch, and it is level with its remote:

   ```bash
   git status --porcelain
   git branch --show-current
   git fetch origin && git status -sb
   ```

   - `git status --porcelain` prints anything. Stop message: "The working tree has uncommitted changes. Commit or stash them, then start again."
   - The branch is not `main` and does not match `^[0-9]+\.[0-9]+\.[0-9]+$`. Stop message: "The current branch is <branch>. Switch to main or a version branch, then start again."
   - The first line of `git status -sb` shows `ahead` or `behind`. Stop message: "<branch> differs from origin/<branch>. Pull or push it so the two match, then start again."

   When the start branch is not `main`, update local `main` so the step 7 diff is current:

   ```bash
   git fetch origin main:main
   ```

   If that fetch fails, because `main` is checked out in another worktree or has diverged from `origin/main`, tell the user the step 7 diff may be stale and continue. Also tell the user the PR will carry the start branch's commits, so they can merge that version first if they want a PR with only this lab.

5. The cluster is reachable and the base is installed, because step 5 runs the promoted spec:

   ```bash
   ./e2e/lib/detect-env.sh
   ```

   It must exit 0, and `existing_install` in `e2e/.work/env.json` must be non-empty. Stop message: "The cluster is unreachable or the base install is missing. Run /evaluate or ./e2e/run-e2e.sh --install-base --only-base first."

Start a fresh session log and record the start:

```bash
e2e/lib/session-log.sh start
e2e/lib/session-log.sh add decision custom/<name> "contribute started"
```

## 2. Scrub

Make the staging copy:

```bash
mkdir -p e2e/.work/contribute/<name> && rm -rf e2e/.work/contribute/<name>/stage && cp -R custom-labs/<name> e2e/.work/contribute/<name>/stage
```

Run the scanner on the staging copy:

```bash
python3 e2e/lib/scrub.py --summary --from-changes e2e/.work/contribute/<name>/stage/CHANGES.md e2e/.work/contribute/<name>/stage
```

Each line is `<count>\t<rule>\t<match>\t<first location>`. The rules are `ipv4`, `ipv6`, `hostname`, `email`, `bearer`, `secret-prefix`, `base64-blob`, `cloud-id`, and `user-value`. Drop `--summary` to see every location of a match.

Show the list grouped by rule. Ask the user for a ruling on one group at a time:

- Keep: the value is fine to publish.
- Replace: give the generic value to use, for example `example.com` for a tenant host or `team-a` for a team name.
- Stop: end the session. Nothing has been promoted yet, so the repo is unchanged. Leave `stage/` in place, log the stop, and end:

  ```bash
  e2e/lib/session-log.sh add decision custom/<name> "contribute stopped: scrub"
  ```

The `user-value` group lists the lab's own vocabulary: tier names, user names, resource names, model aliases. Many are fine to keep. The user rules on each one.

Log every ruling:

```bash
e2e/lib/session-log.sh add decision custom/<name> "scrub: <rule> <match> -> <keep|replaced>"
```

After every ruling is in, apply the replacements to the files under `e2e/.work/contribute/<name>/stage/` with your editor. Replace a value everywhere it appears in the lab, the spec, and the assets, so the spec still matches the lab. Never touch `custom-labs/<name>/`.

Run the scanner again. Add `--allow <glob>` for a host the user ruled as fine to keep, so it drops out of the list:

```bash
python3 e2e/lib/scrub.py --summary --allow <glob> --from-changes e2e/.work/contribute/<name>/stage/CHANGES.md e2e/.work/contribute/<name>/stage
```

Every remaining line must be a match the user ruled as keep. A new hit gets a ruling like the others. Nothing is scrubbed without a ruling.

## 3. Promote

List the categories and propose one from the lab's objectives. The user confirms:

```bash
ls labs
```

Read the files under `e2e/.work/contribute/<name>/stage/assets/` and propose where each goes. The user confirms the split. A lab with no assets needs no `--lib` or `--subdir`.

- A file reusable across labs, such as an IdP realm, a deployment manifest, or a claims file, goes to `lib/<concern>/` with `--lib <asset>=<concern>`. Use an existing concern directory when one fits (`ls lib`), else a new one the user names. When the concern directory already holds shared files, put a set specific to one realm or scenario in its own subdirectory, for example `--lib realm.json=keycloak/llm-tiers`, as `lib/keycloak/mcp-enterprise/` does next to `lib/keycloak/deploy.yaml`. Prefer that over a flat file.
- A file specific to this lab goes to `labs/<category>/<name>/`. Pass `--subdir`, and the runbook becomes `labs/<category>/<name>/README.md`, a subdirectory lab per `style-guide.md` §4.

Preview first:

```bash
python3 e2e/lib/promote.py <name> --category <category> --lib <asset>=<concern> --dry-run
```

Show the table of files and rewrites. On the user's yes, run it for real with the same arguments:

```bash
python3 e2e/lib/promote.py <name> --category <category> --lib <asset>=<concern>
```

The promoter copies the lab to `labs/<category>/<name>.md`, the spec to `e2e/specs/<category>/<name>.yaml` with its `lab:` line rewritten, and the assets where you named them. It rewrites every `custom-labs/<name>/assets/` path in the lab and recomputes relative links. It writes `e2e/.work/contribute/<name>/manifest.json`. Everything stays uncommitted in the working tree.

Before you edit any existing repo file in step 4, record it so `--undo` can restore it. Record `README.md` and `CHANGELOG.md` always, and a tracks file or `labs/installation/image-list.md` only if you will edit it:

```bash
python3 e2e/lib/promote.py <name> --record README.md
python3 e2e/lib/promote.py <name> --record CHANGELOG.md
python3 e2e/lib/promote.py <name> --record tracks/llm-track.md               # only when you will edit it
python3 e2e/lib/promote.py <name> --record labs/installation/image-list.md   # only when you will edit it
```

## 4. Conform

Work through `style-guide.md` §15 and its "Checklist when adding a lab". Change the lab only to conform it; never redesign its content.

- README: add a bullet under the category's `## <Category>` section in the form `- [Lab Title](labs/<category>/<name>.md) _(Provider)_`. When the lab spans categories, list it under each one with a reciprocal `_(see also: <Other Categories>)_`. Add a capability bullet under `# Use Cases`.
- tracks: add a `| Lab | What you'll learn |` row to a `### Use Case` table in `tracks/llm-track.md` or `tracks/mcp-track.md` only when the lab fits an existing use case. Otherwise skip it and tell the user you skipped it.
- `labs/installation/image-list.md`: add a block for any new chart-managed image. For an image the lab deploys itself, such as Keycloak, do what the existing labs do for the same image.
- The lab's Pre-requisites name `001` and `002` in the boilerplate sentence and name every environment variable the lab reads.
- CHANGELOG: propose the version and confirm it with the user. Take the first `X.Y.Z - (` line of `CHANGELOG.md` on the start branch and add one to the patch number:

  ```bash
  grep -m1 -E '^[0-9]+\.[0-9]+\.[0-9]+ - \(' CHANGELOG.md
  ```

  The branch for that version must not exist locally or on the remote. Both commands must print nothing:

  ```bash
  git branch --list X.Y.Z
  git ls-remote --heads origin X.Y.Z
  ```

  Add a block at the top of `CHANGELOG.md`, dated today in `M-D-YY` form. The first bullet names the source labs the lab was derived from. Bullets lead with `Add` and name files in backticks:

  ```markdown
  X.Y.Z - (M-D-YY)
  ---
  - Add `labs/<category>/<name>.md`: <what the lab shows>, derived from `labs/<category>/<source-lab>.md` and `labs/<category>/<source-lab>.md`
  - Add `e2e/specs/<category>/<name>.yaml`: <the requirements the spec proves>
  - Add `lib/<concern>/<asset>`: <what the asset is>
  ```

  Write the same block, verbatim, to `e2e/.work/contribute/<name>/pr-body.md`.

Then lint:

```bash
./e2e/run-e2e.sh --lint
```

The lint must be clean, including the conventions and link checks for the new README entries. Fix your own conformance edits until it is.

## 5. Verify as a shipped lab

Run the promoted spec:

```bash
./e2e/run-e2e.sh <category>/<name>
```

Read the outcome line from `e2e/.work/<category>/<name>/outcome`. Only `status=pass` counts. Log it:

```bash
e2e/lib/session-log.sh add run <category>/<name> "<outcome line>"
```

A failure here comes from the move: a path or link the promoter missed, or a heading you changed in step 4. Fix that in the promoted files. Never edit an assertion or a requirement. If the cause is anything else, apply `skills/diagnose-failure/SKILL.md` with the key `<category>/<name>` and go to step 8 if the user stops.

The shipped runner has no leftover check, so check by hand. For each `kubectl delete namespace <ns>` line in the lab's Cleanup section, run:

```bash
kubectl get namespace <ns>
```

Each must return `NotFound`. A namespace that still exists is a Cleanup bug: show it to the user and fix the Cleanup section.

## 6. Branch and commit

Only now does git change. Stage every file in the manifest's `created` and `recorded` lists, then commit with a plain message and no trailer:

```bash
git checkout -b X.Y.Z
git add <every file the promoter and conform steps touched>
git commit -m "see CHANGELOG (X.Y.Z)"
```

Check that `git status --porcelain` prints nothing afterward. One commit.

Right after the commit succeeds, retire the manifest so a later session never offers to undo committed work:

```bash
mv e2e/.work/contribute/<name>/manifest.json e2e/.work/contribute/<name>/manifest.committed.json
```

## 7. Show the diff and stop

```bash
git --no-pager diff --stat main..X.Y.Z
git --no-pager diff main..X.Y.Z
```

Show both in full. State the PR title `see CHANGELOG (X.Y.Z)` and show the body from `e2e/.work/contribute/<name>/pr-body.md`. Ask one question: push this branch and open the PR, yes or no.

Nothing is pushed until the user says yes to the diff. Any answer other than yes ends the session. The branch stays local. Print the branch name and the `git push` and `gh pr create` commands below so the user can push later by hand.

On yes, push and open the PR, then log the URL:

```bash
git push -u origin X.Y.Z
gh pr create --base main --head X.Y.Z --title "see CHANGELOG (X.Y.Z)" --body-file e2e/.work/contribute/<name>/pr-body.md
e2e/lib/session-log.sh add decision custom/<name> "PR opened: <url>"
```

Print the PR URL. Tell the user that `custom-labs/<name>/` is still theirs, and that the shipped lab is now the source of truth for the repo. Never force-push, and never amend a pushed commit.

## 8. Stopping partway

After step 3 and before step 6, a step 5 failure you may not fix or the user quitting leaves uncommitted files on the start branch. Never discard them on your own. Show the manifest:

```bash
cat e2e/.work/contribute/<name>/manifest.json
```

Offer one undo, and run it only on a yes:

```bash
python3 e2e/lib/promote.py <name> --undo
```

Undo deletes every file the promoter created and restores every recorded file from `before/`, then prints what it did. `custom-labs/<name>/` is unchanged either way. On a no, the files stay; print the manifest path so the user can finish or revert by hand.

```bash
e2e/lib/session-log.sh add decision custom/<name> "contribute stopped: <undo|kept>"
```
