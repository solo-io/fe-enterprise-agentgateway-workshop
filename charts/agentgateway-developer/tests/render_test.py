#!/usr/bin/env python3
"""Render tests for agentgateway-developer. Run: python3 charts/agentgateway-developer/tests/render_test.py"""
import subprocess, sys, os, yaml

CHART = os.path.join(os.path.dirname(__file__), "..")
BASE = ["team=team-alpha",
        "endpoints[0].name=tools", "endpoints[0].type=mcp", "endpoints[0].path=/mcp",
        "endpoints[0].targets[0].name=arxiv",
        "endpoints[0].targets[0].host=mcp-airxiv.team-alpha.svc.cluster.local",
        "endpoints[0].targets[0].port=8080",
        "endpoints[0].targets[0].protocol=StreamableHTTP"]

def render(*sets, expect_fail=False, set_json=None):
    cmd = ["helm", "template", "team-alpha", CHART, "--namespace", "team-alpha"]
    for s in sets:
        cmd += ["--set", s]
    for s in (set_json or []):
        cmd += ["--set-json", s]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        assert out.returncode != 0, f"expected failure, got:\n{out.stdout}"
        return out.stderr
    if out.returncode != 0:
        raise AssertionError(f"helm template failed:\n{out.stderr}")
    return [d for d in yaml.safe_load_all(out.stdout) if d]

def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]

def test_mcp_endpoint():
    docs = render("team=team-alpha",
                  "endpoints[0].name=tools", "endpoints[0].type=mcp",
                  "endpoints[0].path=/mcp", "endpoints[0].toolMode=search",
                  "endpoints[0].targets[0].name=everything",
                  "endpoints[0].targets[0].host=mcp-everything.team-alpha.svc.cluster.local",
                  "endpoints[0].targets[0].port=3001",
                  "endpoints[0].targets[0].protocol=StreamableHTTP")
    routes = by_kind(docs, "HTTPRoute")
    assert len(routes) == 1
    r = routes[0]
    assert r["metadata"]["name"] == "team-alpha-tools"
    assert r["metadata"]["labels"]["team"] == "team-alpha"
    assert "parentRefs" not in r["spec"], "child routes must NOT set parentRefs (would attach directly to the gateway, bypassing delegation)"
    assert r["spec"]["rules"][0]["matches"][0]["path"]["value"] == "/teams/team-alpha/mcp"
    assert r["spec"]["rules"][0]["backendRefs"][0] == {
        "name": "team-alpha-tools", "group": "enterpriseagentgateway.solo.io",
        "kind": "EnterpriseAgentgatewayBackend"}
    bes = by_kind(docs, "EnterpriseAgentgatewayBackend")
    assert len(bes) == 1
    be = bes[0]
    assert be["metadata"]["labels"]["team"] == "team-alpha"
    mcp = be["spec"]["entMcp"]
    assert mcp["toolMode"] == "Search"
    t = mcp["targets"][0]
    assert t["name"] == "everything"
    assert t["static"] == {"host": "mcp-everything.team-alpha.svc.cluster.local",
                           "port": 3001, "protocol": "StreamableHTTP"}

def test_mcp_multiple_targets():
    docs = render("team=team-alpha",
                  "endpoints[0].name=tools", "endpoints[0].type=mcp", "endpoints[0].path=/mcp",
                  "endpoints[0].targets[0].name=arxiv",
                  "endpoints[0].targets[0].host=mcp-airxiv.team-alpha.svc.cluster.local",
                  "endpoints[0].targets[0].port=8080",
                  "endpoints[0].targets[1].name=github",
                  "endpoints[0].targets[1].host=github-mcp.team-alpha.svc.cluster.local",
                  "endpoints[0].targets[1].port=9090",
                  "endpoints[0].targets[1].protocol=StreamableHTTP")
    be = by_kind(docs, "EnterpriseAgentgatewayBackend")[0]
    targets = be["spec"]["entMcp"]["targets"]
    assert len(targets) == 2
    assert targets[0]["name"] == "arxiv"
    assert targets[1]["name"] == "github" and targets[1]["static"]["protocol"] == "StreamableHTTP"

def test_no_policy_kind_ever_rendered():
    # This chart no longer owns any traffic/frontend policy, and (post-strip) no
    # backend-scoped policy either -- guardrails and per-provider auth policies
    # were LLM-only and are gone. No EnterpriseAgentgatewayPolicy should render.
    docs = render(*BASE)
    assert by_kind(docs, "EnterpriseAgentgatewayPolicy") == []

def test_schema_rejects_traffic_policy_field():
    err = render(*BASE, "endpoints[0].rateLimit.tokensPerMinute=999999", expect_fail=True)
    err2 = render(*BASE, "hostname=evil.example.com", expect_fail=True)

def test_team_required():
    render("endpoints[0].name=tools", "endpoints[0].type=mcp", "endpoints[0].path=/mcp",
           "endpoints[0].targets[0].name=arxiv",
           "endpoints[0].targets[0].host=mcp-airxiv.team-alpha.svc.cluster.local",
           "endpoints[0].targets[0].port=8080",
           expect_fail=True)

def test_mcp_missing_targets_fails_cleanly():
    err = render("team=team-alpha",
                 "endpoints[0].name=tools", "endpoints[0].type=mcp",
                 "endpoints[0].path=/mcp", expect_fail=True)
    assert "targets" in err, f"expected schema error naming targets, got:\n{err}"

def test_schema_rejects_empty_targets():
    err = render("team=team-alpha",
                 "endpoints[0].name=tools", "endpoints[0].type=mcp",
                 "endpoints[0].path=/mcp",
                 set_json=["endpoints[0].targets=[]"],
                 expect_fail=True)
    assert "targets" in err and "minItems" in err, \
        f"expected schema error naming targets/minItems, got:\n{err}"

def test_schema_rejects_llm_type():
    err = render(*BASE, "endpoints[0].type=llm", expect_fail=True)
    assert "/endpoints/0/type" in err and "mcp" in err, \
        f"expected schema error rejecting the non-mcp type, got:\n{err}"

def test_schema_rejects_provider_field():
    err = render(*BASE, "endpoints[0].provider=openai", expect_fail=True)
    assert "provider" in err, f"expected schema error naming provider, got:\n{err}"

def test_schema_rejects_auth_field():
    err = render(*BASE, "endpoints[0].auth.secretRef=openai-creds", expect_fail=True)
    assert "auth" in err, f"expected schema error naming auth, got:\n{err}"

def test_schema_rejects_failover_field():
    err = render(*BASE, "endpoints[0].failover[0].provider=anthropic",
                 "endpoints[0].failover[0].secretRef=anthropic-creds", expect_fail=True)
    assert "failover" in err, f"expected schema error naming failover, got:\n{err}"

def test_schema_rejects_guardrails_field():
    err = render(*BASE, "endpoints[0].guardrails.promptGuard.request[0].regex.action=Reject",
                 "endpoints[0].guardrails.promptGuard.request[0].regex.matches[0]=badword",
                 expect_fail=True)
    assert "guardrails" in err, f"expected schema error naming guardrails, got:\n{err}"

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
