"""--uninstall-base must never run the cluster-wide Gateway API CRD delete in 001."""
import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "e2e", "lib"))
import labdoc  # noqa: E402

BASE = os.path.join(ROOT, "e2e", "specs", "_base")
FORBIDDEN = ("standard-install.yaml", "gateway-api/releases")


def load(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class BaseUninstall(unittest.TestCase):
    def selected_blocks(self, spec):
        lab = os.path.join(ROOT, spec["lab"])
        sections = labdoc.parse_lab(lab)
        out = []
        for step in spec.get("steps") or []:
            if "section" not in step:
                continue
            section = labdoc.find_section(sections, step["section"], lab)
            out += [body for _i, body in labdoc.select_blocks(section, step.get("run", "all"), lab, "t")]
        return out

    def test_902_never_selects_the_gateway_api_crd_delete(self):
        spec = load("902-uninstall-agentgateway.yaml")
        blocks = self.selected_blocks(spec)
        self.assertTrue(blocks, "902 selects no blocks")
        for body in blocks:
            for needle in FORBIDDEN:
                self.assertNotIn(needle, body)

    def test_001_uninstall_still_has_the_crd_block_separately(self):
        lab = os.path.join(ROOT, "001-install-enterprise-agentgateway.md")
        section = labdoc.find_section(labdoc.parse_lab(lab), "Uninstall", lab)
        self.assertTrue(any(n in b for b in section["blocks"] for n in FORBIDDEN),
                        "the optional CRD delete moved; re-check 902's block selection")

    def test_uninstall_specs_have_no_cleanup(self):
        for name in ("901-uninstall-monitoring.yaml", "902-uninstall-agentgateway.yaml"):
            self.assertIsNone(load(name).get("cleanup"), name)


if __name__ == "__main__":
    unittest.main()
