#!/usr/bin/env python3
"""report.py: build the evaluation report from a run's outcome files.

Usage:
  report.py build --work DIR --track <llm|mcp|adhoc> --env-json PATH
                  --session PATH --out DIR [--specs-dir D] [--tracks-dir T]
      writes <out>/<YYYYMMDD-HHMM>-<class>-<track>.md and .json, prints both paths
  report.py compare A.json B.json
      one line per lab whose result differs, after a line naming both environments

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
    return out if "status" in out else None


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


def fmt_duration(value):
    try:
        secs = int(float(value))
    except (TypeError, ValueError):
        return "-"
    if secs <= 0:
        return "-"
    mins, s = divmod(secs, 60)
    return f"{mins}m{s:02d}s" if mins else f"{s}s"


def lab_row(key, outcome, deviations, specs_dir):
    """Map one outcome to the row dict {key, result, duration, note}."""
    if outcome is None:
        return {"key": key, "result": "FAIL", "duration": "-", "note": "no outcome recorded"}
    status = outcome.get("status", "")
    dur = fmt_duration(outcome.get("duration"))
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
        result, note = f"SKIP (missing {req})" if req else "SKIP", ""
    elif status == "no-spec":
        result, note = "NOT AUTOMATED", "no spec for this lab"
    elif status == "not-applicable":
        result, note = f"NOT APPLICABLE (only on {outcome.get('only_on', '?')})", ""
    else:
        result, note = "FAIL", f"unknown status {status}"
    return {"key": key, "result": result, "duration": dur, "note": note}


def chart_version():
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
            keys.append(os.path.relpath(root, work))
    return [{"name": "Ad hoc run", "keys": sorted(keys)}]


def track_use_cases(track, env_class, specs_dir, tracks_dir):
    path = tracks._resolve_track(tracks_dir, track)
    data = tracks.load_track(path)
    return [{"name": uc["name"], "keys": [lab["spec"] for lab in uc.get("labs") or []]}
            for uc in data.get("use_cases") or []]


def build_data(work, track, env_json, session, specs_dir, tracks_dir):
    with open(env_json, encoding="utf-8") as fh:
        env = json.load(fh)
    events = read_session(session)
    deviations = {}
    for e in events:
        if e.get("kind") == "deviation":
            deviations.setdefault(e.get("lab", ""), []).append(e.get("text", ""))
    if track == "adhoc":
        ucs = adhoc_use_cases(work)
    else:
        ucs = track_use_cases(track, env.get("class", "unknown"), specs_dir, tracks_dir)
    use_cases, failures = [], []
    failed_seen = set()
    explained = {}
    for e in events:
        if e.get("kind") == "failure":
            explained.setdefault(e.get("lab", ""), []).append(e.get("text", ""))
    for uc in ucs:
        labs = []
        for key in uc["keys"]:
            row = lab_row(key, parse_outcome(os.path.join(work, key, "outcome")), deviations, specs_dir)
            labs.append(row)
            if row["result"] == "FAIL" and key not in failed_seen:
                failed_seen.add(key)
                failures.append({"lab": key, "note": row["note"],
                                 "explanation": " ".join(explained.get(key, [])) or "no explanation recorded"})
        use_cases.append({"name": uc["name"], "labs": labs})
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
    try:
        with open(os.path.join(work, "leftovers.txt"), encoding="utf-8") as fh:
            leftovers = [ln.rstrip("\n") for ln in fh if ln.strip()]
    except OSError:
        pass
    return {
        "environment": env,
        "chart_version": chart_version(),
        "track": track,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "use_cases": use_cases,
        "deviations": dev_list,
        "failures": failures,
        "leftovers": leftovers,
        "questions": questions,
    }


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(data):
    env = data["environment"]
    out = [f"# Agentgateway evaluation report: {data['track']} track on {env.get('class', 'unknown')}", "",
           f"Generated {data['generated_at']}.", "", "## Environment", "", "```"]
    for k, v in env.items():
        out.append(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}")
    out += [f"chart_version: {data['chart_version']}", "```", ""]
    for uc in data["use_cases"]:
        out += [f"## {uc['name']}", "", "| Lab | Result | Duration | Note |", "| --- | --- | --- | --- |"]
        for lab in uc["labs"]:
            out.append(f"| {cell(lab['key'])} | {cell(lab['result'])} | {lab['duration']} | {cell(lab['note'])} |")
        out.append("")
    out += ["## Deviations", ""]
    if data["deviations"]:
        out += [f"- {d['lab'] or 'general'}: {d['text']}" for d in data["deviations"]]
    else:
        out.append("None.")
    out += ["", "## Failures", ""]
    if data["failures"]:
        out += [f"- {f['lab']}: {f['explanation']}" for f in data["failures"]]
    else:
        out.append("None.")
    out += ["", "## Leftovers", ""]
    out += [f"- {ln}" for ln in data["leftovers"]] if data["leftovers"] else [NO_LEFTOVERS]
    out += ["", "## Questions", ""]
    if data["questions"]:
        for qa in data["questions"]:
            out.append(f"- Q: {qa['q']}")
            out.append(f"  A: {qa['a'] or 'no answer recorded'}")
    else:
        out.append("None.")
    return "\n".join(out) + "\n"


def cmd_build(args):
    data = build_data(args.work, args.track, args.env_json, args.session, args.specs_dir, args.tracks_dir)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    base = f"{stamp}-{data['environment'].get('class') or 'unknown'}-{args.track}"
    os.makedirs(args.out, exist_ok=True)
    md, js = os.path.join(args.out, base + ".md"), os.path.join(args.out, base + ".json")
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
    with open(args.a, encoding="utf-8") as fh:
        a = json.load(fh)
    with open(args.b, encoding="utf-8") as fh:
        b = json.load(fh)
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
    b.add_argument("--track", required=True)
    b.add_argument("--env-json", required=True)
    b.add_argument("--session", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--specs-dir", default=tracks.DEFAULT_SPECS)
    b.add_argument("--tracks-dir", default=tracks.DEFAULT_TRACKS)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args(argv)
    return cmd_build(args) if args.cmd == "build" else cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
