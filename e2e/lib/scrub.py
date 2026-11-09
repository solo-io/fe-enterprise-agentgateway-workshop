#!/usr/bin/env python3
"""scrub.py: report candidate leaks in a custom lab before it is proposed upstream.

Usage:
  scrub.py [--allow GLOB]... [--from-changes CHANGES.md] PATH...
      walks files (directories recursively, text files only) and prints one line
      per hit: <relpath>:<line>\t<rule>\t<match>. Exits 0 whatever it finds,
      2 when a path (or the CHANGES.md) does not exist. --summary prints one
      line per distinct rule and match instead: count, rule, match, first
      location, sorted by rule then count descending.

Rules: ipv4, ipv6, hostname, email, bearer, secret-prefix, base64-blob,
cloud-id, user-value. The scan favours false positives over misses, so a human
reads the output. --allow adds hostname globs to the built-in allowlist.
--from-changes reads the Configuration and Requirements tables of a CHANGES.md
and treats each backticked value and each run of 2+ capitalised words as a
user-value to look for.

Stdlib only; reads local files only.
"""
import argparse
import fnmatch
import os
import re
import sys

DEFAULT_ALLOW = [
    "*.svc", "*.svc.cluster.local", "example.com", "*.example.com", "localhost",
    "api.openai.com", "quay.io", "docker.io", "ghcr.io", "github.com",
    "docs.solo.io", "agentgateway.dev", "*.kubernetes.io", "*.k8s.io", "*.solo.io",
]
FILE_EXTS = {"md", "yaml", "yml", "json", "sh", "py"}
IGNORED_NAMESPACES = {"agentgateway-system", "default", "keycloak"}
GENERIC_VALUES = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SOLO_TRIAL_LICENSE_KEY", "GATEWAY_IP", "KUBECONFIG"}
# Last labels that count as a real TLD besides any 2-letter alphabetic one.
KNOWN_TLDS = set(
    "com net org edu gov mil int biz info dev app cloud tech xyz ai io internal local "
    "corp lan home company group systems network online site store bank finance".split()
)

IPV4 = re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w])")
_G = r"[0-9A-Fa-f]{1,4}"
IPV6 = re.compile(
    r"(?<![\w:])(?:(?:%s:){7}%s|(?:%s(?::%s){0,6})?::(?:%s(?::%s){0,6})?)(?![\w:])" % ((_G,) * 6)
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
HOSTNAME = re.compile(r"(?<![\w.@-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
VERSION = re.compile(r"v?\d+\.\d+(\.\d+)*")
BEARER = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{3,})", re.IGNORECASE)
SECRET_PREFIX = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]*|\beyJ[A-Za-z0-9_.-]{3,}|\bAKIA[A-Z0-9]{8,}")
BASE64 = re.compile(r"[A-Za-z0-9+/=]{40,}")
KIND_IMAGE_LINE = re.compile(r"^\s*(?:-\s*)?(?:kind|image):")
ARN = re.compile(r"\barn:aws[\w-]*:[\w-]*:[\w-]*:\d*:[^\s'\"`)\]]+")
GCP_PROJECT = re.compile(r"\bprojects/[a-z][a-z0-9-]{4,}/")
GUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def _allowed(host, patterns):
    host = host.lower()
    return any(fnmatch.fnmatch(host, p.lower()) for p in patterns)


def _line_hits(line, allow, user_values):
    """Yield (rule, match) for one line, rules in documented order."""
    patterns = DEFAULT_ALLOW + list(allow)

    for m in IPV4.finditer(line):
        yield "ipv4", m.group(0)

    for m in IPV6.finditer(line):
        if re.search(r"[0-9A-Fa-f]", m.group(0)):
            yield "ipv6", m.group(0)

    email_spans = []
    emails = []
    for m in EMAIL.finditer(line):
        email_spans.append(m.span())
        # An email is only clean under example.com (or a user --allow glob).
        if not _allowed(m.group(1), ["example.com", "*.example.com"] + list(allow)):
            emails.append(m.group(0))

    for m in HOSTNAME.finditer(line):
        if any(s <= m.start() < e for s, e in email_spans):
            continue
        token = m.group(0)
        labels = token.split(".")
        tld = labels[-1]
        if tld.lower() in FILE_EXTS or VERSION.fullmatch(token):
            continue
        if not (tld.isalpha() and (len(tld) == 2 or tld.lower() in KNOWN_TLDS)):
            continue
        if line[m.end():m.end() + 1] == "(":
            continue
        if _allowed(token, patterns):
            continue
        yield "hostname", token

    for e in emails:
        yield "email", e

    for m in BEARER.finditer(line):
        value = m.group(1)
        if value.isalpha() and value.islower() and len(value) < 10:
            continue  # prose such as "Bearer token"
        yield "bearer", m.group(0)

    for m in SECRET_PREFIX.finditer(line):
        yield "secret-prefix", m.group(0)

    if not KIND_IMAGE_LINE.match(line):
        for m in BASE64.finditer(line):
            if len(set(m.group(0))) >= 8:
                yield "base64-blob", m.group(0)

    for rx in (ARN, GCP_PROJECT, GUID):
        for m in rx.finditer(line):
            yield "cloud-id", m.group(0)

    for value in user_values:
        lead = r"(?<![A-Za-z0-9_])" if value[:1].isalnum() else ""
        trail = r"(?![A-Za-z0-9_])" if value[-1:].isalnum() else ""
        if re.search(lead + re.escape(value) + trail, line, re.IGNORECASE):
            yield "user-value", value


def scan_text(text, allow, user_values):
    """Return (line_no, rule, match) tuples, deduplicated per line."""
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        seen = set()
        for rule, match in _line_hits(line, allow, user_values):
            if (rule, match) not in seen:
                seen.add((rule, match))
                out.append((no, rule, match))
    return out


def user_values_from_changes(path):
    """Candidate user-specific values from the Configuration and Requirements tables."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    values = []
    kinds = set()
    section = ""
    header_next = False
    rows = []
    for i, line in enumerate(lines):
        if line.startswith("#"):
            section = line.lstrip("#").strip().lower()
            continue
        if not line.lstrip().startswith("|"):
            continue
        if not ("configuration" in section or "requirements" in section):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) or not c for c in cells):
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if re.fullmatch(r"\s*\|[\s:|-]+\|?\s*", nxt) and "-" in nxt:
            continue  # header row
        rows.append((section, cells))
    for section, cells in rows:
        if "configuration" in section and cells:
            kinds.add(cells[0])
    for _, cells in rows:
        for cell in cells:
            for v in re.findall(r"`([^`]+)`", cell):
                values.append(v.strip())
            stripped = re.sub(r"`[^`]*`", " ", cell)
            for v in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", stripped):
                values.append(v)
    out = []
    for v in values:
        if len(v) < 4 or v in IGNORED_NAMESPACES or v in kinds:
            continue
        if re.fullmatch(r"[A-Z][A-Za-z0-9]*", v):
            continue  # kind-like name such as HTTPRoute
        if v in GENERIC_VALUES or re.fullmatch(r"\d+", v):
            continue
        if re.fullmatch(r"/[a-z]{1,12}", v):
            continue  # a short single path segment such as /openai
        if v not in out:
            out.append(v)
    return out


def _is_binary(path):
    with open(path, "rb") as fh:
        return b"\0" in fh.read(1024)


def _walk(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d != ".git")
                for name in sorted(files):
                    yield os.path.join(root, name)
        else:
            yield p


def main(argv=None):
    ap = argparse.ArgumentParser(description="Report candidate leaks in a custom lab.")
    ap.add_argument("--allow", action="append", default=[], metavar="GLOB")
    ap.add_argument("--from-changes", metavar="CHANGES.md")
    ap.add_argument("--summary", action="store_true",
                    help="one line per distinct rule and match: count, rule, match, first location")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args(argv)

    missing = [p for p in args.paths if not os.path.exists(p)]
    if args.from_changes and not os.path.isfile(args.from_changes):
        missing.append(args.from_changes)
    if missing:
        for p in missing:
            print("scrub: no such path: %s" % p, file=sys.stderr)
        return 2

    user_values = user_values_from_changes(args.from_changes) if args.from_changes else []
    summary = {}
    for path in _walk(args.paths):
        try:
            if _is_binary(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print("scrub: cannot read %s: %s" % (path, exc), file=sys.stderr)
            continue
        rel = os.path.relpath(path)
        for no, rule, match in scan_text(text, args.allow, user_values):
            if args.summary:
                entry = summary.setdefault((rule, match), [0, "%s:%d" % (rel, no)])
                entry[0] += 1
            else:
                print("%s:%d\t%s\t%s" % (rel, no, rule, match))
    for (rule, match), (count, first) in sorted(summary.items(), key=lambda kv: (kv[0][0], -kv[1][0], kv[0][1])):
        print("%d\t%s\t%s\t%s" % (count, rule, match, first))
    return 0


if __name__ == "__main__":
    sys.exit(main())
