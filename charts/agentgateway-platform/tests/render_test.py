#!/usr/bin/env python3
"""Render tests for agentgateway-platform. Run: python3 charts/agentgateway-platform/tests/render_test.py"""
import subprocess, sys, os, yaml

CHART = os.path.join(os.path.dirname(__file__), "..")

def render(*sets, values=None):
    cmd = ["helm", "template", "test-release", CHART, "--namespace", "agentgateway-system"]
    for s in sets:
        cmd += ["--set", s]
    if values:
        cmd += ["--values", values]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"helm template failed:\n{out.stderr}")
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    return docs

def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]

def test_default_render():
    docs = render()
    gws = by_kind(docs, "Gateway")
    assert len(gws) == 1, f"expected 1 Gateway, got {len(gws)}"
    gw = gws[0]
    assert gw["metadata"]["name"] == "agentgateway-proxy"
    assert gw["spec"]["gatewayClassName"] == "enterprise-agentgateway"
    ports = [l["port"] for l in gw["spec"]["listeners"]]
    assert ports == [8080], f"default listeners should be [8080], got {ports}"
    pref = gw["spec"]["infrastructure"]["parametersRef"]
    assert pref["name"] == "agentgateway-proxy-config"
    params = by_kind(docs, "EnterpriseAgentgatewayParameters")
    assert len(params) == 1
    p = params[0]["spec"]
    assert p["logging"]["level"] == "info"
    assert p["deployment"]["spec"]["replicas"] == 2
    assert p["podDisruptionBudget"] == {"spec": {"minAvailable": 1}}
    assert p["shutdown"] == {"min": 10, "max": 60}
    assert p["service"]["spec"]["type"] == "LoadBalancer"

def test_https_listener():
    docs = render("gateway.listeners.https.enabled=true")
    gw = by_kind(docs, "Gateway")[0]
    https = [l for l in gw["spec"]["listeners"] if l["protocol"] == "HTTPS"]
    assert len(https) == 1
    assert https[0]["tls"]["certificateRefs"][0]["name"] == "gateway-tls"

def test_gateway_name_propagates():
    docs = render("gateway.name=agw-platform")
    gw = by_kind(docs, "Gateway")[0]
    assert gw["metadata"]["name"] == "agw-platform"
    assert gw["spec"]["infrastructure"]["parametersRef"]["name"] == "agw-platform-config"

def test_access_log_default_on():
    docs = render()
    pols = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "accessLog" in d["spec"].get("frontend", {})]
    assert len(pols) == 1
    p = pols[0]
    assert p["spec"]["targetRefs"][0] == {
        "group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "agentgateway-proxy"}
    attrs = p["spec"]["frontend"]["accessLog"]["attributes"]["add"]
    assert {"name": "llm.streaming", "expression": "llm.streaming"} in attrs

def test_tracing_off_by_default_on_when_enabled():
    docs = render()
    assert not [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
                if "tracing" in d["spec"].get("frontend", {})]
    docs = render("observability.tracing.enabled=true",
                  "observability.tracing.backendRef.name=otel-collector",
                  "observability.tracing.backendRef.namespace=telemetry")
    pols = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "tracing" in d["spec"].get("frontend", {})]
    assert len(pols) == 1
    tr = pols[0]["spec"]["frontend"]["tracing"]
    assert tr["backendRef"] == {"name": "otel-collector", "namespace": "telemetry", "port": 4317}
    assert tr["protocol"] == "GRPC"

def test_metrics_podmonitor():
    docs = render()
    assert not by_kind(docs, "PodMonitor"), "metrics should be off by default"
    docs = render("observability.metrics.enabled=true")
    pms = by_kind(docs, "PodMonitor")
    assert len(pms) == 1
    pm = pms[0]["spec"]
    assert pm["podMetricsEndpoints"] == [{"port": "metrics"}]
    assert pm["selector"]["matchLabels"] == {"app.kubernetes.io/name": "agentgateway-proxy"}

def test_jwt_remote_jwks():
    docs = render("security.jwt.enabled=true",
                  "security.jwt.issuer=https://idp.example.com/",
                  "security.jwt.jwks.host=idp.example.com",
                  "security.jwt.jwks.path=/.well-known/jwks.json")
    jwks = [d for d in by_kind(docs, "EnterpriseAgentgatewayBackend")
            if d["metadata"]["name"] == "agentgateway-proxy-jwks"]
    assert len(jwks) == 1
    assert jwks[0]["spec"]["static"] == {"host": "idp.example.com", "port": 443}
    assert jwks[0]["spec"]["policies"]["tls"] == {}
    pols = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "jwtAuthentication" in d["spec"].get("traffic", {})]
    assert len(pols) == 1
    prov = pols[0]["spec"]["traffic"]["jwtAuthentication"]["providers"][0]
    assert prov["issuer"] == "https://idp.example.com/"
    # leading slash must be stripped
    assert prov["jwks"]["remote"]["jwksPath"] == ".well-known/jwks.json"
    assert prov["jwks"]["remote"]["backendRef"]["name"] == "agentgateway-proxy-jwks"

def test_jwt_inline_jwks():
    import json, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write('{"keys":[{"kid":"test-key","kty":"RSA"}]}')
        path = f.name
    cmd = ["helm", "template", "test-release", CHART, "--namespace", "agentgateway-system",
           "--set", "security.jwt.enabled=true", "--set", "security.jwt.issuer=solo.io",
           "--set-file", f"security.jwt.jwks.inline={path}"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    assert not [d for d in by_kind(docs, "EnterpriseAgentgatewayBackend")
                if d["metadata"]["name"].endswith("-jwks")], "no JWKS backend for inline mode"
    pols = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "jwtAuthentication" in d["spec"].get("traffic", {})]
    prov = pols[0]["spec"]["traffic"]["jwtAuthentication"]["providers"][0]
    parsed = json.loads(prov["jwks"]["inline"])
    assert parsed["keys"][0]["kid"] == "test-key"

def test_waf_baseline():
    docs = render()
    assert not by_kind(docs, "WAFPolicy"), "WAF off by default"
    docs = render("security.waf.enabled=true")
    wafs = by_kind(docs, "WAFPolicy")
    assert len(wafs) == 1
    assert "SecRuleEngine On" in wafs[0]["spec"]["ruleEngineSettings"]["inline"]
    directives = "".join(d["inline"] for d in wafs[0]["spec"]["customDirectives"])
    assert "AKIA[0-9A-Z]{16}" in directives
    attach = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
              if "entWAF" in d["spec"].get("traffic", {})]
    assert len(attach) == 1
    assert attach[0]["spec"]["traffic"]["entWAF"]["wafPolicyRef"]["name"] == "agentgateway-proxy-waf-baseline"
    assert attach[0]["spec"]["targetRefs"][0]["kind"] == "Gateway"

TEAMS_ARGS = [
    "teams[0].name=team-alpha", "teams[0].namespace=team-alpha", "teams[0].tier=gold",
    "teams[1].name=team-beta", "teams[1].namespace=ns-beta", "teams[1].tier=silver",
]

def test_team_parent_routes():
    docs = render(*TEAMS_ARGS)
    routes = by_kind(docs, "HTTPRoute")
    assert len(routes) == 2
    alpha = [r for r in routes if r["metadata"]["name"] == "team-team-alpha"][0]
    assert alpha["spec"]["parentRefs"] == [{"name": "agentgateway-proxy"}]
    rule = alpha["spec"]["rules"][0]
    assert rule["matches"][0]["path"] == {"type": "PathPrefix", "value": "/teams/team-alpha"}
    assert rule["backendRefs"][0] == {
        "group": "gateway.networking.k8s.io", "kind": "HTTPRoute",
        "name": "team=team-alpha", "namespace": "team-alpha"}
    beta = [r for r in routes if r["metadata"]["name"] == "team-team-beta"][0]
    assert beta["spec"]["rules"][0]["backendRefs"][0]["namespace"] == "ns-beta"

def test_tier_policies():
    docs = render(*TEAMS_ARGS)
    pols = {d["metadata"]["name"]: d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if d["metadata"]["name"].endswith("-tier")}
    gold = pols["team-team-alpha-tier"]["spec"]
    assert gold["targetRefs"][0] == {
        "group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "team-team-alpha"}
    assert gold["traffic"]["rateLimit"]["local"] == [{"tokens": 100000, "unit": "Minutes"}]
    assert gold["traffic"]["retry"]["attempts"] == 3
    assert gold["traffic"]["timeouts"]["request"] == "120s"
    silver = pols["team-team-beta-tier"]["spec"]["traffic"]
    assert silver["rateLimit"]["local"][0]["tokens"] == 10000
    assert "retry" not in silver, "silver tier defines no retry"

def test_tool_call_limits():
    docs = render(*TEAMS_ARGS)
    assert not by_kind(docs, "RateLimitConfig"), "no RateLimitConfig unless toolCallsPerMinute set"
    assert not [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
                if "entRateLimit" in d["spec"].get("traffic", {})]
    docs = render(*TEAMS_ARGS, "tiers.silver.rateLimit.toolCallsPerMinute=5")
    rlcs = by_kind(docs, "RateLimitConfig")
    assert len(rlcs) == 1, "only the silver team renders a RateLimitConfig"
    raw = rlcs[0]["spec"]["raw"]
    assert rlcs[0]["metadata"]["name"] == "team-team-beta-tool-calls"
    assert raw["domain"] == "team-team-beta-tool-calls"
    assert raw["descriptors"][0] == {
        "key": "mcp_tool_call", "value": "true",
        "rateLimit": {"requestsPerUnit": 5, "unit": "MINUTE"}}
    cel = raw["rateLimits"][0]["actions"][0]["cel"]
    assert cel["key"] == "mcp_tool_call"
    assert 'body.method == "tools/call"' in cel["expression"]
    assert "has(body.method)" in cel["expression"], "CEL must guard non-MCP bodies"
    pols = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "entRateLimit" in d["spec"].get("traffic", {})]
    assert len(pols) == 1
    p = pols[0]
    assert p["metadata"]["name"] == "team-team-beta-tool-calls"
    assert p["spec"]["targetRefs"][0] == {
        "group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "team-team-beta"}
    assert p["spec"]["traffic"]["entRateLimit"]["global"]["rateLimitConfigRefs"] == [
        {"name": "team-team-beta-tool-calls"}]

def test_unknown_tier_fails():
    import subprocess
    cmd = ["helm", "template", "t", CHART, "--set", "teams[0].name=x",
           "--set", "teams[0].namespace=x", "--set", "teams[0].tier=platinum"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode != 0 and "undefined tier" in out.stderr

def test_schema_rejects_unknown_key():
    import subprocess
    cmd = ["helm", "template", "t", CHART, "--set", "trafficPolicy.foo=bar"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode != 0, "schema should reject unknown top-level keys"

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
