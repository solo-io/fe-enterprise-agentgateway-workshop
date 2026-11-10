#!/usr/bin/env python3
"""report.py: build the evaluation report from a run's outcome files.

Usage:
  report.py build --work DIR --track <llm|mcp|adhoc> --env-json PATH [--env-class C]
                  --session PATH --out DIR [--specs-dir D] [--tracks-dir T]
      writes <out>/<YYYYMMDD-HHMMSS>-<class>-<track>.md and .json (UTC; -1, -2
      appended when that name exists), prints both paths. <class> is --env-class
      when given, else the env json's class
  report.py build --work DIR --custom <name> --env-json PATH --session PATH --out DIR
                  [--custom-root R]
      the single-row report for custom-labs/<name> (key custom/<name>), written as
      <stamp>-<class>-custom-<name>.md and .json; adds a Runs section. Reads
      custom-labs/<name>/.work/run/leftovers.txt and only session events for that lab
      or for no lab. The spec is <custom-root>/<name>/spec.yaml (default
      $E2E_CUSTOM_ROOT, else custom-labs/ at the repo root)

  Any lab whose spec declares `requirements:` gets rows in a Requirements
  section, one per requirement id: `| <id> | <text> | PASS |` (or FAIL, SKIP).
  A requirement passes when every step that `proves:` it ran and failed no
  check; the driver records those steps in <work>/<key>/steps.
  `LEFTOVER (warning):` lines in the leftovers file go under Leftover warnings,
  not Leftovers.
  report.py compare A.json B.json
      one line per lab whose result differs, after a line naming both environments;
      exits 2 when either file cannot be read

Reads only local files. The one external command is
`helm list -n agentgateway-system -o json`, for the chart version.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

import yaml

import tracks

NO_LEFTOVERS = "None. The cluster matches the baseline."
NO_LEFTOVERS_BUT_WARNINGS = "None that fail a lab. The objects under Leftover warnings are still on the cluster."
WARN_PREFIX = "LEFTOVER (warning): "
DEFAULT_CUSTOM_ROOT = os.environ.get("E2E_CUSTOM_ROOT") or os.path.join(tracks.REPO_ROOT, "custom-labs")


def parse_outcome(path):
    """Parse 'status=pass rc=0 duration=3 requires=A,B [only_on=x]' into a dict, or None."""
    try:
        with open(path, encoding="utf-8") as fh:
            line = fh.read().strip()
    except OSError:
        return None
    out = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out  # no "status" key means a malformed line; lab_row reports it


def read_session(path):
    events = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except ValueError:
                    continue
    except OSError:
        pass
    return events


def spec_reason(specs_dir, key):
    try:
        with open(os.path.join(specs_dir, key + ".yaml"), encoding="utf-8") as fh:
            spec = yaml.safe_load(fh) or {}
    except OSError:
        return ""
    return " ".join(str(spec.get("reason") or "").split())


def spec_path(key, specs_dir, custom_root):
    """custom/<name> -> <custom_root>/<name>/spec.yaml; anything else -> <specs_dir>/<key>.yaml."""
    if key.startswith("custom/"):
        return os.path.join(custom_root, key.split("/", 1)[1], "spec.yaml")
    return os.path.join(specs_dir, key + ".yaml")


def spec_requirements(path):
    """([{id, text}], {id: number of steps that prove it}) from a spec, or ([], {})."""
    try:
        with open(path, encoding="utf-8") as fh:
            spec = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return [], {}
    if not isinstance(spec, dict):
        return [], {}
    reqs = [{"id": str(r["id"]), "text": " ".join(str(r.get("text") or "").split())}
            for r in spec.get("requirements") or [] if isinstance(r, dict) and r.get("id")]
    expected = {}
    for step in spec.get("steps") or []:
        if isinstance(step, dict):
            for rid in step.get("proves") or []:
                expected[str(rid)] = expected.get(str(rid), 0) + 1
    return reqs, expected


def read_steps(path):
    """The driver's per-step records: [(step, status, requirement id)], or None with no file.

    Each line is `step=<n> status=<pass|fail|skip> req=<id>`; req= runs to the end
    of the line, so an id may hold spaces.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    except OSError:
        return None
    out = []
    for ln in lines:
        head, sep, rid = ln.partition(" req=")
        fields = dict(t.split("=", 1) for t in head.split() if "=" in t)
        if sep and "step" in fields and "status" in fields:
            out.append((fields["step"], fields["status"], rid))
    return out


def requirement_rows(key, outcome, row, reqs, expected, steps):
    """One {lab, id, text, result, detail} per requirement. result is PASS, FAIL or SKIP.

    The lab's own row decides labs that did not run: SKIP for a skipped, excluded
    or not-applicable lab, FAIL for one with no outcome. For a lab that ran, each
    requirement is judged on the steps that prove it.
    """
    status = (outcome or {}).get("status", "")
    rows = []
    for r in reqs:
        rid, result, detail = r["id"], "FAIL", ""
        if status not in RAN:
            if row["result"].startswith(("SKIP", "NOT AUTOMATED", "NOT APPLICABLE")):
                result, detail = "SKIP", row["result"].lower()
            else:
                detail = row["note"] or "lab did not run"
        elif steps is None:
            detail = "no step results recorded"
        else:
            mine = [(n, st) for n, st, x in steps if x == rid]
            failed = [n for n, st in mine if st == "fail"]
            want = expected.get(rid, 0)
            if failed:
                detail = "failed in step " + ", ".join(failed)
            elif len(mine) < want or not want:
                detail = (f"{want - len(mine)} of {want} proving step(s) did not run" if want
                          else "no step proves it")
            elif all(st == "skip" for _, st in mine):
                result, detail = "SKIP", "every proving step was skipped"
            else:
                result = "PASS"
        rows.append({"lab": key, "id": rid, "text": r["text"], "result": result, "detail": detail})
    return rows


def fmt_duration(value):
    try:
        secs = int(float(value))
    except (TypeError, ValueError):
        return "-"
    if secs < 0:
        return "-"
    mins, s = divmod(secs, 60)
    return f"{mins}m{s:02d}s" if mins else f"{s}s"


RAN = ("pass", "fail", "timeout", "interrupted")


def lab_row(key, outcome, deviations, specs_dir, plan_status="", stopped=False):
    """Map one outcome to the row dict {key, result, duration, note}."""
    if outcome is None:
        # No outcome file. The track entry says whether that is expected.
        if plan_status == "no-spec":
            return {"key": key, "result": "NOT AUTOMATED", "duration": "-", "note": "no spec for this lab"}
        if plan_status.startswith("not-applicable:"):
            return {"key": key, "result": f"NOT APPLICABLE (only on {plan_status.split(':', 1)[1]})",
                    "duration": "-", "note": ""}
        if stopped:
            return {"key": key, "result": "FAIL", "duration": "-", "note": "not run: session stopped"}
        return {"key": key, "result": "FAIL", "duration": "-", "note": "no outcome recorded"}
    if "status" not in outcome:
        return {"key": key, "result": "FAIL", "duration": "-", "note": "malformed outcome"}
    status = outcome.get("status", "")
    dur = fmt_duration(outcome.get("duration")) if status in RAN else "-"
    result, note = "FAIL", status
    if status == "pass":
        if deviations.get(key):
            result, note = "PASS (deviation)", deviations[key][0]
        else:
            result, note = "PASS", ""
    elif status == "fail":
        result, note = "FAIL", ""
    elif status == "timeout":
        result, note = "FAIL", "timeout"
    elif status == "interrupted":
        result, note = "FAIL", "interrupted"
    elif status == "excluded":
        result, note = "NOT AUTOMATED", spec_reason(specs_dir, key)
    elif status == "skip":
        req = outcome.get("requires", "")
        if outcome.get("reason") == "declined":
            result, note = "SKIP (declined)", ""
        else:
            result, note = f"SKIP (missing {req})" if req else "SKIP", ""
    elif status == "no-spec":
        result, note = "NOT AUTOMATED", "no spec for this lab"
    elif status == "not-applicable":
        result, note = f"NOT APPLICABLE (only on {outcome.get('only_on', '?')})", ""
    else:
        result, note = "FAIL", f"unknown status {status}"
    if outcome.get("drift") == "1":
        note = f"{note}; baseline drift" if note else "baseline drift"
    cleanup = outcome.get("cleanup_fail", "")
    if cleanup and cleanup != "0":
        extra = "cleanup did not finish" if cleanup == "incomplete" else "cleanup block failed"
        note = f"{note}; {extra}" if note else extra
    if outcome.get("keep") == "1":
        note = f"{note}; kept on the cluster" if note else "kept on the cluster"
    if outcome.get("leftover_check") == "not-run":
        note = f"{note}; leftover check not run" if note else "leftover check not run"
    leftover = outcome.get("leftover", "")
    if leftover and leftover != "0":
        note = f"{note}; {leftover} leftover(s)" if note else f"{leftover} leftover(s)"
    warn = outcome.get("leftover_warn", "")
    if warn and warn != "0":
        note = f"{note}; {warn} leftover warning(s)" if note else f"{warn} leftover warning(s)"
    return {"key": key, "result": result, "duration": dur, "note": note}


def chart_version():
    override = os.environ.get("E2E_CHART_VERSION")
    if override:
        return override
    try:
        proc = subprocess.run(["helm", "list", "-n", "agentgateway-system", "-o", "json"],
                              capture_output=True, text=True, timeout=30)
        releases = json.loads(proc.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown"
    for rel in releases:
        chart = rel.get("chart", "")
        if chart.startswith("enterprise-agentgateway-") and not chart.startswith("enterprise-agentgateway-crds"):
            return chart[len("enterprise-agentgateway-"):] or rel.get("app_version") or "unknown"
    return "unknown"


def adhoc_use_cases(work):
    keys = []
    for root, _dirs, files in os.walk(work):
        if "outcome" in files:
            key = os.path.relpath(root, work)
            # Skip --uninstall-base's own steps and custom labs (author runs).
            if not key.startswith(("_base" + os.sep, "custom" + os.sep)):
                keys.append(key)
    return [{"name": "Ad hoc run", "keys": sorted(keys)}], {}


def track_use_cases(track, env_class, specs_dir, tracks_dir):
    path = tracks.resolve_track(tracks_dir, track)
    data = tracks.load_track(path)
    statuses = {}
    for entry in tracks.plan(data, env_class, specs_dir):
        if entry.status != "duplicate":
            statuses[entry.key] = entry.status
    return [{"name": uc["name"], "keys": [lab["spec"] for lab in uc.get("labs") or []]}
            for uc in data.get("use_cases") or []], statuses


def key_dir(work, key, custom_root=None):
    """A lab's work directory, as run-e2e.sh key_dir writes it. custom/<name> lives in
    the custom lab's own folder, <custom root>/<name>/.work/run; a custom lab whose
    files are still at <work>/custom/<name> (written before the move) is read there."""
    if key.startswith("custom/"):
        own = os.path.join(custom_root or DEFAULT_CUSTOM_ROOT, key[len("custom/"):], ".work", "run")
        if os.path.isdir(own) or not os.path.isdir(os.path.join(work, key)):
            return own
    return os.path.join(work, key)


def build_data(work, track, env_json, session, specs_dir, tracks_dir, custom=None, env_class=None,
               custom_root=None):
    try:
        with open(env_json, encoding="utf-8") as fh:
            env = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"report.py: cannot read env json {env_json}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    # The class the run planned with (run-e2e.sh --env) wins over the detected
    # one, so the title, the file name and the NOT APPLICABLE rows match the run.
    if env_class and env_class != env.get("class"):
        env = dict(env)
        if "class" in env:
            env["detected_class"] = env["class"]
        env["class"] = env_class
    events = read_session(session)
    if custom:
        # A custom report covers only its own lab: session-wide events (no lab)
        # stay, events logged for any other lab are dropped.
        events = [e for e in events if e.get("lab", "") in (f"custom/{custom}", "")]
    deviations = {}
    for e in events:
        if e.get("kind") == "deviation":
            deviations.setdefault(e.get("lab", ""), []).append(e.get("text", ""))
    if custom:
        track = f"custom/{custom}"
        ucs, plan_statuses = [{"name": "Custom lab", "keys": [track]}], {}
    elif track == "adhoc":
        ucs, plan_statuses = adhoc_use_cases(work)
    else:
        ucs, plan_statuses = track_use_cases(track, env.get("class", "unknown"), specs_dir, tracks_dir)
    custom_root = custom_root or DEFAULT_CUSTOM_ROOT
    use_cases, failures, requirements = [], [], []
    failed_seen, row_keys = set(), set()
    explained, decisions = {}, {}
    for e in events:
        if e.get("kind") == "failure":
            explained.setdefault(e.get("lab", ""), []).append(e.get("text", ""))
        elif e.get("kind") == "decision":
            decisions[e.get("lab", "")] = e.get("text", "")
    stopped = any(e.get("kind") == "decision" and e.get("text", "").strip() == "stop" for e in events)
    for uc in ucs:
        labs = []
        for key in uc["keys"]:
            outcome = parse_outcome(os.path.join(key_dir(work, key, custom_root), "outcome"))
            row = lab_row(key, outcome, deviations, specs_dir, plan_statuses.get(key, ""), stopped)
            labs.append(row)
            if key not in row_keys:
                reqs, expected = spec_requirements(spec_path(key, specs_dir, custom_root))
                if reqs:
                    requirements += requirement_rows(key, outcome, row, reqs, expected,
                                                     read_steps(os.path.join(key_dir(work, key, custom_root), "steps")))
            row_keys.add(key)
            if row["result"] == "FAIL" and key not in failed_seen:
                failed_seen.add(key)
                failures.append({"lab": key, "note": row["note"],
                                 "explanation": " ".join(explained.get(key, [])) or "no explanation recorded",
                                 "decision": decisions.get(key, "")})
        use_cases.append({"name": uc["name"], "labs": labs})
    # A failure logged for a lab outside this track (an ad hoc run) still belongs
    # in the report.
    for key, texts in explained.items():
        if key and key not in row_keys and key not in failed_seen:
            failed_seen.add(key)
            failures.append({"lab": key, "note": "not a track lab", "explanation": " ".join(texts),
                             "decision": decisions.get(key, "")})
    dev_list = [{"lab": e.get("lab", ""), "text": e.get("text", "")}
                for e in events if e.get("kind") == "deviation"]
    questions, pending = [], None
    for e in events:
        if e.get("kind") == "question":
            if pending is not None:
                questions.append(pending)
            pending = {"q": e.get("text", ""), "a": ""}
        elif e.get("kind") == "answer" and pending is not None:
            pending["a"] = e.get("text", "")
            questions.append(pending)
            pending = None
    if pending is not None:
        questions.append(pending)
    leftovers = []
    # A custom run writes its own leftovers file under its work directory.
    leftovers_path = (os.path.join(key_dir(work, f"custom/{custom}", custom_root), "leftovers.txt") if custom
                      else os.path.join(work, "leftovers.txt"))
    try:
        with open(leftovers_path, encoding="utf-8") as fh:
            leftovers = [ln.rstrip("\n") for ln in fh if ln.strip()]
    except OSError:
        pass
    # A shipped lab's leftovers warn and do not fail it, so they get their own section.
    warnings = [ln for ln in leftovers if ln.startswith(WARN_PREFIX)]
    leftovers = [ln for ln in leftovers if not ln.startswith(WARN_PREFIX)]
    return {
        "environment": env,
        "chart_version": chart_version(),
        "track": track,
        "custom": custom,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "use_cases": use_cases,
        "requirements": requirements,
        "deviations": dev_list,
        "failures": failures,
        "leftovers": leftovers,
        "leftover_warnings": warnings,
        "base_kept": any(e.get("kind") == "keep-base" for e in events),
        "custom_kept": any(e.get("kind") == "keep-custom" for e in events),
        "runs": [{"lab": e.get("lab", ""), "text": e.get("text", "")} for e in events if e.get("kind") == "run"],
        "questions": questions,
    }


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(data):
    env = data["environment"]
    env_class = env.get("class", "unknown")
    if data.get("custom"):
        title = f"# Agentgateway custom lab report: {data['custom']} on {env_class}"
    else:
        title = f"# Agentgateway evaluation report: {data['track']} track on {env_class}"
    out = [title, "",
           f"Generated {data['generated_at']}.", "", "## Environment", "", "```"]
    for k, v in env.items():
        out.append(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}")
    out += [f"chart_version: {data['chart_version']}", "```", ""]
    for uc in data["use_cases"]:
        out += [f"## {uc['name']}", "", "| Lab | Result | Duration | Note |", "| --- | --- | --- | --- |"]
        for lab in uc["labs"]:
            out.append(f"| {cell(lab['key'])} | {cell(lab['result'])} | {lab['duration']} | {cell(lab['note'])} |")
        out.append("")
    if data.get("requirements"):
        # One table per lab. Each row ends in its result, `| R1 | text | PASS |`, so a
        # check can grep the rows; the reasons for FAIL and SKIP follow the table.
        out += ["## Requirements", ""]
        labs = []
        for r in data["requirements"]:
            if r["lab"] not in labs:
                labs.append(r["lab"])
        for lab in labs:
            rows = [r for r in data["requirements"] if r["lab"] == lab]
            out += [f"### {lab}", "", "| ID | Requirement | Result |", "| --- | --- | --- |"]
            out += [f"| {cell(r['id'])} | {cell(r['text'])} | {r['result']} |" for r in rows]
            out.append("")
            why = [f"- {r['id']}: {r['result']}, {r['detail']}" for r in rows if r["result"] != "PASS" and r["detail"]]
            if why:
                out += why + [""]
    if data.get("custom"):
        out += ["## Runs", ""]
        out += [f"- {r['lab'] or 'general'}: {r['text']}" for r in data.get("runs", [])] or ["None."]
        out.append("")
    out += ["## Deviations", ""]
    if data["deviations"]:
        out += [f"- {d['lab'] or 'general'}: {d['text']}" for d in data["deviations"]]
    else:
        out.append("None.")
    out += ["", "## Failures", ""]
    if data["failures"]:
        for f in data["failures"]:
            line = f"- {f['lab']}"
            if f.get("note"):
                line += f" ({f['note']})"
            line += f": {f['explanation']}"
            if f.get("decision"):
                line += f" Decision: {f['decision']}"
            out.append(line)
    else:
        out.append("None.")
    out += ["", "## Leftovers", ""]
    if data["leftovers"]:
        out += [f"- {ln}" for ln in data["leftovers"]]
    elif data.get("leftover_warnings"):
        out.append(NO_LEFTOVERS_BUT_WARNINGS)
    else:
        out.append(NO_LEFTOVERS)
    if data.get("base_kept"):
        out += ["", "The base install was kept on request."]
    if data.get("custom_kept"):
        out += ["", "The custom lab was kept on the cluster."]
    if data.get("leftover_warnings"):
        # Objects a shipped lab left behind. They do not fail the lab.
        out += ["", "## Leftover warnings", ""]
        out += [f"- {ln}" for ln in data["leftover_warnings"]]
    out += ["", "## Questions", ""]
    if data["questions"]:
        for qa in data["questions"]:
            out.append(f"- Q: {qa['q']}")
            out.append(f"  A: {qa['a'] or 'no answer recorded'}")
    else:
        out.append("None.")
    return "\n".join(out) + "\n"


def cmd_build(args):
    data = build_data(args.work, args.track, args.env_json, args.session, args.specs_dir, args.tracks_dir,
                      custom=args.custom, env_class=args.env_class, custom_root=args.custom_root)
    stamp = datetime.datetime.strptime(data["generated_at"], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y%m%d-%H%M%S")
    suffix = f"custom-{args.custom}" if args.custom else args.track
    base = f"{stamp}-{data['environment'].get('class') or 'unknown'}-{suffix}"
    os.makedirs(args.out, exist_ok=True)
    name, n = base, 0
    while os.path.exists(os.path.join(args.out, name + ".md")) or os.path.exists(os.path.join(args.out, name + ".json")):
        n += 1
        name = f"{base}-{n}"
    md, js = os.path.join(args.out, name + ".md"), os.path.join(args.out, name + ".json")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(data))
    with open(js, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print(md)
    print(js)
    return 0


def _results(data):
    res = {}
    for uc in data.get("use_cases", []):
        for lab in uc.get("labs", []):
            res.setdefault(lab["key"], lab["result"])
    return res


def cmd_compare(args):
    try:
        with open(args.a, encoding="utf-8") as fh:
            a = json.load(fh)
        with open(args.b, encoding="utf-8") as fh:
            b = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"report.py: cannot compare: {exc}", file=sys.stderr)
        return 2
    ea, eb = a.get("environment", {}), b.get("environment", {})
    print(f"environments: {ea.get('class', 'unknown')} ({a.get('chart_version', 'unknown')}) "
          f"-> {eb.get('class', 'unknown')} ({b.get('chart_version', 'unknown')})")
    ra, rb = _results(a), _results(b)
    for key in list(ra) + [k for k in rb if k not in ra]:
        x, y = ra.get(key, "absent"), rb.get(key, "absent")
        if x != y:
            print(f"{key}: {x} -> {y}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--work", required=True)
    which = b.add_mutually_exclusive_group(required=True)
    which.add_argument("--track")
    which.add_argument("--custom", help="build the single-row report for custom-labs/<name>")
    b.add_argument("--env-json", required=True)
    b.add_argument("--env-class", help="the class the run used (run-e2e.sh --env); overrides the env json's class")
    b.add_argument("--session", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--specs-dir", default=tracks.DEFAULT_SPECS)
    b.add_argument("--tracks-dir", default=tracks.DEFAULT_TRACKS)
    b.add_argument("--custom-root", help="the directory holding custom labs (default $E2E_CUSTOM_ROOT, "
                   "else custom-labs/ at the repo root)")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args(argv)
    return cmd_build(args) if args.cmd == "build" else cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
