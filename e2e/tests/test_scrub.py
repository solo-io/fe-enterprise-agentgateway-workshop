"""Tests for scrub.py: leak rules, allowlist, --from-changes, CLI output."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
sys.path.insert(0, LIB)
import scrub  # noqa: E402

SCRUB = os.path.join(LIB, "scrub.py")


class TestRules(unittest.TestCase):
    def hits(self, text, allow=(), user_values=()):
        return [(r, m) for _, r, m in scrub.scan_text(text, list(allow), list(user_values))]

    def test_ipv4_in_url(self):
        self.assertIn(("ipv4", "172.22.255.253"), self.hits("issuer: http://172.22.255.253:8080/realms/x"))

    def test_version_is_not_ipv4_or_hostname(self):
        self.assertEqual(self.hits("chart v2026.10.1 and 2.3.2"), [])

    def test_hostname_in_markdown_link(self):
        self.assertIn(("hostname", "corp.acme-internal.net"), self.hits("see [portal](https://corp.acme-internal.net/x)"))

    def test_allowlisted_hosts_clean(self):
        text = "keycloak.keycloak.svc.cluster.local api.openai.com quay.io/keycloak/keycloak:26.0 docs.solo.io"
        self.assertEqual(self.hits(text), [])

    def test_filenames_are_not_hostnames(self):
        self.assertEqual(self.hits("open labs/security/jwt-auth-with-rbac.md and spec.yaml"), [])

    def test_email_outside_example_com(self):
        self.assertIn(("email", "alex@acme.io"), self.hits("contact alex@acme.io"))
        self.assertEqual(self.hits("gold-user@example.com"), [])

    def test_bearer_and_prefixes(self):
        self.assertIn(("bearer", "Bearer abc.def.ghi"), self.hits("-H 'Authorization: Bearer abc.def.ghi'"))
        self.assertEqual([r for r, _ in self.hits("key sk-live-12345 and eyJhbGciOi")], ["secret-prefix", "secret-prefix"])

    def test_base64_blob(self):
        blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9w"
        self.assertIn(("base64-blob", blob), self.hits(f"n: {blob}"))
        self.assertEqual(self.hits(f"image: registry/{blob}"), [])

    def test_cloud_ids(self):
        rules = [r for r, _ in self.hits("arn:aws:iam::123456789012:role/x projects/my-proj-123/ 3f2504e0-4f89-11d3-9a0c-0305e82c3301")]
        self.assertEqual(rules, ["cloud-id", "cloud-id", "cloud-id"])

    def test_user_values(self):
        self.assertIn(("user-value", "Acme Payments"), self.hits("the Acme Payments team", user_values=["Acme Payments"]))

    def test_extra_allow(self):
        self.assertEqual(self.hits("host corp.acme-internal.net", allow=["*.acme-internal.net"]), [])


class TestCli(unittest.TestCase):
    def test_from_changes_and_output_format(self):
        tmp = tempfile.mkdtemp()
        changes = os.path.join(tmp, "CHANGES.md")
        with open(changes, "w") as fh:
            fh.write("## Configuration\n\n| Kind | Name | Namespace | What |\n|--|--|--|--|\n| Secret | `acme-key` | agentgateway-system | x |\n\n## Requirements\n\n| Id | Requirement |\n|--|--|\n| R1 | Acme Payments gets gold |\n")
        lab = os.path.join(tmp, "lab.md")
        with open(lab, "w") as fh:
            fh.write("line one\nthe acme-key secret for Acme Payments\n")
        proc = subprocess.run([sys.executable, SCRUB, "--from-changes", changes, lab], capture_output=True, text=True, cwd=tmp)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("lab.md:2\tuser-value\tacme-key", proc.stdout)
        self.assertIn("lab.md:2\tuser-value\tAcme Payments", proc.stdout)

    def test_missing_path_exits_2(self):
        proc = subprocess.run([sys.executable, SCRUB, "/nonexistent/x.md"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
