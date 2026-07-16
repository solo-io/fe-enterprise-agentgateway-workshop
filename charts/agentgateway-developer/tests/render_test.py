#!/usr/bin/env python3
"""Render tests for agentgateway-developer. Run: python3 charts/agentgateway-developer/tests/render_test.py"""
import subprocess, sys, os, yaml

CHART = os.path.join(os.path.dirname(__file__), "..")
BASE = ["team=team-alpha",
        "endpoints[0].name=chat", "endpoints[0].type=llm", "endpoints[0].provider=openai",
        "endpoints[0].model=gpt-4o-mini", "endpoints[0].path=/chat",
        "endpoints[0].auth.secretRef=openai-creds"]

def render(*sets, expect_fail=False):
    cmd = ["helm", "template", "team-alpha", CHART, "--namespace", "team-alpha"]
    for s in sets:
        cmd += ["--set", s]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        assert out.returncode != 0, f"expected failure, got:\n{out.stdout}"
        return out.stderr
    if out.returncode != 0:
        raise AssertionError(f"helm template failed:\n{out.stderr}")
    return [d for d in yaml.safe_load_all(out.stdout) if d]

def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]

def test_llm_endpoint():
    docs = render(*BASE)
    routes = by_kind(docs, "HTTPRoute")
    assert len(routes) == 1
    r = routes[0]
    assert r["metadata"]["name"] == "team-alpha-chat"
    assert r["metadata"]["labels"]["team"] == "team-alpha"
    assert "parentRefs" not in r["spec"], "child routes must NOT set parentRefs (would attach directly to the gateway, bypassing delegation)"
    rule = r["spec"]["rules"][0]
    assert rule["matches"][0]["path"] == {"type": "PathPrefix", "value": "/teams/team-alpha/chat"}
    assert rule["backendRefs"][0] == {
        "name": "team-alpha-chat", "group": "enterpriseagentgateway.solo.io",
        "kind": "EnterpriseAgentgatewayBackend"}
    bes = by_kind(docs, "EnterpriseAgentgatewayBackend")
    assert len(bes) == 1
    be = bes[0]
    assert be["metadata"]["labels"]["team"] == "team-alpha"
    assert be["spec"]["ai"]["provider"]["openai"] == {"model": "gpt-4o-mini"}
    assert be["spec"]["policies"]["auth"] == {"secretRef": {"name": "openai-creds"}}

def test_llm_host_override_and_passthrough():
    docs = render("team=team-alpha",
                  "endpoints[0].name=mock", "endpoints[0].type=llm",
                  "endpoints[0].provider=openai", "endpoints[0].model=mock-gpt-4o",
                  "endpoints[0].path=/mock",
                  "endpoints[0].host=mock-gpt-4o-svc.team-alpha.svc.cluster.local",
                  "endpoints[0].port=8000", "endpoints[0].apiPath=/v1/chat/completions",
                  "endpoints[0].auth.passthrough=true")
    be = by_kind(docs, "EnterpriseAgentgatewayBackend")[0]
    prov = be["spec"]["ai"]["provider"]
    assert prov["host"] == "mock-gpt-4o-svc.team-alpha.svc.cluster.local"
    assert prov["port"] == 8000
    assert prov["path"] == "/v1/chat/completions"
    assert be["spec"]["policies"]["auth"] == {"passthrough": {}}

def test_no_policy_kinds_ever_rendered():
    # backend-scoped policy sections (backend.auth for failover, backend.ai guardrails)
    # are allowed; traffic/frontend sections must never render.
    docs = render(*BASE,
                  "endpoints[0].failover[0].provider=anthropic",
                  "endpoints[0].failover[0].secretRef=anthropic-creds",
                  "endpoints[0].guardrails.promptGuard.request[0].regex.action=Reject",
                  "endpoints[0].guardrails.promptGuard.request[0].regex.matches[0]=badword")
    pols = by_kind(docs, "EnterpriseAgentgatewayPolicy")
    assert pols, "expected backend policies to be rendered for this values set"
    for d in pols:
        assert "traffic" not in d["spec"] and "frontend" not in d["spec"]

def test_schema_rejects_traffic_policy_field():
    err = render(*BASE, "endpoints[0].rateLimit.tokensPerMinute=999999", expect_fail=True)
    err2 = render(*BASE, "hostname=evil.example.com", expect_fail=True)

def test_team_required():
    render("endpoints[0].name=chat", "endpoints[0].type=llm",
           "endpoints[0].provider=openai", "endpoints[0].path=/chat",
           "endpoints[0].auth.secretRef=x", expect_fail=True)

def test_llm_missing_provider_fails_cleanly():
    err = render("team=team-alpha",
                 "endpoints[0].name=chat", "endpoints[0].type=llm",
                 "endpoints[0].path=/chat", "endpoints[0].auth.secretRef=x",
                 expect_fail=True)
    assert "provider" in err, f"expected schema error naming provider, got:\n{err}"

def test_llm_missing_auth_fails_cleanly():
    err = render("team=team-alpha",
                 "endpoints[0].name=chat", "endpoints[0].type=llm",
                 "endpoints[0].provider=openai", "endpoints[0].path=/chat",
                 expect_fail=True)
    assert "auth" in err, f"expected schema error naming auth, got:\n{err}"

def test_failover_groups():
    docs = render("team=team-alpha",
                  "endpoints[0].name=chat", "endpoints[0].type=llm",
                  "endpoints[0].provider=openai", "endpoints[0].model=gpt-4o-mini",
                  "endpoints[0].path=/chat", "endpoints[0].auth.secretRef=openai-creds",
                  "endpoints[0].failover[0].provider=anthropic",
                  "endpoints[0].failover[0].model=claude-sonnet-5",
                  "endpoints[0].failover[0].secretRef=anthropic-creds")
    be = by_kind(docs, "EnterpriseAgentgatewayBackend")[0]
    groups = be["spec"]["ai"]["groups"]
    assert len(groups) == 2
    p0 = groups[0]["providers"][0]
    assert p0["name"] == "primary" and p0["openai"]["model"] == "gpt-4o-mini"
    p1 = groups[1]["providers"][0]
    assert p1["name"] == "failover-1" and p1["anthropic"]["model"] == "claude-sonnet-5"
    assert "policies" not in be["spec"], "failover backends use per-provider auth policies"
    pols = by_kind(docs, "EnterpriseAgentgatewayPolicy")
    auths = {p["spec"]["targetRefs"][0]["sectionName"]: p["spec"]["backend"]["auth"] for p in pols
             if "auth" in p["spec"].get("backend", {})}
    assert auths["primary"] == {"secretRef": {"name": "openai-creds"}}
    assert auths["failover-1"] == {"secretRef": {"name": "anthropic-creds"}}
    for p in pols:
        tr = p["spec"]["targetRefs"][0]
        assert tr["kind"] == "EnterpriseAgentgatewayBackend" and tr["name"] == "team-alpha-chat"

def test_guardrails_policy():
    docs = render(*BASE, "endpoints[0].guardrails.promptGuard.request[0].regex.action=Reject",
                  "endpoints[0].guardrails.promptGuard.request[0].regex.matches[0]=badword")
    pols = [p for p in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "ai" in p["spec"].get("backend", {})]
    assert len(pols) == 1
    p = pols[0]
    assert p["spec"]["targetRefs"][0] == {
        "group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "team-alpha-chat"}
    guard = p["spec"]["backend"]["ai"]["promptGuard"]["request"][0]["regex"]
    assert guard["action"] == "Reject" and guard["matches"] == ["badword"]

def test_mcp_endpoint():
    docs = render("team=team-alpha",
                  "endpoints[0].name=tools", "endpoints[0].type=mcp",
                  "endpoints[0].path=/mcp", "endpoints[0].toolMode=search",
                  "endpoints[0].targets[0].name=everything",
                  "endpoints[0].targets[0].host=mcp-everything.team-alpha.svc.cluster.local",
                  "endpoints[0].targets[0].port=3001",
                  "endpoints[0].targets[0].protocol=StreamableHTTP")
    be = by_kind(docs, "EnterpriseAgentgatewayBackend")[0]
    mcp = be["spec"]["entMcp"]
    assert mcp["toolMode"] == "Search"
    t = mcp["targets"][0]
    assert t["name"] == "everything"
    assert t["static"] == {"host": "mcp-everything.team-alpha.svc.cluster.local",
                           "port": 3001, "protocol": "StreamableHTTP"}
    r = by_kind(docs, "HTTPRoute")[0]
    assert r["spec"]["rules"][0]["matches"][0]["path"]["value"] == "/teams/team-alpha/mcp"

def test_failover_missing_secret_fails_cleanly():
    err = render("team=team-alpha",
                 "endpoints[0].name=chat", "endpoints[0].type=llm",
                 "endpoints[0].provider=openai", "endpoints[0].path=/chat",
                 "endpoints[0].auth.secretRef=openai-creds",
                 "endpoints[0].failover[0].provider=anthropic",
                 expect_fail=True)
    assert "failover" in err, f"expected required-error naming failover, got:\n{err}"

def test_mcp_missing_targets_fails_cleanly():
    err = render("team=team-alpha",
                 "endpoints[0].name=tools", "endpoints[0].type=mcp",
                 "endpoints[0].path=/mcp", expect_fail=True)
    assert "targets" in err, f"expected schema error naming targets, got:\n{err}"

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
