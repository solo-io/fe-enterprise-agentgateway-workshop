"""Dashboard interface tests: the JSON is valid, internally consistent
(dash_prices.py invariants), and pinned to pricing.json."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OBS = os.path.dirname(HERE)
DASH = os.path.join(OBS, "agentgateway-grafana-dashboard-v1.json")
PRICES = os.path.join(OBS, "dash_prices.py")


class TestDashboard(unittest.TestCase):
    def test_json_parses_and_has_panels(self):
        d = json.load(open(DASH, encoding="utf-8"))
        self.assertGreater(len(d["panels"]), 80)

    def test_validate_passes(self):
        r = subprocess.run([sys.executable, PRICES, "validate"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)

    def test_pricing_json_matches_dashboard(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            r = subprocess.run([sys.executable, PRICES, "extract",
                                "--json", tmp.name],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            extracted = json.load(open(tmp.name, encoding="utf-8"))
        pinned = json.load(open(os.path.join(OBS, "pricing.json"),
                                encoding="utf-8"))
        self.assertEqual(extracted["rates"], pinned["rates"],
                         "dashboard rates diverged from pricing.json — "
                         "regenerate pricing.json via dash_prices.py extract")


class TestModelsDevCheck(unittest.TestCase):
    def test_offline_check_runs_against_fixture(self):
        # Fixture: models.dev agreeing exactly with the pinned card for one
        # model, so the check exits 0 for that mapping subset is not provable
        # offline; instead assert the script runs and classifies correctly.
        card = json.load(open(os.path.join(OBS, "pricing.json"),
                              encoding="utf-8"))
        rates = card["rates"]["gpt-5.4-mini"]
        api = {"openai": {"models": {"gpt-5.4-mini": {"cost": {
            "input": rates["input"], "output": rates["output"],
            "cache_read": rates.get("cache_read")}}}},
            "anthropic": {"models": {}}, "amazon-bedrock": {"models": {}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            json.dump(api, f); path = f.name
        r = subprocess.run([sys.executable,
                            os.path.join(OBS, "models-dev-check.py"),
                            "--api-file", path],
                           capture_output=True, text=True)
        self.assertNotIn("DRIFT gpt-5.4-mini", r.stdout)
        self.assertIn("NOT-ON-MODELS.DEV", r.stdout)  # the other mapped models


if __name__ == "__main__":
    unittest.main()
