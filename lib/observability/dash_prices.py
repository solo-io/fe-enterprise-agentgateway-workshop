#!/usr/bin/env python3
"""Extract / plan / apply / validate LLM price constants in the agentgateway
Grafana dashboard.

The dashboard hard-codes per-1M-token prices inside ~50 PromQL expressions plus
three markdown pricing-table panels. This script is the only sanctioned way to
change them: it edits every occurrence of a rate consistently, preserves each
panel's sign convention, and re-checks the invariants that make the cost panels
add up.

Subcommands
  extract    derive the rate card the dashboard currently implements
  write-map  (re)generate the model map from the dashboard
  plan       diff a proposed rate card against the dashboard, no writes
  apply      rewrite PromQL constants + markdown tables from a proposed card
  validate   run every invariant against the dashboard as it stands

Run `validate` before and after `apply`. `apply` refuses to write if the
post-edit validation fails.
"""

import argparse
import collections
import copy
import datetime
import itertools
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAP = os.path.join(HERE, "model-map.json")
DEFAULT_DASHBOARD = os.path.join(
    HERE, "agentgateway-grafana-dashboard-v1.json")

# gen_ai_token_type label value -> rate-card key
TOKEN_TYPES = {
    "input": "input",
    "output": "output",
    "input_cache_read": "cache_read",
    "input_cache_write": "cache_write",
}
RATE_KEYS = ["input", "output", "cache_read", "cache_write"]
CACHE_KEYS = ["cache_read", "cache_write"]

# A price site: gen_ai_token_type="X", gen_ai_response_model=(~)"MATCHER" ... * NUMBER
SITE_RE = re.compile(
    r'gen_ai_token_type="(?P<tt>input|output|input_cache_read|input_cache_write)", '
    r'gen_ai_response_model(?P<op>=~|=)"(?P<matcher>(?:[^"\\]|\\.)*)"'
)
MULT_RE = re.compile(r"\* *(-?\d+\.\d+)")
EPS = 1e-6


# --------------------------------------------------------------------------- io


def load(path):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    return raw, json.loads(raw)


def dump(dash):
    """Serialize exactly the way the checked-in file is serialized.

    Verified byte-identical round-trip: no trailing newline, indent=2.
    """
    return json.dumps(dash, indent=2, ensure_ascii=False)


def panels(dash):
    out = {}

    def walk(lst):
        for p in lst:
            out[p.get("id")] = p
            if p.get("panels"):
                walk(p["panels"])

    walk(dash.get("panels", []))
    return out


def iter_exprs(panel):
    """Yield (setter, expr, ref_id) for every PromQL expression on a panel."""
    for target in panel.get("targets") or []:
        expr = target.get("expr")
        if isinstance(expr, str):
            yield (lambda v, t=target: t.__setitem__("expr", v)), expr, target.get("refId")


def rewrite_expr(expr, fn):
    """Rewrite price constants in `expr`.

    fn(token_type_key, matcher, old_value_str) -> new_value_str or None.
    Returns (new_expr, n_changed).
    """
    out = []
    pos = 0
    changed = 0
    for m in SITE_RE.finditer(expr):
        mult = MULT_RE.search(expr, m.end())
        if not mult:
            continue
        new = fn(TOKEN_TYPES[m.group("tt")], m.group("matcher"), mult.group(1))
        if new is None or new == mult.group(1):
            continue
        out.append(expr[pos : mult.start(1)])
        out.append(new)
        pos = mult.end(1)
        changed += 1
    out.append(expr[pos:])
    return "".join(out), changed


# ---------------------------------------------------------------- extraction


Site = collections.namedtuple("Site", "panel_id panel_title matcher tt value ref_id")


def collect_sites(dash):
    sites = []
    for pid, panel in panels(dash).items():
        for _setter, expr, rid in iter_exprs(panel):
            if (pid, rid) in SAVINGS_SITES:
                continue
            for m in SITE_RE.finditer(expr):
                mult = MULT_RE.search(expr, m.end())
                if not mult:
                    continue
                sites.append(
                    Site(
                        pid,
                        panel.get("title", ""),
                        m.group("matcher"),
                        TOKEN_TYPES[m.group("tt")],
                        float(mult.group(1)),
                        rid,
                    )
                )
    return sites


def extract_card(dash):
    """Derive {matcher: {rates, convention, panels}} from the dashboard.

    Sign conventions in play (see SKILL.md):
      full  - every panel multiplies by the provider's list rate (Anthropic,
              Bedrock, mock rows: the three token types are counted disjointly)
      delta - net-cost panels carry (rate - input_rate) because the provider's
              `input` count already includes the cached/written tokens (OpenAI),
              while the isolated cache panels carry the full rate
    """
    sites = collect_sites(dash)
    grouped = collections.defaultdict(lambda: collections.defaultdict(set))
    where = collections.defaultdict(lambda: collections.defaultdict(set))
    for s in sites:
        grouped[s.matcher][s.tt].add(round(s.value, 6))
        where[s.matcher][s.tt].add(s.panel_id)

    card, problems = {}, []
    for matcher, tts in grouped.items():
        rates, conv = {}, None
        for key in ("input", "output"):
            vals = tts.get(key)
            if not vals:
                continue
            if len(vals) > 1:
                problems.append(
                    f"{matcher}: {key} has disagreeing constants {sorted(vals)} "
                    f"across panels {sorted(where[matcher][key])}"
                )
            rates[key] = max(vals)
        in_rate = rates.get("input")
        for key in CACHE_KEYS:
            vals = sorted(tts.get(key, ()))
            if not vals:
                continue
            if len(vals) == 1:
                rates[key] = vals[0]
                this = "full"
            elif len(vals) == 2 and in_rate is not None:
                lo, hi = vals
                if abs(lo - (hi - in_rate)) < EPS:
                    rates[key], this = hi, "delta"
                else:
                    problems.append(
                        f"{matcher}: {key} constants {vals} are not a "
                        f"full/(full-input) pair for input={in_rate}"
                    )
                    rates[key], this = hi, "full"
            else:
                problems.append(f"{matcher}: {key} has {len(vals)} distinct constants {vals}")
                rates[key], this = vals[-1], "full"
            if conv and this != conv:
                problems.append(
                    f"{matcher}: cache_read and cache_write disagree on convention "
                    f"({conv} vs {this})"
                )
            conv = conv or this
        card[matcher] = {
            "rates": rates,
            "convention": conv or "full",
            "panels": {k: sorted(v) for k, v in where[matcher].items()},
        }
    return card, problems


# ------------------------------------------------------------------ model map


def matcher_to_key(matcher):
    """gpt-5\\.4-nano.* -> gpt-5.4-nano ; claude-haiku-4-5-.* -> claude-haiku-4-5"""
    key = matcher.replace("\\\\.", ".").replace("\\.", ".")
    key = re.sub(r"\.\*$", "", key)
    return key.rstrip("-")


def guess_provider(matcher):
    key = matcher_to_key(matcher)
    if key.startswith("mock"):
        return "mock"
    if re.match(r"^(us|eu|apac)?\.?(anthropic|meta|mistral|amazon|cohere|ai21|deepseek)\.", key):
        return "bedrock"
    if key.startswith("claude"):
        return "anthropic"
    return "openai"


TABLE_PANELS = {"openai": 87, "anthropic": 90, "bedrock": 100}

# Targets whose constants are SAVINGS rates (input − cache_read per model), not list
# rates: excluded from site collection, checked and rewritten by their own paths.
SAVINGS_SITES = {(252, "E")}  # (panel_id, target refId) — Spend & Savings panel

TABLE_ROW_RE = re.compile(r"^\|\s*(?P<label>[^|]+?)\s*\|")


def table_rows(panel):
    """Yield (line_index, label) for data rows of a markdown pricing table."""
    content = panel.get("options", {}).get("content", "")
    for i, line in enumerate(content.split("\n")):
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        m = TABLE_ROW_RE.match(line)
        if not m:
            continue
        label = m.group("label")
        if label.lower() == "model":
            continue
        yield i, label


def build_map(dash, card):
    ps = panels(dash)
    labels = {}  # normalized label -> (panel_id, raw label)
    for pid in TABLE_PANELS.values():
        if pid not in ps:
            continue
        for _i, label in table_rows(ps[pid]):
            norm = label.replace(" (Bedrock)", "").strip()
            labels[norm] = (pid, label)

    models, unlinked = [], []
    for matcher in sorted(card, key=matcher_to_key):
        key = matcher_to_key(matcher)
        entry = {
            "key": key,
            "provider": guess_provider(matcher),
            "matcher": matcher,
            "cache_convention": card[matcher]["convention"],
        }
        hit = labels.pop(key, None)
        for alt in (
            key.replace(".", "-"),  # matcher with an unescaped dot: mock-gpt-5.2 -> mock-gpt-5-2
            re.sub(r"-20\d\d$", "", key),  # snapshot matcher: gpt-5-2025.* -> gpt-5
        ):
            if hit or alt == key:
                continue
            hit = labels.pop(alt, None)
            if hit:
                entry["key"] = alt
        if hit:
            entry["table_panel"], entry["table_label"] = hit
        else:
            unlinked.append(key)
        models.append(entry)
    return {"models": models}, unlinked, sorted(labels)


def load_map(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    by_key = {m["key"]: m for m in data["models"]}
    by_matcher = {m["matcher"]: m for m in data["models"]}
    return data, by_key, by_matcher


# ------------------------------------------------------------------ formatting


def fmt_const(v):
    """PromQL constant: always 4 decimals, matching the existing file."""
    return f"{v:.4f}"


def fmt_price(v):
    """Markdown table cell: exact, minimum two decimals ($0.25, $0.075, $3.125)."""
    s = f"{v:.4f}".rstrip("0")
    whole, _, dec = s.partition(".")
    return f"${whole}.{dec.ljust(2, '0')}"


def lossy(v):
    return abs(round(v, 4) - v) > 1e-9


# ---------------------------------------------------------------- proposed card


def load_proposed(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rates = data.get("rates", data)
    verified = data.get("verified", {})
    return rates, verified


def resolve_proposed(rates, by_key, current):
    """proposed rates keyed by model key -> keyed by matcher, with errors."""
    out, errors = {}, []
    for key, vals in rates.items():
        entry = by_key.get(key)
        if not entry:
            errors.append(
                f"unknown model '{key}': use the add-model subcommand to introduce it "
                f"first (see references/adding-a-model.md)"
            )
            continue
        cur = current.get(entry["matcher"], {}).get("rates", {})
        clean = {}
        for rk, v in vals.items():
            if rk not in RATE_KEYS:
                errors.append(f"{key}: unknown rate '{rk}'")
                continue
            if rk not in cur:
                errors.append(
                    f"{key}: dashboard has no '{rk}' term; adding a token type to the "
                    f"cost expressions is out of scope for this skill"
                )
                continue
            if lossy(float(v)):
                errors.append(f"{key}.{rk}={v} needs more than 4 decimals; not representable")
                continue
            clean[rk] = float(v)
        out[entry["matcher"]] = clean
    return out, errors


def diff(current, proposed_by_matcher, by_matcher):
    changes = []
    for matcher, vals in proposed_by_matcher.items():
        cur = current[matcher]["rates"]
        for rk, new in sorted(vals.items()):
            old = cur.get(rk)
            if old is None or abs(old - new) > 1e-9:
                changes.append(
                    {
                        "key": by_matcher[matcher]["key"],
                        "matcher": matcher,
                        "rate": rk,
                        "old": old,
                        "new": new,
                        "convention": current[matcher]["convention"],
                    }
                )
    return changes


# ----------------------------------------------------------------------- apply


def apply_card(dash, current, proposed_by_matcher, by_matcher, tables_verified, today):
    """Rewrite PromQL constants and markdown tables in place. Returns a report."""
    report = {"sites": 0, "table_rows": 0, "table_dates": [], "warnings": []}

    def new_value(kind, matcher, old_str):
        want = proposed_by_matcher.get(matcher)
        if not want:
            return None
        cur = current[matcher]
        old_full = cur["rates"].get(kind)
        if old_full is None:
            return None
        new_full = want.get(kind, old_full)
        new_in = want.get("input", cur["rates"].get("input"))
        old_in = cur["rates"].get("input")
        if kind in ("input", "output") or cur["convention"] == "full":
            return fmt_const(new_full)
        # delta convention: classify this site by its current constant
        old_val = float(old_str)
        if abs(old_val - old_full) < EPS:  # isolated cache panel: full rate
            return fmt_const(new_full)
        if abs(old_val - (old_full - old_in)) < EPS:  # net-cost panel: surcharge
            return fmt_const(new_full - new_in)
        report["warnings"].append(
            f"{by_matcher[matcher]['key']}.{kind}: constant {old_str} matches neither the "
            f"full rate ({old_full}) nor the net delta ({old_full - old_in}); left unchanged"
        )
        return None

    for pid, panel in panels(dash).items():
        for setter, expr, rid in iter_exprs(panel):
            if (pid, rid) in SAVINGS_SITES:
                continue
            new_expr, n = rewrite_expr(expr, new_value)
            if n:
                setter(new_expr)
                report["sites"] += n

    # markdown pricing tables
    ps = panels(dash)
    rows_by_panel = collections.defaultdict(dict)
    for matcher, entry in by_matcher.items():
        if "table_panel" in entry:
            rows_by_panel[entry["table_panel"]][entry["table_label"]] = matcher

    for provider, pid in TABLE_PANELS.items():
        panel = ps.get(pid)
        if not panel:
            continue
        lines = panel["options"]["content"].split("\n")
        touched = False
        for i, label in table_rows(panel):
            matcher = rows_by_panel[pid].get(label)
            if not matcher:
                report["warnings"].append(
                    f"panel {pid}: table row '{label}' is not linked to a model in "
                    f"model-map.json; left unchanged"
                )
                continue
            if matcher not in proposed_by_matcher:
                continue
            cells = lines[i].split("|")
            if len(cells) < 7:
                report["warnings"].append(f"panel {pid}: row '{label}' has unexpected shape")
                continue
            rates = dict(current[matcher]["rates"])
            rates.update(proposed_by_matcher[matcher])
            for offset, rk in enumerate(RATE_KEYS):
                v = rates.get(rk)
                cells[2 + offset] = f" {fmt_price(v) if v is not None else '—'} "
            new_line = "|".join(cells)
            if new_line != lines[i]:
                lines[i] = new_line
                report["table_rows"] += 1
                touched = True
        if touched and provider in tables_verified:
            for i, line in enumerate(lines):
                if line.startswith("_Last updated:"):
                    stamp = tables_verified.get(provider) or today
                    lines[i] = f"_Last updated: {stamp}_"
                    report["table_dates"].append((pid, stamp))
        panel["options"]["content"] = "\n".join(lines)

    merged = {}
    for matcher, cur in current.items():
        r = dict(cur["rates"]); r.update(proposed_by_matcher.get(matcher, {}))
        merged[matcher] = r

    def savings_value(kind, matcher, old_str):
        r = merged.get(matcher)
        if not r or kind != "cache_read":
            return None
        if r.get("input") is None or r.get("cache_read") is None:
            return None
        return fmt_const(r["input"] - r["cache_read"])

    ps2 = panels(dash)
    for pid, rid in SAVINGS_SITES:
        panel = ps2.get(pid)
        if not panel:
            continue
        for setter, expr, r in iter_exprs(panel):
            if r != rid:
                continue
            new_expr, n = rewrite_expr(expr, savings_value)
            if n:
                setter(new_expr)
                report["sites"] += n

    return report


# -------------------------------------------------------- add / remove a model


def promql_to_json_matcher(m):
    """--matcher is given in PromQL form (gpt-5\\.6-luna.*).

    The JSON layer needs each backslash doubled; the raw file then shows four.
    """
    return m.replace("\\", "\\\\")


def next_ref_id(used):
    """Grafana-style refId: A..Z, AA..AZ, ..."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for size in (1, 2, 3):
        for combo in itertools.product(letters, repeat=size):
            rid = "".join(combo)
            if rid not in used:
                return rid
    raise RuntimeError("ran out of refIds")


def is_price_line(line):
    return SITE_RE.search(line) is not None


def or_joined(expr):
    return any(line.strip() == "or" for line in expr.split("\n"))


def clone_constants(text, tmpl_matcher, tmpl_rates, tmpl_conv, new_rates):
    """Retarget the price constants in cloned template text to the new model.

    Runs before the matcher substitution, while the text still carries the
    template's matcher, and reuses the same site classification as `apply`:
    a constant equal to the template's full rate is an isolated cache panel,
    one equal to (full - input) is a net-cost panel.
    """

    def fn(kind, matcher, old_str):
        if matcher != tmpl_matcher:
            return None
        old_full, new_full = tmpl_rates.get(kind), new_rates.get(kind)
        if old_full is None or new_full is None:
            return None
        if kind in ("input", "output") or tmpl_conv == "full":
            return fmt_const(new_full)
        old_in, new_in = tmpl_rates["input"], new_rates["input"]
        old_val = float(old_str)
        if abs(old_val - old_full) < EPS:
            return fmt_const(new_full)
        if abs(old_val - (old_full - old_in)) < EPS:
            return fmt_const(new_full - new_in)
        if abs(old_val - (old_in - old_full)) < EPS:
            # savings constant (input - cache_read), see SAVINGS_SITES
            return fmt_const(new_in - new_full)
        return None

    out, _ = rewrite_expr(text, fn)
    return out


def sub_matcher(text, tmpl_matcher, new_matcher):
    """Swap the matcher only inside label selectors.

    Matching on the quoted form is what keeps `claude-3-5-haiku-.*` from also
    hitting `anthropic\\.claude-3-5-haiku-.*`, which is a substring of it.
    """
    return text.replace(f'"{tmpl_matcher}"', f'"{new_matcher}"')


def alt_insert(expr, tmpl_matcher, new_matcher):
    """Add the new matcher to a model alternation list, after the template's entry.

    The parenthesised form `(matcher)` is unambiguous where the bare matcher is not.
    """
    old = f"({tmpl_matcher})"
    return expr.replace(old, f"{old}|({new_matcher})") if old in expr else expr


def alt_remove(expr, matcher):
    for form in (f"({matcher})|", f"|({matcher})", f"({matcher})"):
        if form in expr:
            return expr.replace(form, "")
    return expr


def model_lines(expr, matcher):
    """Line indices of the price terms for `matcher` in a shared expression."""
    lines = expr.split("\n")
    return [
        i
        for i, line in enumerate(lines)
        if f'"{matcher}"' in line and is_price_line(line)
    ]


def contiguous_runs(idx):
    """Split a sorted list of line indices into runs of consecutive lines.

    A model can appear in more than one region of a single expression (panel
    220's cost-per-request target repeats the priced sum in both halves of a
    ratio). Treating first-to-last as one span would sweep every other model's
    lines in between into a clone or a delete.
    """
    runs = []
    for i in idx:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def add_model(dash, tmpl_entry, new_key, new_matcher, new_rates, table_label, card):
    """Clone a template model everywhere it appears. Returns a report."""
    tmpl_matcher = tmpl_entry["matcher"]
    conv = tmpl_entry.get("cache_convention", "full")
    tmpl_rates = card[tmpl_matcher]["rates"]
    report = {"targets": 0, "terms": 0, "alt": 0, "rows": 0, "warnings": []}

    def retarget(text):
        return sub_matcher(
            clone_constants(text, tmpl_matcher, tmpl_rates, conv, new_rates),
            tmpl_matcher,
            new_matcher,
        )

    for pid, panel in panels(dash).items():
        targets = panel.get("targets") or []
        clones = []  # (index_to_insert_after, cloned_target)
        used_refs = {t.get("refId") for t in targets}
        for ti, target in enumerate(targets):
            expr = target.get("expr")
            if not isinstance(expr, str):
                continue

            if ALT_RE.search(expr):
                grown = alt_insert(expr, tmpl_matcher, new_matcher)
                if grown != expr:
                    target["expr"] = expr = grown
                    report["alt"] += 1

            if f'"{tmpl_matcher}"' not in expr:
                continue

            others = {m.group("matcher") for m in SITE_RE.finditer(expr)} - {tmpl_matcher}
            if not others:
                # the template owns this target: clone the whole thing
                clone = copy.deepcopy(target)
                clone["expr"] = retarget(expr)
                rid = next_ref_id(used_refs)
                used_refs.add(rid)
                clone["refId"] = rid
                if isinstance(clone.get("legendFormat"), str):
                    clone["legendFormat"] = clone["legendFormat"].replace(
                        tmpl_entry["key"], new_key
                    )
                clones.append((ti, clone))
                report["targets"] += 1
                continue

            # shared target: clone the template's terms in place, one
            # contiguous run at a time (reversed so indices stay valid)
            lines = expr.split("\n")
            idx = model_lines(expr, tmpl_matcher)
            if not idx:
                continue
            # joiner style: some exprs put the `+` at the start of each line
            # (panel 252's savings sum), with the expression's opening paren
            # riding on the first term's line. A verbatim copy of that line
            # duplicates the paren; rebuild the clone as a middle line instead.
            lead = next(
                (
                    re.match(r"\s*\+\s*", l).group(0)
                    for l in lines
                    if re.match(r"\s*\+\s", l)
                ),
                None,
            )
            if lead is not None and not or_joined(expr):
                for run in reversed(contiguous_runs(idx)):
                    block = [
                        lead
                        + retarget(re.sub(r"^(\s*\+\s*|\(\s*)", "", line, count=1))
                        for line in lines[run[0] : run[-1] + 1]
                        if line.strip() != "or"
                    ]
                    lines[run[-1] + 1 : run[-1] + 1] = block
                target["expr"] = "\n".join(lines)
                report["terms"] += len(idx)
                continue
            for run in reversed(contiguous_runs(idx)):
                span = lines[run[0] : run[-1] + 1]  # includes interior `or` separators
                block = [
                    line if line.strip() == "or" else retarget(line) for line in span
                ]
                if or_joined(expr):
                    sep = next(
                        (line for line in lines if line.strip() == "or"), "  or"
                    )
                    block = [sep] + block
                elif not lines[run[-1]].rstrip().endswith("+"):
                    # the template was the final term of its region: it now needs
                    # a joiner, and the cloned block already ends without one
                    lines[run[-1]] = lines[run[-1]] + " +"
                lines[run[-1] + 1 : run[-1] + 1] = block
            target["expr"] = "\n".join(lines)
            report["terms"] += len(idx)

        for offset, (ti, clone) in enumerate(clones):
            targets.insert(ti + 1 + offset, clone)

    # markdown table row
    if "table_panel" in tmpl_entry:
        pid = tmpl_entry["table_panel"]
        panel = panels(dash).get(pid)
        if panel:
            lines = panel["options"]["content"].split("\n")
            at = next(
                (
                    i
                    for i, label in table_rows(panel)
                    if label == tmpl_entry["table_label"]
                ),
                None,
            )
            if at is None:
                report["warnings"].append(
                    f"panel {pid}: template row '{tmpl_entry['table_label']}' not found; "
                    f"add the table row by hand"
                )
            else:
                cells = ["", f" {table_label} "]
                for rk in RATE_KEYS:
                    v = new_rates.get(rk)
                    cells.append(f" {fmt_price(v) if v is not None else '—'} ")
                cells.append("")
                lines.insert(at + 1, "|".join(cells))
                panel["options"]["content"] = "\n".join(lines)
                report["rows"] += 1
    return report


def remove_model(dash, entry):
    """Delete every trace of a model. Inverse of add_model."""
    matcher = entry["matcher"]
    report = {"targets": 0, "terms": 0, "alt": 0, "rows": 0, "warnings": []}

    for pid, panel in panels(dash).items():
        targets = panel.get("targets") or []
        keep = []
        for target in targets:
            expr = target.get("expr")
            if not isinstance(expr, str):
                keep.append(target)
                continue

            if ALT_RE.search(expr):
                shrunk = alt_remove(expr, matcher)
                if shrunk != expr:
                    target["expr"] = expr = shrunk
                    report["alt"] += 1

            if f'"{matcher}"' not in expr:
                keep.append(target)
                continue

            others = {m.group("matcher") for m in SITE_RE.finditer(expr)} - {matcher}
            if not others:
                report["targets"] += 1  # drop the whole target
                continue

            lines = expr.split("\n")
            idx = model_lines(expr, matcher)
            if idx:
                for run in reversed(contiguous_runs(idx)):
                    lo, hi = run[0], run[-1]
                    if or_joined(expr):
                        # take the paired `or` separator with the block
                        if lo > 0 and lines[lo - 1].strip() == "or":
                            lo -= 1
                        elif hi + 1 < len(lines) and lines[hi + 1].strip() == "or":
                            hi += 1
                    was_final = not lines[hi].rstrip().endswith("+")
                    del lines[lo : hi + 1]
                    if was_final and not or_joined(expr):
                        # the region's new final term must drop its joiner:
                        # scan up from the deletion point, not from the end of
                        # the expr, so a later region is never touched
                        for i in range(lo - 1, -1, -1):
                            if is_price_line(lines[i]):
                                lines[i] = re.sub(r" \+$", "", lines[i])
                                break
                target["expr"] = "\n".join(lines)
                report["terms"] += len(idx)
            keep.append(target)
        if targets:
            panel["targets"] = keep

    if "table_panel" in entry:
        panel = panels(dash).get(entry["table_panel"])
        if panel:
            lines = panel["options"]["content"].split("\n")
            at = next(
                (i for i, label in table_rows(panel) if label == entry["table_label"]),
                None,
            )
            if at is not None:
                del lines[at]
                panel["options"]["content"] = "\n".join(lines)
                report["rows"] += 1
    return report


def write_map_data(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


# -------------------------------------------------------------------- validate


# the untracked-token panels use !~, the cost-per-request divisor uses =~
ALT_RE = re.compile(r'gen_ai_response_model(?:=~|!~)"(?P<alt>\([^"]*\)(?:\|\([^"]*\))+)"')


def split_alternation(alt):
    """Split `(a)|(b)|(c)` into [a, b, c], honouring nested groups.

    A prefix-tolerant matcher contains its own group — `((us|eu)?\\.?anthropic\\..*)`
    — so splitting naively on `|` or on `|(` mangles it.
    """
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(alt):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            parts.append(alt[start:i])
            start = i + 1
    parts.append(alt[start:])
    out = []
    for p in parts:
        p = p.strip()
        if p.startswith("(") and p.endswith(")"):
            p = p[1:-1]  # strip exactly one enclosing layer
        out.append(p)
    return out


def validate(dash, by_matcher, raw=None, baseline=None):
    """Return (errors, warnings, info)."""
    errors, warnings, info = [], [], []

    # every expression must at least balance its parentheses -- a structural
    # edit that breaks one renders as a Grafana parse error, not a wrong number
    for pid, panel in panels(dash).items():
        for _setter, expr, rid in iter_exprs(panel):
            if expr.count("(") != expr.count(")"):
                errors.append(f"panel {pid}/{rid}: unbalanced parentheses in expr")

    if raw is not None and dump(dash) != raw:
        errors.append(
            "serialization drift: json.dumps(indent=2, ensure_ascii=False) does not "
            "reproduce the file byte-for-byte; the file was reformatted by something else"
        )

    card, problems = extract_card(dash)
    errors.extend(problems)

    known = set(by_matcher)
    for matcher in card:
        if matcher not in known:
            errors.append(f"matcher {matcher!r} is priced but missing from model-map.json")
    for matcher in known:
        if matcher not in card:
            errors.append(f"matcher {matcher!r} is in model-map.json but priced nowhere")

    # convention recorded in the map still matches what the file implements
    for matcher, entry in by_matcher.items():
        got = card.get(matcher, {}).get("convention")
        if got and got != entry.get("cache_convention"):
            errors.append(
                f"{entry['key']}: cache convention is '{got}' in the dashboard but "
                f"'{entry.get('cache_convention')}' in model-map.json"
            )

    # net vs isolated target structure: a target that sums input+cache for a model must
    # use the surcharge constant; a target that isolates cache must use the full rate.
    # Scoped per (panel, refId) rather than per panel: a consolidated multi-target stat
    # panel (e.g. panel 252) can legitimately mix a net-cost target with isolated-cache
    # targets, each internally consistent — matching apply_card.new_value's per-site
    # classification.
    per_target = collections.defaultdict(lambda: collections.defaultdict(dict))
    for s in collect_sites(dash):
        per_target[(s.panel_id, s.ref_id)][s.matcher].setdefault(s.tt, set()).add(round(s.value, 6))
    for (pid, rid), models in per_target.items():
        for matcher, tts in models.items():
            entry = by_matcher.get(matcher, {"key": matcher, "cache_convention": "full"})
            if entry.get("cache_convention") != "delta":
                continue
            rates = card[matcher]["rates"]
            in_rate = rates["input"]
            net_panel = "input" in tts
            for rk in CACHE_KEYS:
                if rk not in tts:
                    continue
                full = rates[rk]
                want = full - in_rate if net_panel else full
                for got in tts[rk]:
                    if abs(got - want) > EPS:
                        errors.append(
                            f"panel {pid}/{rid} {entry['key']}.{rk}: constant {got} but a "
                            f"{'net-cost' if net_panel else 'cache-only'} target needs "
                            f"{want:.4f}"
                        )

    # cost reconstruction: dashboard arithmetic must equal the provider rate card
    for matcher, entry in by_matcher.items():
        rates = card[matcher]["rates"]
        r_in, r_out = rates.get("input", 0.0), rates.get("output", 0.0)
        r_cr, r_cw = rates.get("cache_read"), rates.get("cache_write")
        cr, cw, out = 200_000.0, 100_000.0, 50_000.0
        fresh = 700_000.0
        if entry.get("cache_convention") == "delta":
            reported_in = fresh + (cr if r_cr else 0) + (cw if r_cw else 0)
        else:
            reported_in = fresh
        truth = fresh * r_in + out * r_out
        truth += cr * r_cr if r_cr else 0
        truth += cw * r_cw if r_cw else 0
        dash_cost = reported_in * r_in + out * r_out
        if r_cr:
            dash_cost += cr * (r_cr - r_in if entry.get("cache_convention") == "delta" else r_cr)
        if r_cw:
            dash_cost += cw * (r_cw - r_in if entry.get("cache_convention") == "delta" else r_cw)
        if abs(truth - dash_cost) > 1e-6:
            errors.append(
                f"{entry['key']}: net cost reconstruction differs from the rate card "
                f"({dash_cost / 1e6:.6f} vs {truth / 1e6:.6f} per synthetic mix)"
            )

    # savings targets: every term must be a cache_read selector with constant
    # input − cache_read, covering exactly the models that price cache reads
    ps_sv = panels(dash)
    for pid, rid in SAVINGS_SITES:
        panel = ps_sv.get(pid)
        if not panel:
            continue
        for _setter, expr, r in iter_exprs(panel):
            if r != rid:
                continue
            seen = set()
            for m in SITE_RE.finditer(expr):
                mult = MULT_RE.search(expr, m.end())
                if not mult:
                    continue
                matcher, tt = m.group("matcher"), TOKEN_TYPES[m.group("tt")]
                if tt != "cache_read":
                    errors.append(f"panel {pid}/{rid}: savings term for {matcher} uses {tt}, expected input_cache_read")
                    continue
                seen.add(matcher)
                rates = card.get(matcher, {}).get("rates", {})
                if rates.get("input") is None or rates.get("cache_read") is None:
                    errors.append(f"panel {pid}/{rid}: savings term for matcher without input+cache_read pricing: {matcher}")
                    continue
                want = rates["input"] - rates["cache_read"]
                if abs(float(mult.group(1)) - want) > EPS:
                    errors.append(
                        f"panel {pid}/{rid} {matcher}: savings constant {mult.group(1)} "
                        f"!= input−cache_read ({want:.4f})")
            should = {m for m, c in card.items()
                      if c["rates"].get("cache_read") is not None and c["rates"].get("input") is not None}
            if seen and seen != should:
                errors.append(
                    f"panel {pid}/{rid}: savings terms unexpected {sorted(seen - should)}, "
                    f"missing {sorted(should - seen)}")

    # refId collisions: two targets sharing a refId in one panel collide in Grafana
    for pid, panel in panels(dash).items():
        ids = [t.get("refId") for t in (panel.get("targets") or [])]
        dupes = sorted({r for r in ids if ids.count(r) > 1})
        if dupes:
            errors.append(f"panel {pid}: duplicate refId(s) {dupes}")

    # alternation lists (untracked-token panels, cost-per-request divisor)
    for pid, panel in panels(dash).items():
        for _setter, expr, _rid in iter_exprs(panel):
            for m in ALT_RE.finditer(expr):
                listed = set(split_alternation(m.group("alt")))
                missing = known - listed
                extra = listed - known
                if missing or extra:
                    warnings.append(
                        f"panel {pid}: model alternation list out of sync "
                        f"(missing {sorted(missing)}, unexpected {sorted(extra)})"
                    )

    # markdown tables vs PromQL constants
    ps = panels(dash)
    rows = collections.defaultdict(dict)
    for matcher, entry in by_matcher.items():
        if "table_panel" in entry:
            rows[entry["table_panel"]][entry["table_label"]] = matcher
    for provider, pid in TABLE_PANELS.items():
        panel = ps.get(pid)
        if not panel:
            continue
        lines = panel["options"]["content"].split("\n")
        for i, label in table_rows(panel):
            matcher = rows[pid].get(label)
            if not matcher:
                warnings.append(f"panel {pid}: row '{label}' not linked in model-map.json")
                continue
            cells = lines[i].split("|")
            rates = card[matcher]["rates"]
            for offset, rk in enumerate(RATE_KEYS):
                shown = cells[2 + offset].strip() if len(cells) > 2 + offset else ""
                want = rates.get(rk)
                if shown in ("—", "-", ""):
                    if want is not None:
                        errors.append(
                            f"panel {pid} '{label}': {rk} priced at {want} in PromQL but "
                            f"shown as '—' in the table"
                        )
                    continue
                try:
                    got = float(shown.lstrip("$").replace(",", ""))
                except ValueError:
                    warnings.append(f"panel {pid} '{label}': unparsable {rk} cell {shown!r}")
                    continue
                if want is None:
                    errors.append(
                        f"panel {pid} '{label}': table shows {rk} {shown} but PromQL "
                        f"prices no {rk} term"
                    )
                elif abs(got - want) > 1e-9:
                    kind = "rounded" if abs(got - want) <= 0.005 else "WRONG"
                    (warnings if kind == "rounded" else errors).append(
                        f"panel {pid} '{label}': table {rk} {shown} != PromQL "
                        f"{want:.4f} ({kind})"
                    )

    if baseline:
        base = collections.Counter((s.matcher, s.tt) for s in collect_sites(baseline))
        now = collections.Counter((s.matcher, s.tt) for s in collect_sites(dash))
        for k in set(base) | set(now):
            if base[k] != now[k]:
                errors.append(
                    f"price-site count changed for {k[0]}.{k[1]}: {base[k]} -> {now[k]}"
                )

    info.append(f"{len(card)} models, {len(collect_sites(dash))} price sites")
    return errors, warnings, info


# ------------------------------------------------------------------------- cli


def cmd_extract(args):
    _raw, dash = load(args.dashboard)
    card, problems = extract_card(dash)
    _data, by_key, by_matcher = load_map(args.model_map)
    out = {"verified": {}, "rates": {}}
    for matcher in sorted(card, key=matcher_to_key):
        entry = by_matcher.get(matcher, {"key": matcher_to_key(matcher), "provider": "?"})
        out["rates"][entry["key"]] = {
            k: card[matcher]["rates"][k] for k in RATE_KEYS if k in card[matcher]["rates"]
        }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
            fh.write("\n")
        print(f"wrote {args.json}")
    hdr = f"{'model':40s} {'prov':9s} {'conv':6s} {'input':>9s} {'output':>9s} {'cache_r':>9s} {'cache_w':>9s}"
    print(hdr)
    print("-" * len(hdr))
    for matcher in sorted(card, key=lambda m: (guess_provider(m), matcher_to_key(m))):
        entry = by_matcher.get(matcher, {"key": matcher_to_key(matcher), "provider": "?"})
        r = card[matcher]["rates"]
        cells = " ".join(
            f"{r[k]:9.4f}" if k in r else f"{'—':>9s}" for k in RATE_KEYS
        )
        print(
            f"{entry['key']:40s} {entry.get('provider', '?'):9s} "
            f"{card[matcher]['convention']:6s} {cells}"
        )
    for p in problems:
        print(f"PROBLEM: {p}", file=sys.stderr)
    return 1 if problems else 0


def cmd_write_map(args):
    _raw, dash = load(args.dashboard)
    card, problems = extract_card(dash)
    data, unlinked, orphan_rows = build_map(dash, card)
    with open(args.model_map, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print(f"wrote {args.model_map} ({len(data['models'])} models)")
    for k in unlinked:
        print(f"WARN: no markdown table row found for {k}", file=sys.stderr)
    for label in orphan_rows:
        print(f"WARN: table row '{label}' matched no priced model", file=sys.stderr)
    for p in problems:
        print(f"PROBLEM: {p}", file=sys.stderr)
    return 0


def _plan(args):
    _raw, dash = load(args.dashboard)
    current, problems = extract_card(dash)
    _data, by_key, by_matcher = load_map(args.model_map)
    rates, verified = load_proposed(args.proposed)
    proposed, errors = resolve_proposed(rates, by_key, current)
    changes = diff(current, proposed, by_matcher)
    return dash, current, proposed, by_matcher, verified, changes, problems + errors


def print_changes(changes):
    if not changes:
        print("no rate changes: the dashboard already matches the proposed card")
        return
    print(f"{len(changes)} rate change(s):")
    for c in changes:
        old = f"{c['old']:.4f}" if c["old"] is not None else "—"
        note = " (net panels carry the surcharge)" if c["convention"] == "delta" else ""
        print(f"  {c['key']:38s} {c['rate']:11s} {old:>9s} -> {c['new']:.4f}{note}")


def cmd_plan(args):
    _dash, _cur, _prop, _bm, _ver, changes, errors = _plan(args)
    print_changes(changes)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    return 1 if errors else 0


def cmd_apply(args):
    raw, _ = load(args.dashboard)
    dash, current, proposed, by_matcher, verified, changes, errors = _plan(args)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print("refusing to apply", file=sys.stderr)
        return 1
    print_changes(changes)
    if not changes and not args.force:
        return 0
    _b, baseline = load(args.dashboard)
    today = args.date or datetime.date.today().isoformat()
    tables = verified or {}
    if args.stamp_all:
        tables = {p: today for p in TABLE_PANELS}
    report = apply_card(dash, current, proposed, by_matcher, tables, today)
    print(
        f"rewrote {report['sites']} PromQL constant(s), {report['table_rows']} table row(s)"
    )
    for pid, stamp in report["table_dates"]:
        print(f"  panel {pid}: _Last updated: {stamp}_")
    for w in report["warnings"]:
        print(f"WARN: {w}", file=sys.stderr)

    _d, by_key2, by_matcher2 = load_map(args.model_map)
    verrors, vwarn, vinfo = validate(dash, by_matcher2, baseline=baseline)
    for w in vwarn:
        print(f"WARN: {w}", file=sys.stderr)
    if verrors:
        for e in verrors:
            print(f"POST-CHECK FAILED: {e}", file=sys.stderr)
        print("not writing the dashboard", file=sys.stderr)
        return 1
    new_raw = dump(dash)
    if args.dry_run:
        print("dry run: validated, not written")
        return 0
    with open(args.dashboard, "w", encoding="utf-8") as fh:
        fh.write(new_raw)
    print(f"wrote {args.dashboard} ({vinfo[0]})")
    return 0


def _finish(args, dash, by_matcher, baseline, expect_like=None, new_matcher=None):
    """Validate a structural edit, then write unless --dry-run. Returns exit code."""
    errors, warnings, info = validate(dash, by_matcher)
    if expect_like and new_matcher:
        base = collections.Counter((s.matcher, s.tt) for s in collect_sites(baseline))
        now = collections.Counter((s.matcher, s.tt) for s in collect_sites(dash))
        for (matcher, tt), n in base.items():
            if matcher == new_matcher:
                continue
            if now.get((matcher, tt), 0) != n:
                errors.append(
                    f"price-site count changed for an untouched model "
                    f"{matcher}.{tt}: {n} -> {now.get((matcher, tt), 0)}"
                )
        for tt in {t for m, t in base if m == expect_like}:
            want, got = base[(expect_like, tt)], now.get((new_matcher, tt), 0)
            if want != got:
                errors.append(
                    f"new model has {got} {tt} site(s) but its template has {want}: "
                    f"the clone is incomplete"
                )
    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"POST-CHECK FAILED: {e}", file=sys.stderr)
        print("not writing the dashboard", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry run: validated ({info[0]}), not written")
        return 0
    with open(args.dashboard, "w", encoding="utf-8") as fh:
        fh.write(dump(dash))
    print(f"wrote {args.dashboard} ({info[0]})")
    return 0


def cmd_add_model(args):
    _raw, dash = load(args.dashboard)
    _b, baseline = load(args.dashboard)
    card, problems = extract_card(dash)
    data, by_key, by_matcher = load_map(args.model_map)
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return 1
    tmpl = by_key.get(args.like)
    if not tmpl:
        print(
            f"ERROR: unknown template model '{args.like}'; pick one from extract",
            file=sys.stderr,
        )
        return 1
    if args.key in by_key:
        print(f"ERROR: '{args.key}' is already in the dashboard", file=sys.stderr)
        return 1
    new_matcher = promql_to_json_matcher(args.matcher)
    if new_matcher in by_matcher:
        print(f"ERROR: matcher {args.matcher} is already used", file=sys.stderr)
        return 1

    rates = {k: float(v) for k, v in json.loads(args.rates).items()}
    bad = [k for k in rates if k not in RATE_KEYS]
    if bad:
        print(f"ERROR: unknown rate key(s) {bad}", file=sys.stderr)
        return 1
    tmpl_rates = card[tmpl["matcher"]]["rates"]
    if set(rates) != set(tmpl_rates):
        print(
            f"ERROR: template '{args.like}' prices {sorted(tmpl_rates)} but you gave "
            f"{sorted(rates)}. The clone copies the template's structure, so the token "
            f"types must match exactly — pick a template with the same shape.",
            file=sys.stderr,
        )
        return 1
    lossy_keys = [k for k, v in rates.items() if lossy(v)]
    if lossy_keys:
        print(f"ERROR: {lossy_keys} need more than 4 decimals", file=sys.stderr)
        return 1

    label = args.table_label or (
        f"{args.key} (Bedrock)" if tmpl.get("table_label", "").endswith("(Bedrock)")
        else args.key
    )
    print(
        f"cloning '{args.like}' -> '{args.key}'\n"
        f"  matcher   {args.matcher}   (JSON {new_matcher})\n"
        f"  provider  {tmpl['provider']}   cache convention {tmpl.get('cache_convention')}\n"
        f"  table row '{label}' in panel {tmpl.get('table_panel')}"
    )
    report = add_model(dash, tmpl, args.key, new_matcher, rates, label, card)
    print(
        f"added {report['targets']} target(s), {report['terms']} term block(s), "
        f"{report['alt']} alternation entr(ies), {report['rows']} table row(s)"
    )
    for w in report["warnings"]:
        print(f"WARN: {w}", file=sys.stderr)

    entry = {
        "key": args.key,
        "provider": tmpl["provider"],
        "matcher": new_matcher,
        "cache_convention": tmpl.get("cache_convention", "full"),
    }
    if report["rows"]:
        entry["table_panel"] = tmpl["table_panel"]
        entry["table_label"] = label
    data["models"].append(entry)
    data["models"].sort(key=lambda m: matcher_to_key(m["matcher"]))
    by_matcher = {m["matcher"]: m for m in data["models"]}

    rc = _finish(args, dash, by_matcher, baseline, tmpl["matcher"], new_matcher)
    if rc == 0 and not args.dry_run:
        write_map_data(args.model_map, data)
        print(f"updated {args.model_map}")
    return rc


def cmd_remove_model(args):
    _raw, dash = load(args.dashboard)
    _b, baseline = load(args.dashboard)
    data, by_key, _bm = load_map(args.model_map)
    entry = by_key.get(args.key)
    if not entry:
        print(f"ERROR: unknown model '{args.key}'", file=sys.stderr)
        return 1
    report = remove_model(dash, entry)
    print(
        f"removed {report['targets']} target(s), {report['terms']} term block(s), "
        f"{report['alt']} alternation entr(ies), {report['rows']} table row(s)"
    )
    data["models"] = [m for m in data["models"] if m["key"] != args.key]
    by_matcher = {m["matcher"]: m for m in data["models"]}
    rc = _finish(args, dash, by_matcher, baseline)
    if rc == 0 and not args.dry_run:
        write_map_data(args.model_map, data)
        print(f"updated {args.model_map}")
    return rc


def cmd_validate(args):
    raw, dash = load(args.dashboard)
    _data, _bk, by_matcher = load_map(args.model_map)
    errors, warnings, info = validate(dash, by_matcher, raw=raw)
    for i in info:
        print(i)
    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    print("FAIL" if errors else "OK")
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dashboard", default=DEFAULT_DASHBOARD)
    ap.add_argument("--model-map", default=DEFAULT_MAP)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help="print the rate card the dashboard implements")
    p.add_argument("--json", help="also write a proposed-card skeleton here")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("write-map", help="regenerate model-map.json from the dashboard")
    p.set_defaults(func=cmd_write_map)

    p = sub.add_parser("plan", help="diff a proposed card against the dashboard")
    p.add_argument("--proposed", required=True)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="rewrite constants and tables from a proposed card")
    p.add_argument("--proposed", required=True)
    p.add_argument("--date", help="override the _Last updated:_ stamp (YYYY-MM-DD)")
    p.add_argument(
        "--stamp-all",
        action="store_true",
        help="stamp every changed table, not just providers listed under 'verified'",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="run even with no rate changes")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser(
        "add-model", help="clone an existing model to introduce a new one"
    )
    p.add_argument("--like", required=True, help="template model key to clone")
    p.add_argument("--key", required=True, help="new model key, e.g. gpt-5.6-luna")
    p.add_argument(
        "--matcher",
        required=True,
        help=r"PromQL regex, single-backslash form: 'gpt-5\.6-luna.*'",
    )
    p.add_argument(
        "--rates",
        required=True,
        help='JSON rates, e.g. \'{"input":1.0,"output":6.0,"cache_read":0.10,"cache_write":1.25}\'',
    )
    p.add_argument("--table-label", help="markdown table row label (default: --key)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_add_model)

    p = sub.add_parser("remove-model", help="delete a model everywhere (inverse of add)")
    p.add_argument("--key", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_remove_model)

    p = sub.add_parser("validate", help="check every pricing invariant")
    p.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
