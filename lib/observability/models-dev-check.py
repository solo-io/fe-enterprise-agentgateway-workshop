#!/usr/bin/env python3
"""models-dev-check.py — compare the dashboard's rate card with models.dev.

models.dev (https://models.dev/api.json) is the pricing source of record for
this dashboard. This script reports drift; it never edits the dashboard.
Feed its proposed card to `dash_prices.py plan/apply` to act on it.

Usage:
  models-dev-check.py [--api-file cached-api.json] [--out proposed-card.json]
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API_URL = "https://models.dev/api.json"

# dashboard model name -> (models.dev provider key, models.dev model id)
# Mock rows have no public price and are intentionally absent.
MODELS_DEV_IDS = {
    "gpt-5":            ("openai", "gpt-5"),
    "gpt-5-mini":       ("openai", "gpt-5-mini"),
    "gpt-5-nano":       ("openai", "gpt-5-nano"),
    "gpt-5.4":          ("openai", "gpt-5.4"),
    "gpt-5.4-mini":     ("openai", "gpt-5.4-mini"),
    "gpt-5.4-nano":     ("openai", "gpt-5.4-nano"),
    "gpt-5.6-luna":     ("openai", "gpt-5.6-luna"),
    "gpt-5.6-sol":      ("openai", "gpt-5.6-sol"),
    "gpt-5.6-terra":    ("openai", "gpt-5.6-terra"),
    "claude-fable-5":   ("anthropic", "claude-fable-5"),
    "claude-haiku-4-5": ("anthropic", "claude-haiku-4-5"),
    "claude-opus-4-7":  ("anthropic", "claude-opus-4-7"),
    "claude-opus-4-8":  ("anthropic", "claude-opus-4-8"),
    "claude-opus-5":    ("anthropic", "claude-opus-5"),
    "claude-sonnet-4-5": ("anthropic", "claude-sonnet-4-5"),
    "claude-sonnet-4-6": ("anthropic", "claude-sonnet-4-6"),
    "claude-sonnet-5":  ("anthropic", "claude-sonnet-5"),
    "us.anthropic.claude-haiku-4-5":  ("amazon-bedrock", "us.anthropic.claude-haiku-4-5"),
    "us.anthropic.claude-opus-4-7":   ("amazon-bedrock", "us.anthropic.claude-opus-4-7"),
    "us.anthropic.claude-sonnet-4-6": ("amazon-bedrock", "us.anthropic.claude-sonnet-4-6"),
    "anthropic.claude-fable-5":  ("amazon-bedrock", "anthropic.claude-fable-5"),
    "anthropic.claude-opus-4-8": ("amazon-bedrock", "anthropic.claude-opus-4-8"),
    "anthropic.claude-opus-5":   ("amazon-bedrock", "anthropic.claude-opus-5"),
    "anthropic.claude-sonnet-5": ("amazon-bedrock", "anthropic.claude-sonnet-5"),
    "meta.llama3-1-8b":          ("amazon-bedrock", "meta.llama3-1-8b"),
    "mistral.voxtral-mini-3b":   ("amazon-bedrock", "mistral.voxtral-mini-3b"),
}
TERMS = ("input", "output", "cache_read", "cache_write")


def load_api(path):
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    # models.dev returns 403 to urllib's default User-Agent; a browser-like
    # one is required for the live fetch to succeed.
    req = urllib.request.Request(API_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-file", help="cached copy of api.json (offline/tests)")
    ap.add_argument("--out", help="write a proposed card for dash_prices.py plan")
    args = ap.parse_args()

    card = json.load(open(os.path.join(HERE, "pricing.json"), encoding="utf-8"))
    api = load_api(args.api_file)

    drift, unmapped, missing = {}, [], []
    for model, rates in sorted(card["rates"].items()):
        ref = MODELS_DEV_IDS.get(model)
        if ref is None:
            unmapped.append(model)
            continue
        prov, mid = ref
        entry = api.get(prov, {}).get("models", {}).get(mid)
        if entry is None or "cost" not in entry:
            missing.append(f"{model} ({prov}/{mid})")
            continue
        cost = entry["cost"]
        diffs = {}
        for term in TERMS:
            ours, theirs = rates.get(term), cost.get(term)
            if ours is None and theirs is None:
                continue
            if ours is None or theirs is None or abs(ours - float(theirs)) > 1e-9:
                diffs[term] = {"dashboard": ours, "models.dev": theirs}
        if diffs:
            drift[model] = diffs

    for model, diffs in drift.items():
        print(f"DRIFT {model}")
        for term, d in diffs.items():
            print(f"    {term:<12} dashboard={d['dashboard']}  models.dev={d['models.dev']}")
    for m in unmapped:
        print(f"UNMAPPED {m} (no models.dev id — mock/internal row)")
    for m in missing:
        print(f"NOT-ON-MODELS.DEV {m}")
    print(f"\n{len(card['rates'])} models: {len(drift)} drifted, "
          f"{len(unmapped)} unmapped, {len(missing)} absent upstream")

    if args.out and drift:
        proposed = {"verified": {}, "rates": {}}
        for model, diffs in drift.items():
            prov, mid = MODELS_DEV_IDS[model]
            cost = api[prov]["models"][mid]["cost"]
            proposed["rates"][model] = {t: float(cost[t]) for t in TERMS if t in cost}
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(proposed, fh, indent=2)
        print(f"proposed card -> {args.out} (feed to dash_prices.py plan)")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
