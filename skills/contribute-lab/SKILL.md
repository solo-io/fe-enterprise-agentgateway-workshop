---
name: contribute-lab
description: Promote a custom lab that passed adapt mode to a shipped lab, conform it to the style guide, prove it as a shipped spec, commit it on a new version branch, and open a PR only after the user approves the diff. Use only when the user runs /contribute-lab or asks to contribute a custom lab.
---

# Contribute a lab

Work from the repo root. Follow the steps in order. The four hard rules in `AGENTS.md` apply throughout. One session promotes one custom lab from `custom-labs/<name>/` into one PR on `solo-io/fe-enterprise-agentgateway-workshop`. It works from a clone of that repo, or from a clone of a fork with the main repo added as the `upstream` remote.

Run this only when the user asks for it. Adapt mode never calls it. The user's request covers the local commit in step 6. Nothing is pushed until the user says yes to the diff.

The user's folder `custom-labs/<name>/` is never edited. You scrub a staging copy, and the promoter copies from that staging copy into the repo. The folder stays re-runnable with `./e2e/run-e2e.sh --custom <name>`.

Session files live under `e2e/.work/contribute/<name>/`:

```
e2e/.work/contribute/<name>/
  stage/          the staging copy you scrub
  manifest.json   files the promoter created and repo files you recorded
  before/         copies of recorded repo files, for --undo
  lint.before     ./e2e/run-e2e.sh --lint output before the promote
  pr-body.md      the CHANGELOG bullets, used as the PR body
  staged.patch    the staged diff, scanned again before the commit
```

Contribute mode makes no call to the Solo docs server. The scrub list, the lab text, and the diff stay on the machine.

## 1. Preflight

Run these checks in order. Stop on the first failure with the message shown, and do nothing else.

1. The custom lab is complete and passed. `custom-labs/<name>/` holds `<name>.md`, `spec.yaml`, and `CHANGES.md`, and the newest report under `reports/` has the passing row `| custom/<name> | PASS |` and a passing row for every requirement:

   ```bash
   ls custom-labs/<name>/<name>.md custom-labs/<name>/spec.yaml custom-labs/<name>/CHANGES.md
   R=$(ls -t custom-labs/<name>/reports/*.md | head -1); echo "$R"
   grep -F "| custom/<name> | PASS |" "$R"
   awk '/^## Requirements/{on=1; next} /^## /{on=0} on && /^\| R[0-9]+ /' "$R"
   ```

   The `grep` must print the row, and every requirement row the `awk` prints must end in `| PASS |`. An older passing report does not count: the newest run is the one that proves the lab as it is now.

   Stop message: "custom-labs/<name>/ has no passing report. Run /adapt-lab to finish and validate it first."

2. No unfinished session exists. Check this before the working tree, because a half-promoted session leaves the tree dirty:

   ```bash
   ls e2e/.work/contribute/<name>/manifest.json
   ```

   If the file exists, a previous session stopped. Check whether its files are still uncommitted:

   ```bash
   jq -r '.created[], .recorded[].path' e2e/.work/contribute/<name>/manifest.json | tr '\n' '\0' | xargs -0 -r git status --porcelain --
   ```

   - The command prints nothing, or the manifest lists no paths: the previous session's work is already committed. Tell the user, and offer to retire the manifest. On a yes, run `mv e2e/.work/contribute/<name>/manifest.json e2e/.work/contribute/<name>/manifest.stale.json` and continue with check 3. On a no, stop the session and print the manifest path.
   - The command prints lines: those files are uncommitted. Show the manifest and offer the undo from step 8. On a yes, run the undo, then continue with check 3. On a no, stop the session and print the manifest path, `e2e/.work/contribute/<name>/manifest.json`, so the user can finish or revert by hand.

   Never start a new run on top of an old manifest.

3. `gh` is logged in, and the clone reaches the main repo directly or through a fork:

   ```bash
   gh auth status
   git remote get-url origin
   git remote get-url upstream    # fork clones only; it fails in a direct clone
   gh repo view solo-io/fe-enterprise-agentgateway-workshop --json defaultBranchRef -q .defaultBranchRef.name
   ```

   A URL names the main repo when it ends in `github.com/solo-io/fe-enterprise-agentgateway-workshop` or `github.com:solo-io/fe-enterprise-agentgateway-workshop`, with or without `.git`.

   - `gh auth status` fails. Stop message: "gh is not logged in to github.com. Run gh auth login, then start again."
   - `origin` names the main repo: direct mode. `<up>` is `origin`.
   - `origin` names another repo and `upstream` names the main repo: fork mode. `<up>` is `upstream`, and `<fork>` is the `owner/name` in the `origin` URL. Check that `origin` is a fork of the main repo:

     ```bash
     gh repo view <fork> --json parent -q .parent.nameWithOwner
     ```

     It must print `solo-io/fe-enterprise-agentgateway-workshop`. Stop message: "origin is <origin URL>, which is not a fork of solo-io/fe-enterprise-agentgateway-workshop. Contribute from a clone of the main repo or of your fork."
   - Neither. Stop message: "origin is <origin URL>. Contribute from a clone of solo-io/fe-enterprise-agentgateway-workshop, or from a clone of your fork with the main repo as upstream: git remote add upstream https://github.com/solo-io/fe-enterprise-agentgateway-workshop.git"
   - The last command prints anything other than `main`. Stop message: "solo-io/fe-enterprise-agentgateway-workshop reports default branch <printed value>, not main. Ask the repo owners which branch takes contributions."

   Wherever a step below names `<up>`, use the remote you found here.

4. The working tree is clean, the branch is `main` or a version branch, and it is level with the same branch on `<up>`:

   ```bash
   git status --porcelain
   git branch --show-current
   git fetch <up>
   git rev-list --left-right --count HEAD...<up>/<branch>
   ```

   - `git status --porcelain` prints anything. Stop message: "The working tree has uncommitted changes. Commit or stash them, then start again."
   - The branch is not `main` and does not match `^[0-9]+\.[0-9]+\.[0-9]+$`. Stop message: "The current branch is <branch>. Switch to main or a version branch, then start again."
   - The `rev-list` command fails or prints anything other than `0	0`. Stop message: "<branch> differs from <up>/<branch>, or <up> has no such branch. Start from a branch that matches the main repo, then start again."

   When the start branch is not `main`, update local `main` so check 5 and the step 7 diff read the current `main`:

   ```bash
   git fetch <up> main:main
   ```

   If that fetch fails, because `main` is checked out in another worktree or has diverged from `<up>/main`, tell the user that check 5 and the step 7 diff read a stale `main`, and continue. Also tell the user the PR will carry the start branch's commits, so they can merge that version first if they want a PR with only this lab.

5. A free version and branch name exist. Print the highest version among the start branch's CHANGELOG head, `main`'s CHANGELOG head, and every local and remote version branch:

   ```bash
   { grep -m1 -E '^[0-9]+\.[0-9]+\.[0-9]+ - \(' CHANGELOG.md
     git show main:CHANGELOG.md | grep -m1 -E '^[0-9]+\.[0-9]+\.[0-9]+ - \('
     git branch --list '[0-9]*.[0-9]*.[0-9]*'
     git ls-remote --heads <up> '[0-9]*.[0-9]*.[0-9]*'
     git ls-remote --heads origin '[0-9]*.[0-9]*.[0-9]*'
   } | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1
   ```

   Add one to the patch number of the printed version. That is the proposed `X.Y.Z`. These commands must print nothing for it. In direct mode the last two read the same remote:

   ```bash
   git branch --list X.Y.Z
   git ls-remote --heads <up> X.Y.Z
   git ls-remote --heads origin X.Y.Z
   ```

   If any prints a line, add one to the patch number again and repeat the two commands. Propose the result to the user, who confirms it or gives another name. Run the three commands on a name the user gives.

   - The chosen name exists locally or on `origin`. Stop message: "Branch X.Y.Z already exists locally, on <up>, or on origin. Choose a version that is not taken, then start again."

   Use the confirmed `X.Y.Z` for the CHANGELOG block in step 4, the branch in step 6, and the PR title in step 7.

6. The cluster is reachable and the base is installed, because step 5 runs the promoted spec:

   ```bash
   ./e2e/lib/detect-env.sh
   ```

   It must exit 0, and `existing_install` in `e2e/.work/env.json` must be non-empty. Stop message: "The cluster is unreachable or the base install is missing. Run /evaluate or ./e2e/run-e2e.sh --install-base --only-base first."

Record the lint baseline, so step 4 can tell your lint lines from ones that were there before:

```bash
mkdir -p e2e/.work/contribute/<name>
./e2e/run-e2e.sh --lint > e2e/.work/contribute/<name>/lint.before 2>&1
```

Start a fresh session log and record the start:

```bash
e2e/lib/session-log.sh start
e2e/lib/session-log.sh add decision custom/<name> "contribute started: <direct|fork> mode"
```

## 2. Scrub

Make the staging copy:

```bash
rm -rf e2e/.work/contribute/<name>/stage && cp -R custom-labs/<name> e2e/.work/contribute/<name>/stage
```

Run the scanner on the lab, spec, CHANGES.md, and assets in the staging copy. Leave out the `assets` path when the lab has no assets:

```bash
S=e2e/.work/contribute/<name>/stage
python3 e2e/lib/scrub.py --summary --from-changes $S/CHANGES.md $S/<name>.md $S/spec.yaml $S/CHANGES.md $S/assets
```

Each line is `<count>\t<rule>\t<match>\t<first location>`. The rules are `ipv4`, `ipv6`, `hostname`, `email`, `bearer`, `secret-prefix`, `base64-blob`, `cloud-id`, and `user-value`. Drop `--summary` to see every location of a match.

Each distinct match gets one ruling. Show the matches grouped by rule and go through one rule at a time. For every rule except `user-value`, the user may give one ruling for the whole group. For `user-value`, the user rules on each distinct value. The rulings are:

- Keep: the value is fine to publish.
- Replace: give the generic value to use, for example `example.com` for a tenant host or `team-a` for a team name.
- Stop: end the session. Nothing has been promoted yet, so the repo is unchanged. Leave `stage/` in place, log the stop, and end:

  ```bash
  e2e/lib/session-log.sh add decision custom/<name> "contribute stopped: scrub"
  ```

The `user-value` group lists the lab's own vocabulary from the `CHANGES.md` tables: tier names, user names, resource names, model aliases. Many are fine to keep.

Log every ruling:

```bash
e2e/lib/session-log.sh add decision custom/<name> "scrub: <rule> <match> -> <keep|replaced>"
```

After every ruling is in, apply the replacements to the files under `e2e/.work/contribute/<name>/stage/` with your editor. Replace a value everywhere it appears in the lab, the spec, and the assets, so the spec still matches the lab. Never touch `custom-labs/<name>/`.

Run the scanner again. Add `--allow <glob>` for a host the user ruled as fine to keep, so it drops out of the list:

```bash
S=e2e/.work/contribute/<name>/stage
python3 e2e/lib/scrub.py --summary --allow <glob> --from-changes $S/CHANGES.md $S/<name>.md $S/spec.yaml $S/CHANGES.md $S/assets
```

Every remaining line must be a match the user ruled as keep. A new hit gets a ruling like the others. Nothing is scrubbed without a ruling.

## 3. Promote

List the categories and propose one from the lab's objectives. The user confirms. The lab name must not exist yet as `labs/<category>/<name>.md` or `labs/<category>/<name>/`; the promoter refuses a name that does:

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

Show the table of files and rewrites. A `warning: <path> is gitignored and will not be committed` line means the commit in step 6 would leave that file out: agree a different file name or destination with the user and preview again. If the promoter reports that a file still references `custom-labs/`, edit that file under `stage/` and preview again.

On the user's yes, run it for real with the same arguments:

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

Work through `style-guide.md` §15 and its "Checklist when adding a lab". Change the lab only to conform it; never redesign its content. Edit only the promoted files and the files you recorded. Check the lab's prose, the CHANGELOG bullets, and `pr-body.md` against `skills/stop-slop/SKILL.md`.

- README: add a bullet under the category's `## <Category>` section in the form `- [Lab Title](labs/<category>/<name>.md) _(Provider)_`, with `labs/<category>/<name>/README.md` as the link target in subdir mode. When the lab spans categories, list it under each one with a reciprocal `_(see also: <Other Categories>)_`. Add a capability bullet under `# Use Cases`.
- tracks: add a `| Lab | What you'll learn |` row to a `### Use Case` table in `tracks/llm-track.md` or `tracks/mcp-track.md` only when the lab fits an existing use case. Otherwise skip it and tell the user you skipped it.
- `labs/installation/image-list.md`: add a block for any new chart-managed image. For an image the lab deploys itself, such as Keycloak, do what the existing labs do for the same image.
- The lab's Pre-requisites name `001` and `002` in the boilerplate sentence and name every environment variable the lab reads.
- Assets: read each copied asset for comments that mention `assets/` or the custom lab, and change them to name the asset's new path.
- CHANGELOG: add a block for the `X.Y.Z` confirmed in preflight check 5 at the top of `CHANGELOG.md`, dated today in `M-D-YY` form. The first bullet names the source labs the lab was derived from. Bullets lead with `Add` and name files in backticks:

  ```markdown
  X.Y.Z - (M-D-YY)
  ---
  - Add `labs/<category>/<name>.md`: <what the lab shows>, derived from `labs/<category>/<source-lab>.md` and `labs/<category>/<source-lab>.md`
  - Add `e2e/specs/<category>/<name>.yaml`: <the requirements the spec proves>
  - Add `lib/<concern>/<asset>`: <what the asset is>
  ```

  In subdir mode the first bullet names `labs/<category>/<name>/README.md`.

  Write the block's bullets to `e2e/.work/contribute/<name>/pr-body.md`, without the `X.Y.Z - (M-D-YY)` and `---` lines. The PR title carries the version.

Then lint:

```bash
./e2e/run-e2e.sh --lint
```

It must print nothing beyond the lines in `e2e/.work/contribute/<name>/lint.before`, including the conventions and link checks for the new README entries. Fix your own conformance edits until it holds.

## 5. Verify as a shipped lab

Run the promoted spec:

```bash
./e2e/run-e2e.sh <category>/<name>
```

Read the outcome line from `e2e/.work/<category>/<name>/outcome`. Only `status=pass` with `rc=0`, `leftover_warn=0`, and none of the fields `cleanup_fail=`, `drift=`, or `leftover_check=` counts. Log it:

```bash
e2e/lib/session-log.sh add run <category>/<name> "<outcome line>"
```

A failure here comes from the move: a path or link the promoter missed, a value you replaced in step 2 that the spec and lab no longer share, or a heading you changed in step 4. Fix that in the promoted files. Never edit an assertion or a requirement. If the cause is anything else, apply `skills/diagnose-failure/SKILL.md` with the key `<category>/<name>`, using its "In contribute mode" choices: retry step 5 as written, or stop and go to step 8. Run only the shipped key `<category>/<name>` here, never `./e2e/run-e2e.sh --custom <name>`.

For a shipped lab, the runner prints each object the lab left behind as a `LEFTOVER (warning):` line and counts them in `leftover_warn=`. Evaluate only reports these, but a promoted lab must leave nothing: a `leftover_warn` above 0 is a Cleanup bug, and `leftover_check=not-run` means the check did not run, so run step 5 again. Show the lines to the user and fix the lab's Cleanup section.

## 6. Branch and commit

Only now does git change. Stage every path in the manifest's `created` and `recorded` lists, then scan the staged diff and the PR body once more. Pass the same `--allow` flags as the last step 2 scan, or none if it had none:

```bash
jq -r '.created[], .recorded[].path' e2e/.work/contribute/<name>/manifest.json | tr '\n' '\0' | xargs -0 -r git add --
git diff --cached > e2e/.work/contribute/<name>/staged.patch
python3 e2e/lib/scrub.py --summary --allow <glob> --from-changes e2e/.work/contribute/<name>/stage/CHANGES.md e2e/.work/contribute/<name>/staged.patch e2e/.work/contribute/<name>/pr-body.md
```

Every line must be a match the user ruled keep in step 2. A new hit gets a ruling before the commit. For a replace, replace the value in every promoted or recorded file it appears in, so the spec still matches the lab, run step 5 again, run the three commands again, and repeat until every line is a keep. For a stop, unstage with `git reset -q`, which leaves the files in place, and go to step 8.

Create the branch and commit with a plain message and no trailer. One commit:

```bash
git checkout -b X.Y.Z
git commit -m "see CHANGELOG (X.Y.Z)"
```

Right after the commit succeeds, retire the manifest so a later session never offers to undo committed work:

```bash
mv e2e/.work/contribute/<name>/manifest.json e2e/.work/contribute/<name>/manifest.committed.json
```

Then run `git status --porcelain`. If it prints anything, stop and show the output: a file the session wrote is missing from the commit. The commit is unpushed, so you may add the file and run `git commit --amend --no-edit`, but only after the user says yes.

## 7. Show the diff and stop

Show this lab's commit in full:

```bash
git --no-pager show --stat HEAD
git --no-pager show HEAD
```

Then show the PR scope, every file the PR changes against `main`, and the number of commits it carries:

```bash
git --no-pager diff --stat main...X.Y.Z
git rev-list --count main..X.Y.Z
```

A count above 1 means the PR also carries the start branch's commits; say so. State the PR title `see CHANGELOG (X.Y.Z)` and show the body from `e2e/.work/contribute/<name>/pr-body.md`. Ask one question: push this branch and open the PR, yes or no.

Nothing is pushed until the user says yes to the diff. Any answer other than yes ends the session. The branch stays local. Print the branch name and the `git push` and `gh pr create` commands below so the user can push later by hand.

On yes, push the branch to `origin`, which is the main repo in direct mode and the fork in fork mode, and open the PR on the main repo. `<head>` is `X.Y.Z` in direct mode and `<fork owner>:X.Y.Z` in fork mode. Then log the URL:

```bash
git push -u origin X.Y.Z
gh pr create --repo solo-io/fe-enterprise-agentgateway-workshop --base main --head <head> --title "see CHANGELOG (X.Y.Z)" --body-file e2e/.work/contribute/<name>/pr-body.md
e2e/lib/session-log.sh add decision custom/<name> "PR opened: <url>"
```

Print the PR URL. Another contributor can take the same `X.Y.Z` before this PR merges; the reviewer then renumbers the version at merge. Tell the user that `custom-labs/<name>/` is still theirs, and that the shipped lab is now the source of truth for the repo. Never force-push, and never amend a pushed commit.

## 8. Stopping partway

The session can stop after step 3 and before the commit in step 6: a step 5 failure you may not fix, a stop ruling at the step 6 scan, or the user quitting. Each leaves uncommitted files on the start branch. Never discard them on your own. Show the manifest:

```bash
cat e2e/.work/contribute/<name>/manifest.json
```

Offer one undo, and run it only on a yes:

```bash
python3 e2e/lib/promote.py <name> --undo
```

Undo deletes every file the promoter created and restores every recorded file from `before/`, then prints what it did. If it ends with `undo incomplete; manifest kept`, show the user the lines it printed for the entries that failed. The manifest stays, so the user can fix those entries and run the undo again. A line starting `skip` means a recorded file had no saved copy, so undo left it as it is; tell the user to check that file by hand. `custom-labs/<name>/` is unchanged either way. On a no, the files stay; print the manifest path so the user can finish or revert by hand.

```bash
e2e/lib/session-log.sh add decision custom/<name> "contribute stopped: <undo|kept>"
```
