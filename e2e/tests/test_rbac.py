"""Structural checks for rbac/evaluator.yaml. No cluster needed."""
import os
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
RBAC = os.path.join(HERE, "..", "..", "rbac", "evaluator.yaml")


def docs():
    with open(RBAC) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def roles():
    return [d for d in docs() if d["kind"] in ("ClusterRole", "Role")]


def role():
    return next(d for d in docs() if d["kind"] == "ClusterRole" and d["metadata"]["name"] == "agw-evaluator")


def all_rules():
    return [r for d in roles() for r in d.get("rules") or []]


class TestEvaluatorRbac(unittest.TestCase):
    def test_no_wildcard_verbs_resources_or_groups(self):
        rules = all_rules()
        self.assertTrue(rules)
        for r in rules:
            for key in ("apiGroups", "resources", "verbs", "nonResourceURLs", "resourceNames"):
                self.assertNotIn("*", r.get(key, []), f"wildcard in {key}: {r}")
            self.assertNotIn("impersonate", r.get("verbs", []), f"impersonate granted: {r}")
            self.assertTrue(r.get("verbs"), f"rule without verbs: {r}")
            self.assertTrue(r.get("resources"), f"rule without resources: {r}")

    def test_every_rule_has_a_reason_comment(self):
        with open(RBAC) as f:
            lines = f.read().splitlines()
        idx = [i for i, l in enumerate(lines) if l.strip() == "- apiGroups:"]
        self.assertEqual(len(idx), len(all_rules()))
        for i in idx:
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            self.assertTrue(j >= 0 and lines[j].strip().startswith("#"),
                            f"rule at line {i + 1} has no comment above it")

    def test_escalate_and_bind_are_resource_scoped(self):
        found = False
        for r in all_rules():
            if {"escalate", "bind"} & set(r["verbs"]):
                found = True
                self.assertTrue(r.get("resourceNames"), f"unscoped escalate/bind: {r}")
        self.assertTrue(found, "expected escalate/bind rules for chart ClusterRoles")

    def test_custom_lab_snapshot_kinds_are_readable(self):
        # run-e2e.sh's custom-lab leftover check lists these cluster-scoped kinds.
        def can(group, resource, verb):
            return any(group in r.get("apiGroups", []) and resource in r["resources"] and verb in r["verbs"]
                       for r in all_rules())
        for group, resource in (("scheduling.k8s.io", "priorityclasses"),
                                ("admissionregistration.k8s.io", "validatingwebhookconfigurations"),
                                ("admissionregistration.k8s.io", "mutatingwebhookconfigurations")):
            for verb in ("get", "list"):
                self.assertTrue(can(group, resource, verb), f"{verb} {group}/{resource} not granted")

    def test_service_account_and_binding_match(self):
        d = docs()
        sa = next(x for x in d if x["kind"] == "ServiceAccount")
        crb = next(x for x in d if x["kind"] == "ClusterRoleBinding")
        self.assertEqual(sa["metadata"]["name"], "agw-evaluator")
        self.assertEqual(sa["metadata"]["namespace"], "agw-evaluator")
        self.assertEqual(role()["metadata"]["name"], "agw-evaluator")
        self.assertEqual(crb["roleRef"]["kind"], "ClusterRole")
        self.assertEqual(crb["roleRef"]["name"], role()["metadata"]["name"])
        self.assertIn({"kind": "ServiceAccount", "name": "agw-evaluator",
                       "namespace": "agw-evaluator"}, crb["subjects"])


if __name__ == "__main__":
    unittest.main()
