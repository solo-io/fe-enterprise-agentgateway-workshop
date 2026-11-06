"""Tests for report.py: result mapping, markdown layout, compare."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
sys.path.insert(0, LIB)
import report  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "report")


class ReportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session = os.path.join(FIX, "session.jsonl")

    def build(self, work=None, session=None):
        work = work or os.path.join(FIX, "work")
        proc = subprocess.run(
            [sys.executable, os.path.join(LIB, "report.py"), "build", "--work", work,
             "--track", "t", "--env-json", os.path.join(FIX, "env.json"),
             "--session", session or self.session, "--out", self.tmp.name,
             "--specs-dir", os.path.join(FIX, "specs"), "--tracks-dir", os.path.join(FIX, "tracks")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        md, js = proc.stdout.split()
        with open(md, encoding="utf-8") as fh:
            text = fh.read()
        with open(js, encoding="utf-8") as fh:
            data = json.load(fh)
        return md, js, text, data

    def row(self, data, key, uc=0):
        return next(r for r in data["use_cases"][uc]["labs"] if r["key"] == key)


class TestBuild(ReportCase):
    def test_filenames_use_class_and_track(self):
        md, js, _, _ = self.build()
        self.assertRegex(os.path.basename(md), r"^\d{8}-\d{4}-vind-t\.md$")
        self.assertTrue(js.endswith(".json"))

    def test_pass_with_deviation_is_marked(self):
        _, _, _, data = self.build()
        self.assertEqual(self.row(data, "routing/a")["result"], "PASS (deviation)")
        self.assertIn("port 8081", self.row(data, "routing/a")["note"])

    def test_skip_names_the_missing_key(self):
        _, _, _, data = self.build()
        self.assertEqual(self.row(data, "routing/b")["result"], "SKIP (missing KEY_X,KEY_Y)")

    def test_no_spec_is_not_automated(self):
        _, _, _, data = self.build()
        self.assertEqual(self.row(data, "routing/f", 1)["result"], "NOT AUTOMATED")

    def test_excluded_uses_spec_reason(self):
        _, _, _, data = self.build()
        r = self.row(data, "routing/d", 1)
        self.assertEqual((r["result"], r["note"]), ("NOT AUTOMATED", "Needs a live Keycloak server."))

    def test_not_applicable_names_env(self):
        _, _, _, data = self.build()
        self.assertEqual(self.row(data, "routing/e", 1)["result"], "NOT APPLICABLE (only on eks)")

    def test_interrupted_lab_reported_as_fail(self):
        _, _, _, data = self.build()
        r = self.row(data, "routing/c")
        self.assertEqual((r["result"], r["note"]), ("FAIL", "interrupted"))

    def test_missing_outcome_is_fail(self):
        _, _, _, data = self.build()
        r = self.row(data, "routing/g", 1)
        self.assertEqual((r["result"], r["note"]), ("FAIL", "no outcome recorded"))

    def test_duplicate_lab_listed_in_both_use_cases_once_run(self):
        _, _, _, data = self.build()
        self.assertEqual(self.row(data, "routing/a", 0)["result"], self.row(data, "routing/a", 1)["result"])
        self.assertEqual(self.row(data, "routing/a", 1)["result"], "PASS (deviation)")

    def test_leftovers_empty_renders_sentence(self):
        _, _, text, data = self.build()
        self.assertIn("None. The cluster matches the baseline.", text)
        self.assertEqual(data["leftovers"], [])

    def test_leftovers_file_is_listed(self):
        work = os.path.join(self.tmp.name, "w")
        os.makedirs(work)
        with open(os.path.join(work, "leftovers.txt"), "w") as fh:
            fh.write("BASELINE DRIFT: routing/a\n")
        _, _, text, data = self.build(work=work)
        self.assertEqual(data["leftovers"], ["BASELINE DRIFT: routing/a"])
        self.assertNotIn("None. The cluster matches", text)

    def test_questions_pair_with_answers(self):
        _, _, _, data = self.build()
        self.assertEqual(data["questions"], [{"q": "Do you have an OpenAI key?", "a": "No, skip it"}])

    def test_markdown_section_order(self):
        _, _, text, _ = self.build()
        heads = re.findall(r"^## (.+)$", text, re.M)
        self.assertEqual(heads, ["Environment", "First", "Second", "Deviations", "Failures", "Leftovers", "Questions"])
        self.assertIn("| Lab | Result | Duration | Note |", text)
        self.assertNotIn("—", text)


class TestCompare(ReportCase):
    def test_compare_prints_only_differences(self):
        _, js, _, data = self.build()
        other = json.loads(json.dumps(data))
        other["environment"]["class"] = "eks"
        self.row(other, "routing/c")["result"] = "PASS"
        pb = os.path.join(self.tmp.name, "b.json")
        with open(pb, "w") as fh:
            json.dump(other, fh)
        proc = subprocess.run([sys.executable, os.path.join(LIB, "report.py"), "compare", js, pb],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        lines = proc.stdout.strip().splitlines()
        self.assertIn("vind", lines[0])
        self.assertIn("eks", lines[0])
        self.assertEqual(lines[1:], ["routing/c: FAIL -> PASS"])


if __name__ == "__main__":
    unittest.main()
