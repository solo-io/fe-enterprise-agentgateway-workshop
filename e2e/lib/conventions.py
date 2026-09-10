#!/usr/bin/env python3
"""conventions.py — lint the lab corpus's copy-paste conventions.

The docs deliberately duplicate standard blocks so a reader can follow one
page without chasing includes (style-guide.md). This linter makes that
duplication safe: identical everywhere, or reported.

Rules:
  links       every relative .md link in README/tracks/labs resolves
  gateway-ip  every `export GATEWAY_IP=$(kubectl get svc ...` line is
              byte-identical to the canonical block (style-guide.md §6)
  cleanup     every lab has a `## Cleanup` or `### Cleanup` section (a
              per-option lab keeps its cleanups under H3; allowlist below)
  eagb        no bare `kind: AgentgatewayBackend` — Enterprise CRDs only
              (allowlist: the Kyverno lab's deliberate negative example)

Usage: conventions.py <repo_root>     exit 1 on any violation
"""
import os
import re
import sys

import labdoc

CANONICAL_GATEWAY_IP = (
    "export GATEWAY_IP=$(kubectl get svc -n agentgateway-system "
    "--selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy "
    "-o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}"
    "{.items[*].status.loadBalancer.ingress[0].hostname}')"
)

# Labs that legitimately have no Cleanup section (reference/decision docs,
# installers whose teardown is the whole-cluster cleanup script).
CLEANUP_ALLOW = {
    "labs/installation/image-list.md",
    "labs/installation/system-requirements.md",
    "labs/installation/airgap/001-airgap.md",
    "labs/installation/openshift/001-set-up-enterprise-agentgateway-ocp.md",
    "labs/installation/openshift/002-set-up-monitoring-tools-ocp.md",
    "labs/observability/production-observability-alerting-and-scaling.md",
    "labs/platform-engineering/networking-architecture.md",
    "labs/upgrades/migrate-v2026.5.x-to-v2026.7.x.md",
}

# The Kyverno lab shows a bare AgentgatewayBackend as the resource its
# policy REJECTS — that one is the point, not drift.
EAGB_ALLOW = {"labs/platform-engineering/kyverno-admission-control.md"}

# Labs whose GATEWAY_IP block legitimately differs from the canonical form
# (a different Gateway/selector, not accidental drift). One-line reason each.
GATEWAY_IP_ALLOW = {
    # deploys its own Gateway "agw-llm-ops", not the shared agentgateway-proxy
    "labs/platform-engineering/centralized-llm-ops-helm-chart.md",
    # deploys its own Gateway "agw-platform", not the shared agentgateway-proxy
    "labs/platform-engineering/platform-and-developer-helm-charts-mcp.md",
    # deploys its own Gateway "agentgateway-sni" for the SNI-matching listener
    "labs/security/sni-matching.md",
}

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
GW_PREFIX = "export GATEWAY_IP=$(kubectl get svc"
EAGB_BARE = re.compile(r"kind:\s*AgentgatewayBackend\b")


def md_files(root):
    out = ["README.md"]
    for base in ("tracks", "labs"):
        for dirpath, _dirs, files in os.walk(os.path.join(root, base)):
            for f in sorted(files):
                if f.endswith(".md"):
                    out.append(os.path.relpath(os.path.join(dirpath, f), root))
    for f in sorted(os.listdir(root)):
        if re.match(r"00\d-.*\.md$", f):
            out.append(f)
    return out


def check_links(root, files):
    errs = []
    for rel in files:
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        for target in LINK.findall(text):
            target = target.split("#")[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not target.endswith(".md"):
                continue
            resolved = os.path.normpath(
                os.path.join(root, os.path.dirname(rel), target))
            if not os.path.exists(resolved):
                errs.append(f"{rel}: broken link -> {target}")
    return errs


def check_gateway_ip(root, files, allow=GATEWAY_IP_ALLOW):
    errs = []
    for rel in files:
        if rel in allow:
            continue
        for section in labdoc.parse_lab(os.path.join(root, rel)):
            for block in section["blocks"]:
                for line in block.splitlines():
                    if line.startswith(GW_PREFIX) and line != CANONICAL_GATEWAY_IP:
                        errs.append(
                            f"{rel} [{section['heading']}]: GATEWAY_IP block "
                            f"drifted from the canonical form (style-guide §6)")
    return errs


def check_cleanup(root, files, allow=CLEANUP_ALLOW):
    errs = []
    for rel in files:
        if not rel.startswith("labs/") or rel in allow:
            continue
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        if not re.search(r"^#{2,3}\s+Cleanup\s*$", text, re.MULTILINE):
            errs.append(f"{rel}: no `## Cleanup` section (style-guide §10)")
    return errs


def check_eagb(root, files, allow=EAGB_ALLOW):
    errs = []
    for rel in files:
        if rel in allow:
            continue
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        for m in EAGB_BARE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            errs.append(f"{rel}:{line}: bare `kind: AgentgatewayBackend` — "
                        f"use EnterpriseAgentgatewayBackend")
    return errs


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    files = md_files(root)
    errs = (check_links(root, files) + check_gateway_ip(root, files)
            + check_cleanup(root, files) + check_eagb(root, files))
    for e in errs:
        print(f"  ✗ {e}")
    print(f"conventions: {len(files)} file(s), {len(errs)} violation(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
