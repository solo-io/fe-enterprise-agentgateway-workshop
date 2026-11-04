#!/usr/bin/env python3
"""Render tests for agentgateway-llm-ops. Run: python3 charts/agentgateway-llm-ops/tests/render_test.py"""
import json
import subprocess, os, tempfile, yaml

CHART = os.path.join(os.path.dirname(__file__), "..")

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
    assert not by_kind(docs, "AgentgatewayModel")
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
    baseURL: http://mock-model-svc.agentgateway-system.svc.cluster.local:8000/v1
  - alias: chat-real
    provider: openai
    model: gpt-5.4-nano
    auth:
      secretRef: openai-secret
"""

def test_catalog_models():
    docs = render(values=values_file(CATALOG_VALUES))
    assert not by_kind(docs, "EnterpriseAgentgatewayBackend")
    models = {d["metadata"]["name"]: d for d in by_kind(docs, "AgentgatewayModel")}
    assert set(models) == {"llm-chat-mock", "llm-chat-real"}, f"got {set(models)}"
    assert models["llm-chat-mock"]["apiVersion"] == "agentgateway.dev/v1alpha1"
    mock = models["llm-chat-mock"]["spec"]
    assert mock["parentRefs"] == [{
        "group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "llm-chat-mock"}]
    assert mock["match"] == {"model": "*"}
    assert mock["provider"] == "OpenAI"
    assert mock["baseURL"] == "http://mock-model-svc.agentgateway-system.svc.cluster.local:8000/v1"
    assert mock["policies"]["auth"] == {"passthrough": {}}
    # the alias pins its model, whatever the client sends
    assert mock["policies"]["transformations"] == [{"field": "model", "expression": '"mock-model"'}]
    real = models["llm-chat-real"]["spec"]
    assert real["policies"]["transformations"] == [{"field": "model", "expression": '"gpt-5.4-nano"'}]
    assert "baseURL" not in real
    assert real["policies"]["auth"] == {"secretRef": {"name": "openai-secret"}}

def test_catalog_anthropic_provider():
    docs = render(values=values_file("""
modelCatalog:
  - alias: claude
    provider: anthropic
    model: claude-haiku-4-5
    auth:
      secretRef: anthropic-secret
"""))
    m = by_kind(docs, "AgentgatewayModel")[0]["spec"]
    assert m["provider"] == "Anthropic"

def test_catalog_rejects_unsupported_provider():
    err = render_error(values=values_file("""
modelCatalog:
  - alias: br
    provider: bedrock
    model: x
"""))
    assert "provider" in err, err

def test_catalog_rejects_legacy_host_keys():
    err = render_error(values=values_file("""
modelCatalog:
  - alias: m
    provider: openai
    model: x
    host: mock
    port: 8000
"""))
    assert "host" in err or "additional" in err.lower(), err

def test_catalog_routes():
    docs = render(values=values_file(CATALOG_VALUES))
    routes = {d["metadata"]["name"]: d for d in by_kind(docs, "HTTPRoute")}
    assert set(routes) == {"llm-chat-mock", "llm-chat-real"}
    r = routes["llm-chat-mock"]["spec"]
    assert r["parentRefs"] == [{"name": "agw-llm-ops"}]
    assert r["rules"][0]["matches"][0]["path"] == {"type": "PathPrefix", "value": "/llm/chat-mock"}
    assert r["rules"][0]["backendRefs"][0] == {
        "group": "agentgateway.dev", "kind": "AgentgatewayModel", "name": "*"}

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

TIERS = """
budgetTiers:
  - name: small
    tokensPerMinute: 5
  - name: large
    tokensPerMinute: 1000
"""

FULL_VALUES = CATALOG_VALUES + TIERS + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-real
      - chat-mock
    budgetTier: large
  - team: team-beta
    key: beta-key-2c71
    aliases:
      - chat-mock
    budgetTier: small
"""

def budget_policies(docs):
    return {d["metadata"]["name"]: d for d in by_kind(docs, "EnterpriseAgentgatewayPolicy")
            if "entRateLimit" in d["spec"].get("traffic", {})}

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
    assert entry == {"key": "alpha-key-8f3a",
                     "metadata": {"user_id": "team-alpha", "budget_tier": "large"}}

def test_budget_tiers_config():
    """One RateLimitConfig holds every tier: budget_tier -> user_id, so each
    team keeps its own counter inside its tier. A key with a missing or
    unknown tier falls back to the FIRST tier listed, never to no limit."""
    docs = render(values=values_file(FULL_VALUES))
    rlcs = by_kind(docs, "RateLimitConfig")
    assert [r["metadata"]["name"] for r in rlcs] == ["llm-budget-tiers"]
    raw = rlcs[0]["spec"]["raw"]
    assert raw["domain"] == "llm-budget-tiers"
    assert raw["descriptors"] == [
        {"key": "budget_tier", "value": "small", "descriptors": [
            {"key": "user_id", "rateLimit": {"unit": "MINUTE", "requestsPerUnit": 5}}]},
        {"key": "budget_tier", "value": "large", "descriptors": [
            {"key": "user_id", "rateLimit": {"unit": "MINUTE", "requestsPerUnit": 1000}}]},
    ]
    rl = raw["rateLimits"][0]
    assert rl["type"] == "TOKEN"
    assert rl["actions"][0]["cel"] == {
        "key": "budget_tier",
        "expression": 'default(apiKey.budget_tier, "") in ["small", "large"] ? apiKey.budget_tier : "small"'}
    assert rl["actions"][1]["cel"] == {"key": "user_id", "expression": "apiKey.user_id"}

def test_budget_policy_per_alias():
    """Exactly one entRateLimit policy per alias route, owned by the infra
    release: a route enforces only one entRateLimit policy, so a second
    release must never attach its own."""
    docs = render(values=values_file(FULL_VALUES))
    pols = budget_policies(docs)
    assert set(pols) == {"llm-chat-mock-budget", "llm-chat-real-budget"}
    for alias in ("chat-mock", "chat-real"):
        spec = pols[f"llm-{alias}-budget"]["spec"]
        assert spec["targetRefs"] == [
            {"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": f"llm-{alias}"}]
        assert spec["traffic"]["entRateLimit"]["global"]["rateLimitConfigRefs"] == [
            {"name": "llm-budget-tiers"}]

def test_tiers_without_grants_still_render_budgets():
    """Every catalog alias is budgeted even when no grant lives in this
    release, so isolated grant releases are enforced too."""
    docs = render(values=values_file(CATALOG_VALUES + TIERS))
    assert [r["metadata"]["name"] for r in by_kind(docs, "RateLimitConfig")] == ["llm-budget-tiers"]
    assert set(budget_policies(docs)) == {"llm-chat-mock-budget", "llm-chat-real-budget"}

def test_no_tiers_no_budget_resources():
    docs = render(values=values_file(CATALOG_VALUES))
    assert not by_kind(docs, "RateLimitConfig")
    assert not by_kind(docs, "Secret")
    assert not budget_policies(docs)

def test_grants_without_tiers_fail():
    bad = CATALOG_VALUES + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
    budgetTier: small
"""
    err = render_error(values=values_file(bad))
    assert "budgetTiers" in err, err

def test_grant_unknown_tier_fails():
    bad = CATALOG_VALUES + TIERS + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
    budgetTier: huge
"""
    err = render_error(values=values_file(bad))
    assert "unknown budget tier" in err and "huge" in err, err

def test_duplicate_tier_fails():
    bad = CATALOG_VALUES + """
budgetTiers:
  - name: small
    tokensPerMinute: 5
  - name: small
    tokensPerMinute: 50
"""
    err = render_error(values=values_file(bad))
    assert "duplicate budget tier" in err and "small" in err, err

def test_schema_rejects_legacy_tokens_per_minute():
    bad = CATALOG_VALUES + TIERS + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
    budgetTier: small
    tokensPerMinute: 1000
"""
    err = render_error(values=values_file(bad))
    assert "tokensPerMinute" in err, err

def test_grant_unknown_alias_fails():
    bad = CATALOG_VALUES + TIERS + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-nonexistent
    budgetTier: large
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
    budgetTier: large
"""

def test_grant_only_mode():
    """A grant-only release renders ONLY the team's key Secret. Its budget
    comes from the tier named in the key, enforced by the infra release's
    per-alias policy, so this release attaches no policy of its own."""
    docs = render(values=values_file(GRANT_ONLY_VALUES))
    assert [(d["kind"], d["metadata"]["name"]) for d in docs] == [("Secret", "llm-key-team-gamma")]
    entry = json.loads(docs[0]["stringData"]["team-gamma"])
    assert entry["metadata"] == {"user_id": "team-gamma", "budget_tier": "large"}

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
    assert "budgetTier" in err

def test_duplicate_grant_team_fails():
    bad = CATALOG_VALUES + TIERS + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
    budgetTier: large
  - team: team-alpha
    key: alpha-key-second
    aliases:
      - chat-real
    budgetTier: small
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
    dup = CATALOG_VALUES + TIERS + """
grants:
  - team: team-alpha
    key: alpha-key-8f3a
    aliases:
      - chat-mock
      - chat-mock
      - chat-real
    budgetTier: large
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
    budgetTier: large
"""
    docs = render(values=values_file(only_grants))
    assert len(by_kind(docs, "Gateway")) == 1
    assert len(by_kind(docs, "EnterpriseAgentgatewayParameters")) == 1

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
    model: gpt-5.4-nano
    auth:
      secretRef: openai-secret

grants:
  - team: team-gamma
    key: gamma-secret-key-000
    aliases:
      - chat-real
    budgetTier: large
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
