#!/usr/bin/env python3
"""tracks.py: the machine-readable side of tracks/*.md.

Each prose track has a sibling YAML listing its use cases and labs in run
order. `list` turns one into a run plan for an environment class; `lint`
checks the YAML and the prose agree with each other and with e2e/specs.

Usage:
  tracks.py list <track> --env <env> [--specs-dir D] [--tracks-dir T]
      one line per lab: <spec-key>\\t<status>\\t<use-case-name>
      status: run | no-spec | excluded | not-applicable:<only_on joined by ,> | duplicate
      excluded: the spec's tier is excluded or t5-manual, so the harness never runs it
  tracks.py lint [--specs-dir D] [--tracks-dir T] [--labs-root R]
      one line per problem, exit 1 if any
"""
import argparse
import glob
import os
import re
import sys
from typing import NamedTuple, Optional

import yaml

import labdoc

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_SPECS = os.path.join(REPO_ROOT, "e2e", "specs")
DEFAULT_TRACKS = os.path.join(REPO_ROOT, "tracks")

# Same link shape conventions.py uses.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# Base labs are installed by the harness, so they are never track entries.
BASE_LAB = re.compile(r"^00[12]-.*\.md$")
# Tiers run-e2e.sh records as EXCLUDED without running the lab.
EXCLUDED_TIERS = ("excluded", "t5-manual")


class Entry(NamedTuple):
    key: str
    status: str
    use_case: str
    lab: Optional[str] = None


def load_track(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _spec_path(specs_dir, key):
    return os.path.join(specs_dir, key + ".yaml")


def plan(track, env, specs_dir):
    seen = set()
    entries = []
    for uc in track.get("use_cases") or []:
        for lab in uc.get("labs") or []:
            key = lab["spec"]
            only_on = lab.get("only_on") or []
            no_spec = (lab.get("status") == "no-spec"
                       or not os.path.isfile(_spec_path(specs_dir, key)))
            if key in seen:
                status = "duplicate"
            elif only_on and env not in only_on:
                status = "not-applicable:" + ",".join(only_on)
            elif no_spec:
                status = "no-spec"
            elif labdoc.load_spec(_spec_path(specs_dir, key)).get("tier") in EXCLUDED_TIERS:
                status = "excluded"
            else:
                status = "run"
            seen.add(key)
            entries.append(Entry(key, status, uc["name"], lab.get("lab")))
    return entries


def _track_files(tracks_dir):
    return sorted(glob.glob(os.path.join(tracks_dir, "*.yaml")))


def _yaml_labs(track, specs_dir, labs_root):
    """Lab paths (repo-relative) a track YAML lists, plus missing-spec and stale no-spec keys."""
    labs, missing, stale = set(), [], []
    for uc in track.get("use_cases") or []:
        for lab in uc.get("labs") or []:
            if lab.get("status") == "no-spec":
                if os.path.isfile(_spec_path(specs_dir, lab["spec"])):
                    stale.append(lab["spec"])
                if lab.get("lab"):
                    labs.add(lab["lab"])
                continue
            path = _spec_path(specs_dir, lab["spec"])
            if not os.path.isfile(path):
                missing.append(lab["spec"])
                continue
            labs.add(labdoc.load_spec(path).get("lab"))
    return labs, missing, stale


def _prose_labs(md_path, labs_root):
    """Distinct lab links (repo-relative) in a prose track, base labs excluded."""
    if not os.path.isfile(md_path):
        return set()
    with open(md_path, encoding="utf-8") as fh:
        text = fh.read()
    found = set()
    for target in LINK.findall(text):
        target = target.split("#", 1)[0]
        if not target.endswith(".md") or re.match(r"^[a-z]+://", target):
            continue
        if BASE_LAB.match(os.path.basename(target)):
            continue
        full = os.path.normpath(os.path.join(os.path.dirname(md_path), target))
        found.add(os.path.relpath(full, labs_root))
    return found


def lint(tracks_dir, specs_dir, labs_root):
    errors = []
    for yaml_path in _track_files(tracks_dir):
        name = os.path.basename(yaml_path)
        md_name = os.path.splitext(name)[0] + ".md"
        labs, missing, stale = _yaml_labs(load_track(yaml_path), specs_dir, labs_root)
        for key in missing:
            errors.append(f"missing spec: {key} (in {name})")
        for key in stale:
            errors.append(f"stale no-spec: {key} (in {name})")
        prose = _prose_labs(os.path.join(tracks_dir, md_name), labs_root)
        for lab in sorted(prose - labs):
            errors.append(f"unlisted lab: {lab} (linked from {md_name})")
    return errors


def resolve_track(tracks_dir, name):
    for cand in (f"{name}-track.yaml", f"{name}.yaml"):
        path = os.path.join(tracks_dir, cand)
        if os.path.isfile(path):
            return path
    raise SystemExit(f"tracks.py: no track {name!r} in {tracks_dir}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("list", "lint"):
        p = sub.add_parser(cmd)
        p.add_argument("--specs-dir", default=DEFAULT_SPECS)
        p.add_argument("--tracks-dir", default=DEFAULT_TRACKS)
        if cmd == "list":
            p.add_argument("track")
            p.add_argument("--env", required=True)
        else:
            p.add_argument("--labs-root", default=REPO_ROOT)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        track = load_track(resolve_track(args.tracks_dir, args.track))
        for e in plan(track, args.env, args.specs_dir):
            print(f"{e.key}\t{e.status}\t{e.use_case}")
        return 0

    errors = lint(args.tracks_dir, args.specs_dir, args.labs_root)
    for line in errors:
        print(line)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
