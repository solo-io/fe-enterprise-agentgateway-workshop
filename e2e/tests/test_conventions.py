"""Tests for the conventions linter, using throwaway fixture trees."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import conventions  # noqa: E402


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class TestLinkRule(unittest.TestCase):
    def test_broken_relative_link_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/a.md", "see [b](b.md) and [gone](missing.md)")
        write(root, "labs/b.md", "# B\n\n## Cleanup\n")
        errs = conventions.check_links(root, ["labs/a.md", "labs/b.md"])
        self.assertEqual(len(errs), 1)
        self.assertIn("missing.md", errs[0])

    def test_anchors_and_external_ignored(self):
        root = tempfile.mkdtemp()
        write(root, "labs/a.md", "[x](#anchor) [y](https://x.io) [z](b.md#frag)")
        write(root, "labs/b.md", "# B")
        errs = conventions.check_links(root, ["labs/a.md", "labs/b.md"])
        self.assertEqual(errs, [])


class TestGatewayIpRule(unittest.TestCase):
    def test_drifted_copy_reported(self):
        root = tempfile.mkdtemp()
        good = conventions.CANONICAL_GATEWAY_IP
        bad = good.replace("gateway-name=agentgateway-proxy", "gateway-name=agw")
        write(root, "labs/good.md", f"## S\n\n```bash\n{good}\n```\n\n## Cleanup\n")
        write(root, "labs/bad.md", f"## S\n\n```bash\n{bad}\n```\n\n## Cleanup\n")
        errs = conventions.check_gateway_ip(root, ["labs/good.md", "labs/bad.md"])
        self.assertEqual(len(errs), 1)
        self.assertIn("bad.md", errs[0])


class TestCleanupRule(unittest.TestCase):
    def test_missing_cleanup_reported_unless_allowlisted(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", "# X\n\n```bash\necho hi\n```\n")
        errs = conventions.check_cleanup(root, ["labs/x.md"], allow=set())
        self.assertEqual(len(errs), 1)
        errs = conventions.check_cleanup(root, ["labs/x.md"], allow={"labs/x.md"})
        self.assertEqual(errs, [])

    def test_h3_cleanup_accepted(self):
        root = tempfile.mkdtemp()
        write(root, "labs/y.md", "# Y\n\n### Cleanup\n\n```bash\necho bye\n```\n")
        errs = conventions.check_cleanup(root, ["labs/y.md"], allow=set())
        self.assertEqual(errs, [])


class TestEagbRule(unittest.TestCase):
    def test_bare_backend_kind_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", "```yaml\nkind: AgentgatewayBackend\n```\n\n## Cleanup\n")
        write(root, "labs/y.md", "```yaml\nkind: EnterpriseAgentgatewayBackend\n```\n\n## Cleanup\n")
        errs = conventions.check_eagb(root, ["labs/x.md", "labs/y.md"], allow=set())
        self.assertEqual(len(errs), 1)
        self.assertIn("x.md", errs[0])


AI_BACKEND = (
    "```yaml\napiVersion: enterpriseagentgateway.solo.io/v1alpha1\n"
    "kind: EnterpriseAgentgatewayBackend\nmetadata:\n  name: openai\n"
    "spec:\n  ai:\n    provider:\n      openai: {}\n```\n\n## Cleanup\n")
MCP_BACKEND = (
    "```yaml\napiVersion: enterpriseagentgateway.solo.io/v1alpha1\n"
    "kind: EnterpriseAgentgatewayBackend\nmetadata:\n  name: mcp\n"
    "spec:\n  mcp:\n    targets: []\n```\n\n## Cleanup\n")


class TestAiBackendRule(unittest.TestCase):
    def test_ai_backend_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", AI_BACKEND)
        write(root, "labs/y.md", MCP_BACKEND)
        errs = conventions.check_ai_backend(root, ["labs/x.md", "labs/y.md"], allow=set())
        self.assertEqual(len(errs), 1)
        self.assertIn("labs/x.md:3:", errs[0])

    def test_ai_backend_allowlisted(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", AI_BACKEND)
        errs = conventions.check_ai_backend(root, ["labs/x.md"], allow={"labs/x.md"})
        self.assertEqual(errs, [])

    def test_backendref_kind_not_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", (
            "```yaml\n  rules:\n    - backendRefs:\n        - name: openai\n"
            "          group: enterpriseagentgateway.solo.io\n"
            "          kind: EnterpriseAgentgatewayBackend\n"
            "      ai:\n        x: y\n```\n\n## Cleanup\n"))
        errs = conventions.check_ai_backend(root, ["labs/x.md"], allow=set())
        self.assertEqual(errs, [])

    def test_indented_heredoc_backend_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", (
            "```bash\ncat <<EOF\n  apiVersion: enterpriseagentgateway.solo.io/v1alpha1\n"
            "  kind: EnterpriseAgentgatewayBackend\n  metadata:\n    name: a\n"
            "  spec:\n    policies:\n      auth: {}\n    ai:\n      provider: {}\nEOF\n```\n\n## Cleanup\n"))
        errs = conventions.check_ai_backend(root, ["labs/x.md"], allow=set())
        self.assertEqual(len(errs), 1)

    def test_kind_before_apiversion_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", (
            "```yaml\nkind: EnterpriseAgentgatewayBackend\n"
            "apiVersion: enterpriseagentgateway.solo.io/v1alpha1\n"
            "metadata:\n  name: a\nspec:\n  ai:\n    provider: {}\n```\n\n## Cleanup\n"))
        errs = conventions.check_ai_backend(root, ["labs/x.md"], allow=set())
        self.assertEqual(len(errs), 1)

    def test_comments_between_apiversion_and_kind_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", (
            "```yaml\napiVersion: enterpriseagentgateway.solo.io/v1alpha1\n"
            "# an LLM backend\n# for OpenAI\nkind: EnterpriseAgentgatewayBackend\n"
            "metadata:\n  name: a\nspec:\n  ai:\n    provider: {}\n```\n\n## Cleanup\n"))
        errs = conventions.check_ai_backend(root, ["labs/x.md"], allow=set())
        self.assertEqual(len(errs), 1)

    def test_comment_inside_spec_reported(self):
        root = tempfile.mkdtemp()
        write(root, "labs/x.md", (
            "```bash\nkubectl apply -f - <<EOF\n  apiVersion: enterpriseagentgateway.solo.io/v1alpha1\n"
            "  kind: EnterpriseAgentgatewayBackend\n  metadata:\n    name: a\n  spec:\n"
            "# provider settings\n    ai:\n      provider: {}\nEOF\n```\n\n## Cleanup\n"))
        errs = conventions.check_ai_backend(root, ["labs/x.md"], allow=set())
        self.assertEqual(len(errs), 1)


if __name__ == "__main__":
    unittest.main()
