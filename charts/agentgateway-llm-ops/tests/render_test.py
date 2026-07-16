#!/usr/bin/env python3
"""Render tests for agentgateway-llm-ops. Run: python3 charts/agentgateway-llm-ops/tests/render_test.py"""
import hashlib
import json
import subprocess, os, tempfile, yaml

CHART = os.path.join(os.path.dirname(__file__), "..")

def budget_policy_name(alias, release="test-release"):
    """Mirror of the template's llm-budget-<alias>-<sha256(release)[:8]> naming."""
    return f"llm-budget-{alias}-{hashlib.sha256(release.encode()).hexdigest()[:8]}"

def render(*sets, values=None, release="test-release"):
    cmd = ["helm", "template", release, CHART, "--namespace", "agentgateway-system"]
    for s in sets:
        cmd += ["--set", s]
    if values:
        cmd += ["--values", values]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(f"helm template failed:\n{out.stderr}")
    return [d for d in yaml.safe_load_all(out.stdout) if d]

def render_error(*sets, values=None):
    """Render expecting failure; returns stderr."""
    cmd = ["helm", "template", "test-release", CHART, "--namespace", "agentgateway-system"]
    for s in sets:
        cmd += ["--set", s]
    if values:
        cmd += ["--values", values]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode != 0, "expected helm template to fail, but it succeeded"
    return out.stderr

def values_file(content):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return f.name

def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]

def test_default_render():
    docs = render()
    gws = by_kind(docs, "Gateway")
    assert len(gws) == 1, f"expected 1 Gateway, got {len(gws)}"
    gw = gws[0]
    assert gw["metadata"]["name"] == "agw-llm-ops"
    assert gw["spec"]["gatewayClassName"] == "enterprise-agentgateway"
    assert [l["port"] for l in gw["spec"]["listeners"]] == [8080]
    assert gw["spec"]["infrastructure"]["parametersRef"]["name"] == "agw-llm-ops-config"
    params = by_kind(docs, "EnterpriseAgentgatewayParameters")
    assert len(params) == 1
    p = params[0]["spec"]
    assert p["logging"]["level"] == "info"
    assert p["deployment"]["spec"]["replicas"] == 2
    assert p["service"]["spec"]["type"] == "LoadBalancer"
    # empty modelCatalog/grants: no routes, backends, secrets, or rate limits
    assert not by_kind(docs, "HTTPRoute")
    assert not by_kind(docs, "EnterpriseAgentgatewayBackend")
    assert not by_kind(docs, "Secret")
    assert not by_kind(docs, "RateLimitConfig")

def test_access_log_default_on():
    docs = render()
    pols = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "accessLog" in d["spec"].get("frontend", {})]
    assert len(pols) == 1
    p = pols[0]
    assert p["metadata"]["name"] == "agw-llm-ops-access-log"
    assert p["spec"]["targetRefs"][0] == {
        "group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "agw-llm-ops"}

def test_gateway_name_propagates():
    docs = render("gateway.name=agw-custom")
    assert by_kind(docs, "Gateway")[0]["metadata"]["name"] == "agw-custom"
    assert by_kind(docs, "Gateway")[0]["spec"]["infrastructure"]["parametersRef"]["name"] == "agw-custom-config"

CATALOG_VALUES = """
modelCatalog:
  - alias: chat-mock
    provider: openai
    model: mock-model
    host: mock-model-svc.agentgateway-system.svc.cluster.local
    port: 8000
    apiPath: /v1/chat/completions
  - alias: chat-real
    provider: openai
    model: gpt-4o-mini
    auth:
      secretRef: openai-secret
"""

def test_catalog_backends():
    docs = render(values=values_file(CATALOG_VALUES))
    bes = {d["metadata"]["name"]: d for d in by_kind(docs, "EnterpriseAgentgatewayBackend")}
    assert set(bes) == {"llm-chat-mock", "llm-chat-real"}, f"got {set(bes)}"
    mock = bes["llm-chat-mock"]["spec"]
    assert mock["ai"]["provider"]["openai"]["model"] == "mock-model"
    assert mock["ai"]["provider"]["host"] == "mock-model-svc.agentgateway-system.svc.cluster.local"
    assert mock["ai"]["provider"]["port"] == 8000
    assert mock["ai"]["provider"]["path"] == "/v1/chat/completions"
    assert mock["policies"]["auth"] == {"passthrough": {}}
    real = bes["llm-chat-real"]["spec"]
    assert real["ai"]["provider"]["openai"]["model"] == "gpt-4o-mini"
    assert "host" not in real["ai"]["provider"]
    assert real["policies"]["auth"] == {"secretRef": {"name": "openai-secret"}}

def test_catalog_routes():
    docs = render(values=values_file(CATALOG_VALUES))
    routes = {d["metadata"]["name"]: d for d in by_kind(docs, "HTTPRoute")}
    assert set(routes) == {"llm-chat-mock", "llm-chat-real"}
    r = routes["llm-chat-mock"]["spec"]
    assert r["parentRefs"] == [{"name": "agw-llm-ops"}]
    assert r["rules"][0]["matches"][0]["path"] == {"type": "PathPrefix", "value": "/llm/chat-mock"}
    assert r["rules"][0]["backendRefs"][0] == {
        "name": "llm-chat-mock", "group": "enterpriseagentgateway.solo.io",
        "kind": "EnterpriseAgentgatewayBackend"}

def test_catalog_auth_policies():
    docs = render(values=values_file(CATALOG_VALUES))
    pols = {d["metadata"]["name"]: d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "apiKeyAuthentication" in d["spec"].get("traffic", {})}
    assert set(pols) == {"llm-chat-mock-auth", "llm-chat-real-auth"}
    p = pols["llm-chat-mock-auth"]["spec"]
    assert p["targetRefs"][0] == {
        "group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "llm-chat-mock"}
    auth = p["traffic"]["apiKeyAuthentication"]
    assert auth["mode"] == "Strict"
    assert auth["secretSelector"]["matchLabels"] == {
        "llm-ops.agentgateway.solo.io/alias-chat-mock": "granted"}

def test_schema_rejects_unknown_keys():
    err = render_error("tiers.gold.rateLimit.tokensPerMinute=999999")
    assert "additional properties" in err and "tiers" in err

FULL_VALUES = CATALOG_VALUES + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-real
      - chat-mock
    tokensPerMinute: 1000
  - team: team-beta
    key: beta-key-2c71
    aliases:
      - chat-mock
    tokensPerMinute: 5
"""

def test_grant_key_secrets():
    docs = render(values=values_file(FULL_VALUES))
    secs = {d["metadata"]["name"]: d for d in by_kind(docs, "Secret")}
    assert set(secs) == {"llm-key-team-alpha", "llm-key-team-beta"}
    alpha = secs["llm-key-team-alpha"]
    labels = alpha["metadata"]["labels"]
    assert labels["llm-ops.agentgateway.solo.io/alias-chat-real"] == "granted"
    assert labels["llm-ops.agentgateway.solo.io/alias-chat-mock"] == "granted"
    beta_labels = secs["llm-key-team-beta"]["metadata"]["labels"]
    assert "llm-ops.agentgateway.solo.io/alias-chat-real" not in beta_labels
    assert beta_labels["llm-ops.agentgateway.solo.io/alias-chat-mock"] == "granted"
    entry = json.loads(alpha["stringData"]["team-alpha"])
    assert entry == {"key": "alpha-key-8f3a", "metadata": {"user_id": "team-alpha"}}

def test_grant_budgets():
    """Two grants -> two per-team RateLimitConfigs (unchanged), but the budget
    policy is grouped PER ALIAS, not per team: chat-mock is shared by both
    teams, so it gets exactly one policy referencing both teams' configs;
    chat-real is alpha-only, so it gets its own single-ref policy. Verified
    live: one EnterpriseAgentgatewayPolicy per TEAM, each independently
    attaching entRateLimit to a shared alias route, made a 2-replica proxy
    fleet resolve the two competing policies inconsistently -- one replica
    permanently honored one team's policy for the whole route, the other
    replica the other team's, so one team's budget silently never applied on
    however much of its traffic hit the "wrong" replica. Grouping by alias
    means a shared route only ever has single-targetRef policies attached --
    the verified-safe shape -- rather than multi-targetRef policies competing
    on it."""
    docs = render(values=values_file(FULL_VALUES))
    rlcs = {d["metadata"]["name"]: d for d in by_kind(docs, "RateLimitConfig")}
    assert set(rlcs) == {"llm-budget-team-alpha", "llm-budget-team-beta"}

    alpha_raw = rlcs["llm-budget-team-alpha"]["spec"]["raw"]
    assert alpha_raw["domain"] == "llm-budget-team-alpha"
    assert alpha_raw["descriptors"] == [{
        "key": "user_id", "value": "team-alpha",
        "rateLimit": {"unit": "MINUTE", "requestsPerUnit": 1000}}]
    alpha_rl = alpha_raw["rateLimits"][0]
    assert alpha_rl["type"] == "TOKEN"
    assert alpha_rl["actions"][0]["cel"] == {"expression": "apiKey.user_id", "key": "user_id"}

    beta_raw = rlcs["llm-budget-team-beta"]["spec"]["raw"]
    assert beta_raw["domain"] == "llm-budget-team-beta"
    assert beta_raw["descriptors"] == [{
        "key": "user_id", "value": "team-beta",
        "rateLimit": {"unit": "MINUTE", "requestsPerUnit": 5}}]

    pols = {d["metadata"]["name"]: d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "entRateLimit" in d["spec"].get("traffic", {})}
    real_name = budget_policy_name("chat-real")
    mock_name = budget_policy_name("chat-mock")
    assert set(pols) == {real_name, mock_name}

    real_targets = pols[real_name]["spec"]["targetRefs"]
    assert {(t["kind"], t["name"]) for t in real_targets} == {("HTTPRoute", "llm-chat-real")}
    assert all(t["group"] == "gateway.networking.k8s.io" for t in real_targets)
    assert pols[real_name]["spec"]["traffic"]["entRateLimit"]["global"]["rateLimitConfigRefs"] == [
        {"name": "llm-budget-team-alpha"}]

    mock_targets = pols[mock_name]["spec"]["targetRefs"]
    assert {(t["kind"], t["name"]) for t in mock_targets} == {("HTTPRoute", "llm-chat-mock")}
    assert pols[mock_name]["spec"]["traffic"]["entRateLimit"]["global"]["rateLimitConfigRefs"] == [
        {"name": "llm-budget-team-alpha"}, {"name": "llm-budget-team-beta"}]

def test_no_grants_no_budget_resources():
    docs = render(values=values_file(CATALOG_VALUES))
    assert not by_kind(docs, "RateLimitConfig")
    assert not by_kind(docs, "Secret")
    assert not [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
                if "entRateLimit" in d["spec"].get("traffic", {})]

def test_grant_unknown_alias_fails():
    bad = CATALOG_VALUES + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-nonexistent
    tokensPerMinute: 1000
"""
    err = render_error(values=values_file(bad))
    assert "unknown alias" in err and "chat-nonexistent" in err

GRANT_ONLY_VALUES = """
gateway: null

grants:
  - team: team-gamma
    key: gamma-secret-key-000
    aliases:
      - chat-real
    tokensPerMinute: 2000
"""

def test_grant_only_mode():
    """Values with only `grants` set (gateway explicitly nulled out of the
    chart's infra defaults, modelCatalog left at its empty default) render
    ONLY the per-grant Secret + budget pair -- no infra, no catalog."""
    docs = render(values=values_file(GRANT_ONLY_VALUES))
    assert not by_kind(docs, "Gateway")
    assert not by_kind(docs, "EnterpriseAgentgatewayParameters")
    assert not by_kind(docs, "HTTPRoute")
    assert not by_kind(docs, "EnterpriseAgentgatewayBackend")

    secs = by_kind(docs, "Secret")
    assert len(secs) == 1
    assert secs[0]["metadata"]["name"] == "llm-key-team-gamma"

    rlcs = by_kind(docs, "RateLimitConfig")
    assert len(rlcs) == 1
    assert rlcs[0]["metadata"]["name"] == "llm-budget-team-gamma"

    pols = by_kind(docs, "EnterpriseAgentgatewayPolicy")
    assert len(pols) == 1, f"expected only the budget policy, got {[p['metadata']['name'] for p in pols]}"
    assert pols[0]["metadata"]["name"] == budget_policy_name("chat-real")
    assert pols[0]["spec"]["traffic"]["entRateLimit"]["global"]["rateLimitConfigRefs"] == [
        {"name": "llm-budget-team-gamma"}]
    assert "apiKeyAuthentication" not in pols[0]["spec"].get("traffic", {})
    assert "accessLog" not in pols[0]["spec"].get("frontend", {})

    assert len(docs) == 3, f"expected exactly Secret + RateLimitConfig + Policy, got {len(docs)} docs"

def test_grant_only_skips_alias_validation():
    """A grant-only release can't see modelCatalog (it isn't rendering one),
    so an alias with no matching catalog entry must not fail the render --
    the resulting alias label is simply inert until an infra-mode release
    with that alias in its catalog also targets this namespace."""
    docs = render(values=values_file(GRANT_ONLY_VALUES))
    secs = by_kind(docs, "Secret")
    assert len(secs) == 1
    labels = secs[0]["metadata"]["labels"]
    assert labels["llm-ops.agentgateway.solo.io/alias-chat-real"] == "granted"

def test_schema_rejects_incomplete_grant():
    bad = CATALOG_VALUES + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
"""
    err = render_error(values=values_file(bad))
    assert "tokensPerMinute" in err

def test_duplicate_grant_team_fails():
    bad = CATALOG_VALUES + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
    tokensPerMinute: 1000
  - team: team-alpha
    key: alpha-key-second
    aliases:
      - chat-real
    tokensPerMinute: 500
"""
    err = render_error(values=values_file(bad))
    assert "duplicate grant" in err and "team-alpha" in err

def test_duplicate_catalog_alias_fails():
    bad = """
modelCatalog:
  - alias: chat-mock
    provider: openai
    model: mock-model
  - alias: chat-mock
    provider: openai
    model: mock-model-2
"""
    err = render_error(values=values_file(bad))
    assert "duplicate alias" in err and "chat-mock" in err

def test_grant_repeated_alias_dedupes_label():
    dup = CATALOG_VALUES + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
      - chat-mock
      - chat-real
    tokensPerMinute: 1000
"""
    docs = render(values=values_file(dup))
    secs = by_kind(docs, "Secret")
    assert len(secs) == 1
    labels = secs[0]["metadata"]["labels"]
    assert labels["llm-ops.agentgateway.solo.io/alias-chat-mock"] == "granted"
    assert labels["llm-ops.agentgateway.solo.io/alias-chat-real"] == "granted"
    alias_labels = [k for k in labels if k.startswith("llm-ops.agentgateway.solo.io/alias-")]
    assert len(alias_labels) == 2, f"expected 2 alias labels, got {alias_labels}"

def test_access_log_disabled_renders_nothing():
    docs = render("observability.accessLog.enabled=false")
    pols = [d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "accessLog" in d["spec"].get("frontend", {})]
    assert not pols

def test_grant_only_without_gateway_null_renders_infra():
    """Documents the helm-merge reality: a values file with ONLY `grants:`
    (no `gateway: null`) inherits the chart's default gateway block, so the
    infra docs DO render. This is asserted on purpose -- `gateway: null` IS
    the grant-only contract (standard helm remove-key idiom), and this test
    exists so nobody "fixes" the merge behavior silently later. The warning
    lives in values.yaml and the README."""
    only_grants = """
grants:
  - team: team-gamma
    key: gamma-secret-key-000
    aliases:
      - chat-real
    tokensPerMinute: 2000
"""
    docs = render(values=values_file(only_grants))
    assert len(by_kind(docs, "Gateway")) == 1
    assert len(by_kind(docs, "EnterpriseAgentgatewayParameters")) == 1

def test_budget_targetrefs_dedupe_repeated_alias():
    """A repeated alias within one grant must not produce a duplicate ref in
    that alias's policy (each policy is now scoped to a single alias, so
    dedup shows up as one rateLimitConfigRefs entry rather than one
    targetRefs entry)."""
    dup = CATALOG_VALUES + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
      - chat-mock
      - chat-real
    tokensPerMinute: 1000
"""
    docs = render(values=values_file(dup))
    pols = {d["metadata"]["name"]: d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "entRateLimit" in d["spec"].get("traffic", {})}
    assert set(pols) == {budget_policy_name("chat-mock"), budget_policy_name("chat-real")}
    mock_pol = pols[budget_policy_name("chat-mock")]
    assert mock_pol["spec"]["targetRefs"] == [
        {"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "llm-chat-mock"}]
    assert mock_pol["spec"]["traffic"]["entRateLimit"]["global"]["rateLimitConfigRefs"] == [
        {"name": "llm-budget-team-alpha"}], "repeated alias must not duplicate the ref"

def test_budget_policy_name_unique_across_releases():
    """Two releases granting the same alias must render DIFFERENT policy
    names (Helm ownership would collide otherwise). The name is
    llm-budget-<alias>-<sha256(releaseName)[:8]>: plain release-name
    concatenation was rejected because it is ambiguous -- release
    "grant-team-beta" + alias "chat-mock" and release "grant-team-beta-chat"
    + alias "mock" would concatenate to the same string. Release identity
    stays queryable via the app.kubernetes.io/instance label."""
    grant_only = """
gateway: null

grants:
  - team: team-beta
    key: beta-key-2c71
    aliases:
      - chat-mock
    tokensPerMinute: 5
"""
    def policy_names(release):
        docs = render(values=values_file(grant_only), release=release)
        return {d["metadata"]["name"] for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
                if "entRateLimit" in d["spec"].get("traffic", {})}

    infra = render(values=values_file(FULL_VALUES), release="agw-llm-ops")
    infra_names = {d["metadata"]["name"] for d in by_kind(infra, "EnterpriseAgentgatewayPolicy")
                   if "entRateLimit" in d["spec"].get("traffic", {})}
    beta_names = policy_names("grant-team-beta")

    # same alias (chat-mock) granted by both releases -> disjoint policy names
    assert beta_names == {budget_policy_name("chat-mock", "grant-team-beta")}
    assert budget_policy_name("chat-mock", "agw-llm-ops") in infra_names
    assert not (infra_names & beta_names), f"cross-release collision: {infra_names & beta_names}"

    # the reviewer's ambiguous-concatenation pair stays distinct under hashing
    mock_alias = """
gateway: null

grants:
  - team: team-beta
    key: beta-key-2c71
    aliases:
      - mock
    tokensPerMinute: 5
"""
    beta_chat_mock = policy_names("grant-team-beta")
    beta_chat_names = {d["metadata"]["name"] for d in
                       by_kind(render(values=values_file(mock_alias), release="grant-team-beta-chat"),
                               "EnterpriseAgentgatewayPolicy")
                       if "entRateLimit" in d["spec"].get("traffic", {})}
    assert not (beta_chat_mock & beta_chat_names), \
        f"ambiguous concatenation regression: {beta_chat_mock & beta_chat_names}"

def test_grant_only_empty_grants_renders_nothing():
    empty = """
gateway: null

grants: []
"""
    docs = render(values=values_file(empty))
    assert docs == [], f"expected zero docs, got {[(d['kind'], d['metadata']['name']) for d in docs]}"

def test_grant_only_with_leftover_catalog_fails_clearly():
    """`gateway: null` (grant-only mode) with a leftover `modelCatalog` entry
    (e.g. copy-pasted from an infra values file and not trimmed) must fail
    fast with a diagnosis, not a nil-pointer dereference on `.Values.gateway.name`
    inside catalog-routes.yaml."""
    bad = """
gateway: null

modelCatalog:
  - alias: chat-real
    provider: openai
    model: gpt-4o-mini
    auth:
      secretRef: openai-secret

grants:
  - team: team-gamma
    key: gamma-secret-key-000
    aliases:
      - chat-real
    tokensPerMinute: 2000
"""
    err = render_error(values=values_file(bad))
    assert "grant-only releases" in err
    assert "nil pointer" not in err

if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"{len(fns) - failed}/{len(fns)} tests passed")
    sys.exit(1 if failed else 0)
