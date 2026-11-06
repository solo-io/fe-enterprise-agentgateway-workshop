"""Tests for tracks.py: plan ordering/filtering and prose/YAML lint."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import tracks  # noqa: E402

GOOD = os.path.join(HERE, "fixtures", "tracks", "good")
BAD = os.path.join(HERE, "fixtures", "tracks", "bad")
STALE = os.path.join(HERE, "fixtures", "tracks", "stale")
SPECS = os.path.join(GOOD, "specs")


def track(*use_cases):
    return {"track": "t", "title": "T", "use_cases": list(use_cases)}


def uc(name, *labs):
    return {"name": name, "value": "v", "labs": list(labs)}


class TestPlan(unittest.TestCase):
    def test_plan_orders_by_use_case_then_lab(self):
        t = tracks.load_track(os.path.join(GOOD, "t.yaml"))
        entries = tracks.plan(t, "vind", SPECS)
        self.assertEqual([e.key for e in entries],
                         ["routing/a", "routing/b", "routing/c"])
        self.assertEqual([e.status for e in entries], ["run"] * 3)
        self.assertEqual([e.use_case for e in entries], ["First", "First", "Second"])

    def test_only_on_filters_by_env(self):
        t = track(uc("U", {"spec": "routing/a", "only_on": ["eks"]}))
        self.assertEqual(tracks.plan(t, "gke", SPECS)[0].status, "not-applicable:eks")
        self.assertEqual(tracks.plan(t, "eks", SPECS)[0].status, "run")

    def test_only_on_unknown_env_is_not_applicable(self):
        t = track(uc("U", {"spec": "routing/a", "only_on": ["eks"]}))
        self.assertEqual(tracks.plan(t, "unknown", SPECS)[0].status, "not-applicable:eks")

    def test_only_on_joins_multiple(self):
        t = track(uc("U", {"spec": "routing/a", "only_on": ["eks", "gke"]}))
        self.assertEqual(tracks.plan(t, "aks", SPECS)[0].status, "not-applicable:eks,gke")

    def test_no_spec_status_passes_through(self):
        t = track(uc("U", {"spec": "x/nospec", "lab": "labs/x/nospec.md", "status": "no-spec"}))
        e = tracks.plan(t, "vind", SPECS)[0]
        self.assertEqual(e.status, "no-spec")
        self.assertEqual(e.lab, "labs/x/nospec.md")

    def test_duplicate_spec_runs_once(self):
        t = track(uc("One", {"spec": "routing/a"}), uc("Two", {"spec": "routing/a"}))
        self.assertEqual([e.status for e in tracks.plan(t, "vind", SPECS)],
                         ["run", "duplicate"])


class TestLint(unittest.TestCase):
    def test_lint_reports_missing_spec_and_unlisted_lab(self):
        errs = tracks.lint(BAD, os.path.join(BAD, "specs"), BAD)
        self.assertIn("missing spec: routing/gone (in t.yaml)", errs)
        self.assertIn("unlisted lab: labs/unlisted.md (linked from t.md)", errs)
        self.assertEqual(len(errs), 2)

    def test_lint_reports_stale_no_spec(self):
        errs = tracks.lint(STALE, os.path.join(STALE, "specs"), STALE)
        self.assertEqual(errs, ["stale no-spec: routing/a (in t.yaml)"])

    def test_lint_clean_tree_is_empty(self):
        self.assertEqual(tracks.lint(GOOD, SPECS, GOOD), [])


if __name__ == "__main__":
    unittest.main()
