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

    def test_github_and_slack_tokens(self):
        # Built by concatenation so the test file itself holds no token-shaped literal.
        gh = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0"
        slack = "xo" + "xb-" + "1234567890-abcdefABCDEF"
        pat = "github" + "_pat_" + "11ABCDEFG0123456789"
        gkey = "AI" + "za" + "SyA1b2C3d4E5f6G7h8"
        aws_long = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
        aws_sts = "AS" + "IA" + "ABCDEFGHIJKLMNOP"
        for token in (gh, slack, pat, gkey, aws_long, aws_sts):
            self.assertIn(("secret-prefix", token), self.hits("token: %s" % token), token)

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

    def test_dotted_identifiers_are_not_hostnames(self):
        self.assertEqual(self.hits("jwt.plan chat.completion access.token.claim org.keycloak.userprofile.UserProfileProvider"), [])

    def test_tld_rule(self):
        self.assertIn(("hostname", "corp.acme.nl"), self.hits("host corp.acme.nl"))
        self.assertIn(("hostname", "bank.acme.systems"), self.hits("host bank.acme.systems"))
        self.assertIn(("hostname", "corp.acme.NET"), self.hits("host corp.acme.NET"))

    def test_user_value_word_boundary(self):
        self.assertEqual(self.hits("an explanation", user_values=["plan"]), [])
        self.assertIn(("user-value", "plan"), self.hits("the plan claim", user_values=["plan"]))

    def test_ipv6(self):
        self.assertIn(("ipv6", "fe80::1"), self.hits("addr fe80::1 here"))


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

    def run_changes(self, rows):
        tmp = tempfile.mkdtemp()
        changes = os.path.join(tmp, "CHANGES.md")
        with open(changes, "w") as fh:
            fh.write("## Configuration\n\n| Kind | Name |\n|--|--|\n" + rows)
        return scrub.user_values_from_changes(changes)

    def test_extraction_skips(self):
        vals = self.run_changes(
            "| Secret | `12345` |\n| Secret | `/openai` |\n| Secret | `/acme-payments` |\n"
            "| Secret | `OPENAI_API_KEY` |\n| Secret | `KUBECONFIG` |\n| Secret | `gold-user` |\n")
        self.assertEqual(vals, ["/acme-payments", "gold-user"])

    def test_summary(self):
        tmp = tempfile.mkdtemp()
        lab = os.path.join(tmp, "lab.md")
        with open(lab, "w") as fh:
            fh.write("ip 10.1.2.3\nagain 10.1.2.3 and 10.9.9.9\n")
        proc = subprocess.run([sys.executable, SCRUB, "--summary", "lab.md"], capture_output=True, text=True, cwd=tmp)
        self.assertEqual(proc.stdout.splitlines(), ["2\tipv4\t10.1.2.3\tlab.md:1", "1\tipv4\t10.9.9.9\tlab.md:2"])

    def test_binary_skipped(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "b.bin"), "wb") as fh:
            fh.write(b"\0 10.1.2.3")
        proc = subprocess.run([sys.executable, SCRUB, "b.bin"], capture_output=True, text=True, cwd=tmp)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_patch_file_is_scanned(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "staged.patch"), "w") as fh:
            fh.write("diff --git a/x.md b/x.md\n+issuer: http://10.1.2.3/realms/x\n")
        proc = subprocess.run([sys.executable, SCRUB, "--summary", "staged.patch"], capture_output=True, text=True, cwd=tmp)
        self.assertEqual(proc.stdout.splitlines(), ["1\tipv4\t10.1.2.3\tstaged.patch:2"])

    def test_missing_path_exits_2(self):
        proc = subprocess.run([sys.executable, SCRUB, "/nonexistent/x.md"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
