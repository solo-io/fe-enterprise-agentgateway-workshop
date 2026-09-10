#!/usr/bin/env python3
"""labdoc.py — parse a lab markdown file, resolve it against its e2e spec,
and emit an executable bash driver.

The lab markdown is the source of truth: every command the driver runs is a
bash block lifted verbatim from the lab. The spec only selects which sections
run and what to assert about their output. A spec that names a section which
no longer exists in the lab is a hard error — that is the doc-drift detector.

Subcommands:
  sections <lab.md>              list normalized section headings + block counts
  plan <lab.md> <spec.yaml>      emit the bash driver on stdout
  lint <lab.md> <spec.yaml>      validate the spec against the lab, no output
  meta <spec.yaml>               emit shell-eval'able metadata (tier/requires/timeout)

A spec step is exactly one of:
  section:   lifts and runs bash blocks from the named lab heading
  probe:     harness-authored `script:`, for verification the lab does by hand
  resource:  declarative field read — poll `jsonpath:` on the named object and
             assert it equals `expect:`, no bash block involved
"""

import json
import os
import re
import shlex
import sys

try:
    import yaml
except ImportError:
    sys.exit("labdoc.py requires PyYAML (pip install pyyaml)")

# Fences may be indented when they sit inside a numbered list — several labs put
# their curl steps that way. Capture the indent so the body can be dedented.
FENCE = re.compile(r"^(\s*)```(\w*)\s*$")
FENCE_CLOSE = re.compile(r"^\s*```\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")

VALID_TIERS = {"t0", "t1-key", "t2-idp", "t3-cloud", "t4-infra", "t5-manual", "excluded"}


def normalize(text):
    """Normalize a heading for matching: strip markdown, lowercase, collapse space."""
    text = re.sub(r"`([^`]*)`", r"\1", text)          # inline code
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)     # bold
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def parse_lab(path):
    """Return an ordered list of sections:
    [{"heading": str, "norm": str, "level": int, "line": int, "blocks": [str, ...]}]

    Only ```bash blocks are collected. Unfenced/other-language blocks are
    ignored, which is what makes the docs' expected-output samples safe.
    """
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    sections = [{"heading": "(preamble)", "norm": "(preamble)", "level": 0,
                 "line": 0, "blocks": []}]
    in_block = False
    lang = ""
    indent = ""
    buf = []

    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        m = FENCE.match(line)
        if m and not in_block:
            in_block, indent, lang, buf = True, m.group(1), m.group(2), []
            continue
        if in_block:
            if FENCE_CLOSE.match(line):
                if lang == "bash":
                    # Dedent by the fence's own indentation so the block runs as
                    # written rather than with a stray leading indent.
                    body = [l[len(indent):] if l.startswith(indent) else l for l in buf]
                    sections[-1]["blocks"].append("\n".join(body))
                in_block = False
            else:
                buf.append(line)
            continue

        h = HEADING.match(line)
        if h:
            heading = h.group(2)
            sections.append({"heading": heading, "norm": normalize(heading),
                             "level": len(h.group(1)), "line": lineno, "blocks": []})

    return sections


def find_section(sections, wanted, lab_path):
    """Resolve a spec's `section:` to exactly one lab section."""
    target = normalize(wanted)
    hits = [s for s in sections if s["norm"] == target]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        near = [s["heading"] for s in sections if target in s["norm"] or s["norm"] in target]
        hint = f"  did you mean: {near}" if near else ""
        raise SystemExit(
            f"DRIFT: {lab_path} has no section '{wanted}'.\n"
            f"       The lab was renamed or restructured; update the spec.\n{hint}")
    raise SystemExit(
        f"DRIFT: {lab_path} has {len(hits)} sections named '{wanted}' "
        f"(lines {[s['line'] for s in hits]}). Headings must be unique to be addressable.")


def select_blocks(section, run, lab_path, label):
    """Apply a spec's `run:` selector to a section's bash blocks."""
    blocks = section["blocks"]
    if run in (None, "all"):
        chosen = list(range(len(blocks)))
    elif run == "none":
        chosen = []
    elif isinstance(run, int):
        chosen = [run - 1]
    elif isinstance(run, list):
        chosen = [int(i) - 1 for i in run]
    else:
        raise SystemExit(f"{label}: invalid `run:` value {run!r} (use all|none|int|list)")

    for i in chosen:
        if i < 0 or i >= len(blocks):
            raise SystemExit(
                f"DRIFT: {lab_path} section '{section['heading']}' has "
                f"{len(blocks)} bash block(s); spec asks for block {i + 1}.")
    return [(i + 1, blocks[i]) for i in chosen]


def load_spec(path):
    with open(path, encoding="utf-8") as fh:
        spec = yaml.safe_load(fh) or {}
    tier = spec.get("tier", "t0")
    if tier not in VALID_TIERS:
        raise SystemExit(f"{path}: invalid tier {tier!r}; expected one of {sorted(VALID_TIERS)}")
    return spec


# ---------------------------------------------------------------------------
# Driver generation
# ---------------------------------------------------------------------------

def q(value):
    return shlex.quote(str(value))


def emit_assertions(out, asserts, label):
    """Translate a step's `assert:` list into lib.sh assertion calls."""
    for a in asserts or []:
        if not isinstance(a, dict) or len(a) != 1:
            raise SystemExit(f"{label}: each assert entry must be a single key/value map, got {a!r}")
        kind, want = next(iter(a.items()))
        name = f"{label} — {kind} {want}"
        if kind == "status":
            out.append(f"assert_status {q(want)} {q(name)}")
        elif kind == "contains":
            out.append(f"assert_contains {q(want)} {q(name)}")
        elif kind == "not_contains":
            out.append(f"assert_not_contains {q(want)} {q(name)}")
        elif kind == "matches":
            out.append(f"assert_matches {q(want)} {q(name)}")
        elif kind == "not_matches":
            out.append(f"assert_not_matches {q(want)} {q(name)}")
        elif kind == "rc":
            out.append(f"assert_rc {q(want)} {q(name)}")
        else:
            raise SystemExit(f"{label}: unknown assertion {kind!r}")


class BlockCounter:
    """Monotonic across steps AND cleanup — cleanup lines are built into a
    separate list, so numbering off list length would collide and overwrite
    the step blocks' captured output.
    """

    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return f"{self.n:04d}"


def emit_step(out, step, sections, lab_path, workdir, idx, counter, cleanup=False):
    """Append driver lines for one spec step. Returns number of blocks written."""
    default_allow = True if cleanup else bool(step.get("allow_failure", False))
    wait = step.get("wait")
    retry = step.get("retry") or {}

    if "resource" in step:
        # Declarative field read: poll one field of one object and assert its
        # exact value. Replaces the probe shape "kubectl get -o jsonpath | grep",
        # which could pass on a coincidental substring.
        jp = step.get("jsonpath")
        want = step.get("expect")
        if not jp or want is None:
            raise SystemExit(
                f"{lab_path}: resource step {idx} needs `jsonpath:` and `expect:`")
        ns = step.get("namespace", "agentgateway-system")
        label = step.get("name", f"{step['resource']} {jp} == {want}")
        out.append(f"e2e_assert_resource_field {q(label)} {q(ns)} "
                   f"{q(step['resource'])} {q(jp)} {q(want)}")
        return 0

    if "probe" in step:
        # Harness-authored verification, used where the lab validates by hand
        # (MCP Inspector, browser, jwt.io). Not lifted from the doc.
        label = f"probe: {step['probe']}"
        units = [(0, step.get("script", ""))]
        if not units[0][1].strip():
            raise SystemExit(f"{lab_path}: probe '{step['probe']}' has no `script:`")
        section_name = step["probe"]
    else:
        if "section" not in step:
            raise SystemExit(f"{lab_path}: step {idx} needs `section:`, `probe:`, or `resource:`")
        section = find_section(sections, step["section"], lab_path)
        section_name = section["heading"]
        units = select_blocks(section, step.get("run", "all"), lab_path,
                              f"{lab_path} [{section_name}]")
        label = section_name

    if not units:
        reason = step.get("reason", "not selected")
        out.append(f"e2e_note {q(f'skipped: {section_name}')} {q(reason)}")
        return 0

    written = 0
    for ordinal, body in units:
        written += 1
        block_path = os.path.join(workdir, "blocks", f"{counter.next()}.sh")
        os.makedirs(os.path.dirname(block_path), exist_ok=True)
        with open(block_path, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")

        tag = label if ordinal == 0 else f"{label} [block {ordinal}]"

        # `retry:` gates the block whose output is asserted — the step's LAST
        # block — exactly like `assert:` and `wait:`. Applied to every block in
        # the section it silently misfires: an opening `kubectl apply` prints
        # "created", never the `type: Attached` the guard is waiting for, so it
        # spins out its full budget every run. That is pure wall-clock with no
        # protection (~310s across the suite), and for the rate-limit and budget
        # labs it re-executes side-effecting curls 8-10 times, perturbing the
        # very counters those labs assert on.
        is_last = ordinal == units[-1][0]
        attempts = int(retry.get("attempts", 1)) if is_last else 1
        delay = retry.get("delay", 2)
        until = retry.get("until", "") if is_last else ""

        out.append(f"e2e_run_block {q(block_path)} {q(tag)} {q(attempts)} "
                   f"{q(delay)} {q(until)} {q('1' if default_allow else '0')}")

        if is_last:
            # `wait:` goes after the section's LAST block. A section commonly
            # opens with `kubectl create namespace` and applies the Deployment
            # in a later block, so waiting after the first block would look for
            # an object that does not exist yet.
            if wait:
                out.append(f"e2e_wait {q(wait)}")
            emit_assertions(out, step.get("assert"), tag)

    return written


def uncovered_sections(lab_path, spec_path):
    """Lab sections that contain bash blocks but no spec step names them.

    Reported by --lint so partial coverage is visible instead of implied.
    """
    spec = load_spec(spec_path)
    sections = parse_lab(lab_path)
    named = {normalize(s["section"]) for s in (spec.get("steps") or []) if "section" in s}
    cleanup = spec.get("cleanup")
    for s in (cleanup if isinstance(cleanup, list) else [cleanup] if cleanup else []):
        if isinstance(s, dict) and "section" in s:
            named.add(normalize(s["section"]))
    return [s["heading"] for s in sections
            if s["blocks"] and s["norm"] not in named and s["norm"] != "(preamble)"]


def build_driver(lab_path, spec_path, workdir, repo_root):
    spec = load_spec(spec_path)
    sections = parse_lab(lab_path)

    counter = BlockCounter()
    out = []
    for idx, step in enumerate(spec.get("steps") or [], start=1):
        emit_step(out, step, sections, lab_path, workdir, idx, counter)

    cleanup_lines = []
    cleanup = spec.get("cleanup")
    if cleanup:
        cleanup_lines.append("e2e_phase cleanup")
        steps = cleanup if isinstance(cleanup, list) else [cleanup]
        for idx, step in enumerate(steps, start=1):
            emit_step(cleanup_lines, step, sections, lab_path, workdir, idx,
                      counter, cleanup=True)

    lib = os.path.join(repo_root, "e2e", "lib", "lib.sh")
    header = [
        "#!/usr/bin/env bash",
        "# GENERATED by e2e/lib/labdoc.py — do not edit.",
        f"# lab:  {lab_path}",
        f"# spec: {spec_path}",
        # No -e/-u/-o pipefail on purpose; see the note at the top of lib.sh.
        f"cd {q(repo_root)}",
        f"export E2E_WORKDIR={q(workdir)}",
        f"export E2E_LAB={q(lab_path)}",
        f"source {q(lib)}",
        "e2e_begin",
        # Cleanup runs even when the body fails or the runner kills us, so a
        # failed lab does not poison the next one.
        "trap 'e2e_run_cleanup' EXIT",
        "e2e_define_cleanup() {",
    ]
    for line in cleanup_lines or ["  :"]:
        header.append(f"  {line}")
    header.append("}")
    header.append("e2e_phase steps")

    return "\n".join(header + out + ["e2e_end"]) + "\n"


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    repo_root = os.environ.get("E2E_REPO_ROOT", os.getcwd())

    if cmd == "sections":
        for s in parse_lab(args[0]):
            if s["blocks"] or s["level"]:
                print(f"L{s['level']} line{s['line']:>5}  bash={len(s['blocks'])}  {s['heading']}")
        return

    if cmd == "meta":
        spec = load_spec(args[0])
        req = spec.get("requires") or []
        print(f"E2E_SPEC_LAB={q(spec.get('lab', ''))}")
        print(f"E2E_SPEC_TIER={q(spec.get('tier', 't0'))}")
        print(f"E2E_SPEC_TIMEOUT={q(spec.get('timeout', 300))}")
        print(f"E2E_SPEC_REQUIRES={q(' '.join(req))}")
        print(f"E2E_SPEC_REASON={q(spec.get('reason', ''))}")
        print(f"E2E_SPEC_DESC={q(spec.get('description', ''))}")
        return

    if cmd in ("plan", "lint"):
        lab, spec_path = args[0], args[1]
        workdir = args[2] if len(args) > 2 else os.path.join("/tmp", "e2e-lint")
        driver = build_driver(lab, spec_path, workdir, repo_root)
        if cmd == "plan":
            sys.stdout.write(driver)
        else:
            n = driver.count("e2e_run_block")
            missed = uncovered_sections(lab, spec_path)
            note = f"  [uncovered: {', '.join(missed)}]" if missed else ""
            print(f"{spec_path} -> {n} block(s) from {lab}{note}")
        return

    sys.exit(f"unknown subcommand {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    main()
