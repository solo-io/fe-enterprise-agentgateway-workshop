#!/usr/bin/env python3
"""promote.py: copy a staged custom lab into the repo as a shipped lab.

Usage:
  promote.py NAME --category CAT [--lib ASSET=CONCERN]... [--subdir]
             [--stage DIR] [--root REPO] [--work DIR] [--dry-run]
      copies <stage>/NAME.md to labs/CAT/NAME.md (or labs/CAT/NAME/README.md
      with --subdir), <stage>/spec.yaml to e2e/specs/CAT/NAME.yaml with its
      lab: line rewritten, each --lib asset to lib/CONCERN/ASSET (CONCERN
      may nest, as keycloak/sub), and every other asset to
      labs/CAT/NAME/ASSET (--subdir only). In the lab text,
      custom-labs/NAME/assets/ASSET references become the asset's new path
      and relative Markdown link targets are recomputed for the new location.
      NAME and CAT are single segments matching ^[a-z0-9][a-z0-9-]*$, and
      labs/CAT/ must exist. Refuses to run when any target exists, when the
      lab exists in the other layout (NAME.md versus NAME/README.md), or when
      the lab, the spec, or a text asset still holds custom-labs/ after the
      rewrite. Prints a warning for each target git ignores. Writes
      <work>/manifest.json.
  promote.py NAME --record FILE [--root REPO] [--work DIR]
      saves a copy of the repo-relative FILE under <work>/before/ so that
      --undo can restore it after a later hand edit. Refuses absolute paths
      and paths outside the repo.
  promote.py NAME --undo [--root REPO] [--work DIR]
      restores recorded files, deletes the files a run created, and removes
      the directories it created once they are empty. An entry that fails is
      reported and the rest still run. The saved copies and the manifest are
      removed only when every entry succeeded; otherwise undo exits 1 and
      keeps them.

Defaults: --root is two levels above this file, --work is
<root>/e2e/.work/contribute/NAME/, --stage is <work>/stage/. The tool reads
the staging copy only and never reads or writes custom-labs/.

Manifest: {"name", "created", "recorded", "rewrites"}. "created" lists
repo-relative paths in creation order; a directory the run created ends
with "/". "recorded" holds {"path", "before"} with "before" relative to
<work>. "rewrites" holds {"file", "from", "to"}.

Stdlib only. Exits 0 on success, 1 on any refusal or error.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

LINK_RE = re.compile(r'\]\(([^)\s#][^)\s]*)(\s+"[^"]*")?\)')
# A path character, or a dot followed by one, ends a match too early to be a whole reference.
PATH_CONT = r"(?![\w/-]|\.[\w/-])"
TEXT = {"encoding": "utf-8", "newline": ""}
LAB_LINE_RE = re.compile(r"^lab:.*$", re.M)
SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PromoteError(Exception):
    pass


class Move:
    """One file copy: kind is lab, spec or asset; paths are repo-relative except src."""

    def __init__(self, kind, src, dst, asset=None):
        self.kind = kind
        self.src = src
        self.dst = dst
        self.asset = asset


def default_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def rel(path):
    return path.replace(os.sep, "/")


def list_assets(stage):
    adir = os.path.join(stage, "assets")
    out = []
    if not os.path.isdir(adir):
        return out
    for dirpath, dirnames, filenames in os.walk(adir):
        dirnames.sort()
        for fn in sorted(filenames):
            out.append(rel(os.path.relpath(os.path.join(dirpath, fn), adir)))
    return out


def lab_target(name, category, subdir):
    if subdir:
        return "labs/%s/%s/README.md" % (category, name)
    return "labs/%s/%s.md" % (category, name)


def plan(name, category, libs, subdir, stage, root):
    """Return the list of Moves, raising PromoteError on a missing input,
    an asset without a destination, or an existing target."""
    lab_src = os.path.join(stage, name + ".md")
    spec_src = os.path.join(stage, "spec.yaml")
    for p in (lab_src, spec_src):
        if not os.path.isfile(p):
            raise PromoteError("missing %s in the staging copy" % p)
    assets = list_assets(stage)
    for a in libs:
        if a not in assets:
            raise PromoteError("--lib names asset %s, which is not in %s" % (a, os.path.join(stage, "assets")))
    moves = [
        Move("lab", lab_src, lab_target(name, category, subdir)),
        Move("spec", spec_src, "e2e/specs/%s/%s.yaml" % (category, name)),
    ]
    for a in assets:
        if a in libs:
            dst = "lib/%s/%s" % (libs[a], a)
        elif subdir:
            dst = "labs/%s/%s/%s" % (category, name, a)
        else:
            raise PromoteError("asset %s has no destination: pass --lib or --subdir" % a)
        moves.append(Move("asset", os.path.join(stage, "assets", a), dst, asset=a))
    seen = set()
    for m in moves:
        if m.dst in seen:
            raise PromoteError("two files map to %s" % m.dst)
        seen.add(m.dst)
    # The same lab name in the other layout counts too: two labs would share a name.
    other = lab_target(name, category, not subdir)
    for dst in [m.dst for m in moves] + [other]:
        if os.path.lexists(os.path.join(root, dst)):
            raise PromoteError("refusing to overwrite %s" % dst)
    return moves


def asset_map_of(moves, name):
    return {"custom-labs/%s/assets/%s" % (name, m.asset): m.dst for m in moves if m.kind == "asset"}


def add_rewrite(rewrites, fname, old, new):
    if rewrites is None:
        return
    row = {"file": fname, "from": old, "to": new}
    if row not in rewrites:
        rewrites.append(row)


def replace_assets(text, asset_map, rewrites=None, fname=None):
    # A match must end the path, so kc.yaml.bak is left for the leftover check.
    for old in sorted(asset_map, key=len, reverse=True):
        new = asset_map[old]
        text, n = re.subn(re.escape(old) + PATH_CONT, lambda _m: new, text)
        if n:
            add_rewrite(rewrites, fname, old, new)
    return text


def rewrite_lab(text, name, asset_map, old_dir, new_dir, rewrites=None, fname=None):
    """Recompute relative link targets for new_dir, then replace asset references.
    old_dir and new_dir are repo-relative directories."""

    def fix(match):
        target, title = match.group(1), match.group(2) or ""
        if "://" in target or target.startswith(("mailto:", "/")):
            return match.group(0)
        path, frag = target, ""
        if "#" in target:
            path, frag = target.split("#", 1)
            frag = "#" + frag
        resolved = rel(os.path.normpath(os.path.join(old_dir, path)))
        resolved = asset_map.get(resolved, resolved)
        new = rel(os.path.relpath(resolved, new_dir)) + frag
        if new != target:
            add_rewrite(rewrites, fname, target, new)
        return "](%s%s)" % (new, title)

    text = LINK_RE.sub(fix, text)
    return replace_assets(text, asset_map, rewrites, fname)


def rewrite_spec(text, new_lab_rel, asset_map=None, rewrites=None, fname=None):
    """Rewrite the top-level lab: line; also replace any asset references."""
    match = LAB_LINE_RE.search(text)
    if not match:
        raise PromoteError("spec has no lab: line")
    new_line = "lab: " + new_lab_rel
    if match.group(0) != new_line:
        add_rewrite(rewrites, fname, match.group(0), new_line)
    text = text[:match.start()] + new_line + text[match.end():]
    return replace_assets(text, asset_map or {}, rewrites, fname)


def render(moves, name, root):
    """Return ({dst: new text or None for a byte copy}, rewrites)."""
    amap = asset_map_of(moves, name)
    rewrites = []
    contents = {}
    lab_dst = next(m.dst for m in moves if m.kind == "lab")
    for m in moves:
        if m.kind == "lab":
            with open(m.src, **TEXT) as f:
                text = f.read()
            text = rewrite_lab(text, name, amap, "custom-labs/%s" % name,
                               os.path.dirname(m.dst), rewrites, m.dst)
        elif m.kind == "spec":
            with open(m.src, **TEXT) as f:
                text = f.read()
            text = rewrite_spec(text, lab_dst, amap, rewrites, m.dst)
        else:
            contents[m.dst] = None
            check_leftover(m.dst, asset_text(m.src))
            continue
        check_leftover(m.dst, text)
        contents[m.dst] = text
    return contents, rewrites


def asset_text(path):
    """The asset's text, or None for a binary file (a NUL byte or invalid UTF-8)."""
    with open(path, "rb") as f:
        data = f.read()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_leftover(dst, text):
    if text and "custom-labs/" in text:
        line = next(l for l in text.splitlines() if "custom-labs/" in l)
        raise PromoteError("%s still references custom-labs/ after rewriting: %s" % (dst, line.strip()))


def manifest_path(work):
    return os.path.join(work, "manifest.json")


def load_manifest(work, name):
    path = manifest_path(work)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"name": name, "created": [], "recorded": [], "rewrites": []}


def save_manifest(work, m):
    os.makedirs(work, exist_ok=True)
    tmp = manifest_path(work) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")
    os.replace(tmp, manifest_path(work))


def show(src, root):
    a = os.path.abspath(src)
    r = os.path.abspath(root)
    if a.startswith(r + os.sep):
        return rel(os.path.relpath(a, r))
    return a


def table(rows):
    rows = [("action", "source", "target")] + rows
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    lines = []
    for i, r in enumerate(rows):
        lines.append(" | ".join(r[j].ljust(widths[j]) for j in range(3)).rstrip())
        if i == 0:
            lines.append("-|-".join("-" * w for w in widths))
    return "\n".join(lines)


def ignored_targets(dsts, root):
    """Targets git would ignore; empty when git is missing or root is not a work tree."""
    try:
        inside = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
    except OSError:
        return []
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return []
    out = []
    for dst in dsts:
        try:
            proc = subprocess.run(["git", "-C", root, "check-ignore", "-q", "--", dst], capture_output=True)
        except OSError:
            return []
        if proc.returncode == 0:
            out.append(dst)
    return out


def apply(moves, work, root, name, dry_run=False):
    """Write every target and the manifest (nothing with dry_run). Returns the table text.
    The manifest is saved after each write, so a run that fails partway can be undone."""
    contents, rewrites = render(moves, name, root)
    rows = [("copy " + m.kind, show(m.src, root), m.dst) for m in moves]
    rows += [("rewrite " + r["file"], r["from"], r["to"]) for r in rewrites]
    warnings = ["warning: %s is gitignored and will not be committed" % d
                for d in ignored_targets([m.dst for m in moves], root)]
    out = "\n".join([table(rows)] + warnings)
    if dry_run:
        return out
    m = load_manifest(work, name)
    m["rewrites"].extend(r for r in rewrites if r not in m["rewrites"])
    for mv in moves:
        parent = os.path.dirname(mv.dst)
        missing = []
        while parent and not os.path.isdir(os.path.join(root, parent)):
            missing.append(parent)
            parent = os.path.dirname(parent)
        for d in reversed(missing):
            os.makedirs(os.path.join(root, d))
            m["created"].append(d + "/")
            save_manifest(work, m)
        dst = os.path.join(root, mv.dst)
        if contents[mv.dst] is None:
            shutil.copy2(mv.src, dst)
        else:
            with open(dst, "w", **TEXT) as f:
                f.write(contents[mv.dst])
        m["created"].append(mv.dst)
        save_manifest(work, m)
    return out


def record(name, path, root, work):
    norm = rel(os.path.normpath(path))
    if os.path.isabs(path) or norm == ".." or norm.startswith("../"):
        raise PromoteError("record: %s is outside the repo" % path)
    path = norm
    src = os.path.join(root, path)
    if not os.path.isfile(src):
        raise PromoteError("cannot record %s: not a file" % path)
    m = load_manifest(work, name)
    if path in m["created"]:
        raise PromoteError("%s was created by promote; undo deletes it, no need to record" % path)
    if any(r["path"] == path for r in m["recorded"]):
        print("already recorded %s" % path)
        return
    before = "before/" + path.replace("/", "__")
    dst = os.path.join(work, before)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    m["recorded"].append({"path": path, "before": before})
    save_manifest(work, m)
    print("recorded %s -> %s" % (path, before))


def undo(name, root, work):
    """Restore, delete and remove each entry on its own; an entry that fails is
    reported and the rest still run. The saved copies and the manifest go only
    when every entry succeeded, so an incomplete undo can be rerun."""
    path = manifest_path(work)
    if not os.path.exists(path):
        raise PromoteError("no manifest at %s" % path)
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    ok = True
    skipped = []
    for r in m["recorded"]:
        saved = os.path.join(work, r["before"])
        if not os.path.isfile(saved):
            # An earlier undo already restored this file and removed its copy,
            # or the user removed the copy. Nothing is written, so skipping is
            # safe; the user checks the file by hand.
            print("skip %s: no saved copy" % r["path"])
            skipped.append(r["path"])
            continue
        try:
            shutil.copy2(saved, os.path.join(root, r["path"]))
            print("restored %s" % r["path"])
        except OSError as e:
            print("cannot restore %s: %s" % (r["path"], e))
            ok = False
    files = [c for c in m["created"] if not c.endswith("/")]
    dirs = [c.rstrip("/") for c in m["created"] if c.endswith("/")]
    for c in files:
        p = os.path.join(root, c)
        try:
            if os.path.lexists(p):
                os.remove(p)
                print("deleted %s" % c)
            else:
                print("already gone %s" % c)
        except OSError as e:
            print("cannot delete %s: %s" % (c, e))
            ok = False
    for d in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
        p = os.path.join(root, d)
        try:
            if os.path.isdir(p) and not os.listdir(p):
                os.rmdir(p)
                print("removed directory %s/" % d)
            elif os.path.isdir(p):
                print("kept non-empty directory %s/" % d)
        except OSError as e:
            print("cannot remove directory %s/: %s" % (d, e))
            ok = False
    if not ok:
        raise PromoteError("undo incomplete; manifest kept")
    for r in m["recorded"]:
        saved = os.path.join(work, r["before"])
        if os.path.exists(saved):
            os.remove(saved)
    if skipped:
        print("check by hand, no saved copy was restored: %s" % ", ".join(skipped))
    before_dir = os.path.join(work, "before")
    if os.path.isdir(before_dir) and not os.listdir(before_dir):
        os.rmdir(before_dir)
    os.remove(path)
    print("removed manifest %s" % show(path, root))


def parse_libs(values):
    libs = {}
    for v in values:
        if "=" not in v:
            raise PromoteError("--lib expects ASSET=CONCERN, got %s" % v)
        a, c = v.split("=", 1)
        if not a or a in (".", "..") or "/" in a:
            raise PromoteError("--lib asset must be a plain file name, got %s" % v)
        if c.startswith("/") or any(seg in ("", ".", "..") for seg in c.split("/")):
            raise PromoteError("--lib concern must be a relative path without empty, . or .. segments, got %s" % v)
        libs[a] = c
    return libs


def check_name(name):
    if not SEGMENT_RE.match(name):
        raise PromoteError("invalid name: %s" % name)


def check_category(category, root):
    if not SEGMENT_RE.match(category):
        raise PromoteError("invalid category: %s" % category)
    if not os.path.isdir(os.path.join(root, "labs", category)):
        raise PromoteError("no such category labs/%s/" % category)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copy a staged custom lab into the repo.")
    ap.add_argument("name")
    ap.add_argument("--category")
    ap.add_argument("--lib", action="append", default=[])
    ap.add_argument("--subdir", action="store_true")
    ap.add_argument("--stage")
    ap.add_argument("--root")
    ap.add_argument("--work")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--record")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root or default_root())
    try:
        check_name(args.name)
        work = os.path.abspath(args.work or os.path.join(root, "e2e", ".work", "contribute", args.name))
        stage = os.path.abspath(args.stage or os.path.join(work, "stage"))
        if args.undo:
            undo(args.name, root, work)
        elif args.record:
            record(args.name, args.record, root, work)
        else:
            if not args.category:
                raise PromoteError("--category is required")
            check_category(args.category, root)
            moves = plan(args.name, args.category, parse_libs(args.lib), args.subdir, stage, root)
            print(apply(moves, work, root, args.name, dry_run=args.dry_run))
            if args.dry_run:
                print("dry run: nothing written")
    except PromoteError as e:
        print("promote: %s" % e, file=sys.stderr)
        return 1
    except OSError as e:
        print("promote: %s (run --undo to remove what was written)" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
