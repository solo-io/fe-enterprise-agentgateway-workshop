"""Exercise certificate generation and the docs handshake without a cluster."""
import importlib.util
import io
import pathlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "e2e/lib"))
import labdoc

spec = importlib.util.spec_from_file_location("solo_check", ROOT / "e2e/lib/check-solo-docs.py")
solo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solo)


class Certificates(unittest.TestCase):
    def test_certificates_are_v3_and_verify_for_their_intended_usage(self):
        for name in ("tls-termination", "frontend-mtls", "sni-matching"):
            with self.subTest(lab=name), tempfile.TemporaryDirectory() as work:
                sections = labdoc.parse_lab(ROOT / "labs/security" / (name + ".md"))
                section = next(s for s in sections if s["heading"].startswith("Create") and s["blocks"])
                for block in section["blocks"]:
                    if "kubectl" in block:
                        continue
                    subprocess.run(["bash", "-c", block], cwd=work, check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                certs = pathlib.Path(work) / "example_certs"
                leaves = list(certs.glob("*.crt"))
                self.assertGreaterEqual(len(leaves), 2)
                for cert in leaves:
                    text = subprocess.check_output(["openssl", "x509", "-in", str(cert),
                                                    "-noout", "-text"], text=True)
                    self.assertIn("Version: 3", text)
                    if cert.name == "try-solo.io.crt":
                        self.assertIn("CA:TRUE", text)
                        continue
                    purpose = "sslclient" if cert.name.startswith("client.") else "sslserver"
                    subprocess.run(["openssl", "verify", "-CAfile", str(certs / "try-solo.io.crt"),
                                    "-purpose", purpose, str(cert)], check=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.assertIn("Subject Alternative Name", text)


class SoloConnection(unittest.TestCase):
    def test_json_and_sse_responses(self):
        expected = {"jsonrpc": "2.0", "id": 1, "result": {}}
        import json
        encoded = json.dumps(expected).encode()
        self.assertEqual(solo.decode(encoded), expected)
        self.assertEqual(solo.decode(b"event: message\r\ndata: " + encoded + b"\r\n\r\n"), expected)

    def test_http_403_is_reported_as_disconnected(self):
        error = urllib.error.HTTPError(solo.URL, 403, "Forbidden", {}, None)
        with patch.object(solo.Client, "check", side_effect=error), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(solo.main(), 1)
            self.assertIn("NOT CONNECTED (HTTP 403)", out.getvalue())

    def test_success_requires_tool_inventory(self):
        client = solo.Client()
        with patch.object(client, "request", side_effect=[{"protocolVersion": "2024-11-05"}, {}, {"tools": []}]):
            with self.assertRaisesRegex(ValueError, "required tools missing"):
                client.check()

    def test_pagination_and_initialized_notification(self):
        client = solo.Client()
        pages = [{"protocolVersion": "2024-11-05"}, {},
                 {"tools": [{"name": "search"}], "nextCursor": "next"},
                 {"tools": [{"name": n} for n in solo.REQUIRED - {"search"}]}]
        with patch.object(client, "request", side_effect=pages) as request:
            client.check()
            self.assertEqual(request.call_args_list[1].args[0], "notifications/initialized")
            self.assertEqual(request.call_args_list[-1].args[1], {"cursor": "next"})

    def test_project_config_has_only_solo(self):
        text = (ROOT / ".codex/config.toml").read_text()
        self.assertEqual(text.count("[mcp_servers."), 1)
        self.assertIn("[mcp_servers.soloio-docs-mcp]", text)
        self.assertIn(solo.URL, text)


class LocalJWKS(unittest.TestCase):
    def test_all_rate_limit_policies_use_the_fresh_clone_signer(self):
        import json
        import yaml
        with tempfile.TemporaryDirectory() as work:
            root = pathlib.Path(work)
            jwt = root / "lib/jwt"
            jwt.mkdir(parents=True)
            shutil.copy(ROOT / "lib/jwt/generate-jwt.sh", jwt)
            shutil.copytree(ROOT / "lib/jwt/claims", jwt / "claims")
            stub = root / "bin"
            stub.mkdir()
            kubectl = stub / "kubectl"
            kubectl.write_text('#!/usr/bin/env bash\nif [[ "$1" == apply ]]; then cat >> "$CAPTURE"; printf "\\n---\\n" >> "$CAPTURE"; fi\n')
            kubectl.chmod(0o755)
            sections = labdoc.parse_lab(ROOT / "labs/rate-limiting/global-rate-limiting.md")
            blocks = [b for section in sections for b in section["blocks"]
                      if 'WORKSHOP_JWKS="' in b or "$WORKSHOP_JWKS" in b]
            self.assertEqual(len(blocks), 4)
            capture = root / "policies.yaml"
            env = dict(os.environ, PATH=str(stub) + os.pathsep + os.environ["PATH"], CAPTURE=str(capture))
            subprocess.run(["bash", "-e", "-c", "\n".join(blocks)], cwd=root, env=env,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            expected = json.loads((jwt / "jwks.json").read_text())
            policies = [p for p in yaml.safe_load_all(capture.read_text()) if p]
            self.assertEqual(len(policies), 3)
            for policy in policies:
                inline = policy["spec"]["traffic"]["jwtAuthentication"]["providers"][0]["jwks"]["inline"]
                self.assertEqual(json.loads(inline), expected)


class CrewRuntime(unittest.TestCase):
    def test_explicit_missing_runtime_does_not_silently_use_another(self):
        env = dict(os.environ, CREWAI_PYTHON="/nonexistent/workshop-python")
        result = subprocess.run(["bash", str(ROOT / "lib/crewai/select-python.sh")],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("Python 3.11 or 3.12", result.stderr)


if __name__ == "__main__":
    unittest.main()
