"""Tests for report.py: result mapping, markdown layout, compare."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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
        # Hermetic: never call helm against the current kube context.
        patcher = mock.patch.dict(os.environ, {"E2E_CHART_VERSION": "v-test"})
        patcher.start()
        self.addCleanup(patcher.stop)

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
        self.assertRegex(os.path.basename(md), r"^\d{8}-\d{6}-vind-t\.md$")
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


class TestFixRound1(ReportCase):
    def session_with(self, *events):
        path = os.path.join(self.tmp.name, "s.jsonl")
        with open(path, "w") as fh:
            for kind, lab, text in events:
                fh.write(json.dumps({"ts": "t", "kind": kind, "lab": lab, "text": text}) + "\n")
        return path

    def test_failure_carries_note_and_decision(self):
        s = self.session_with(("failure", "routing/c", "Port clash."),
                              ("decision", "routing/c", "Skip it."),
                              ("decision", "routing/c", "Retry later."))
        _, _, text, data = self.build(session=s)
        f = data["failures"][0]
        self.assertEqual((f["lab"], f["note"], f["decision"]), ("routing/c", "interrupted", "Retry later."))
        self.assertIn("- routing/c (interrupted): Port clash. Decision: Retry later.", text)

    def test_keep_base_sentence_in_leftovers(self):
        s = self.session_with(("keep-base", "", "keep"))
        _, _, text, _ = self.build(session=s)
        leftovers = text.split("## Leftovers")[1].split("## Questions")[0]
        self.assertIn("The base install was kept on request.", leftovers)
        self.assertNotIn("kept on request", self.build()[2])

    def test_missing_outcome_follows_track_entry(self):
        work = os.path.join(self.tmp.name, "w")
        os.makedirs(work)
        _, _, _, data = self.build(work=work)
        self.assertEqual(self.row(data, "routing/f", 1)["result"], "NOT AUTOMATED")
        self.assertEqual(self.row(data, "routing/e", 1)["result"], "NOT APPLICABLE (only on eks)")
        self.assertEqual(self.row(data, "routing/a")["note"], "no outcome recorded")

    def test_malformed_outcome_is_fail(self):
        work = os.path.join(self.tmp.name, "w")
        os.makedirs(os.path.join(work, "routing", "a"))
        with open(os.path.join(work, "routing", "a", "outcome"), "w") as fh:
            fh.write("garbage\n")
        _, _, _, data = self.build(work=work)
        r = self.row(data, "routing/a")
        self.assertEqual((r["result"], r["note"]), ("FAIL", "malformed outcome"))

    def test_missing_env_json_exits_2_without_traceback(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(LIB, "report.py"), "build", "--work", self.tmp.name,
             "--track", "adhoc", "--env-json", os.path.join(self.tmp.name, "nope.json"),
             "--session", self.session, "--out", self.tmp.name], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("cannot read env json", proc.stderr)

    def test_compare_bad_json_exits_2_one_line(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        proc = subprocess.run([sys.executable, os.path.join(LIB, "report.py"), "compare", bad, bad],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1)
        self.assertNotIn("Traceback", proc.stderr)


class TestFinalWave(ReportCase):
    def work_with(self, **outcomes):
        """outcomes: {'routing/a': 'status=pass ...'}, keys written with / for the slug."""
        work = os.path.join(self.tmp.name, "w")
        for key, line in outcomes.items():
            d = os.path.join(work, *key.split("/"))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "outcome"), "w") as fh:
                fh.write(line + "\n")
        os.makedirs(work, exist_ok=True)
        return work

    def session_with(self, *events):
        path = os.path.join(self.tmp.name, "s.jsonl")
        with open(path, "w") as fh:
            for kind, lab, text in events:
                fh.write(json.dumps({"ts": "t", "kind": kind, "lab": lab, "text": text}) + "\n")
        return path

    def test_drift_outcome_notes_baseline_drift(self):
        work = self.work_with(**{"routing/a": "status=fail rc=0 duration=9 requires= drift=1"})
        _, _, _, data = self.build(work=work)
        r = self.row(data, "routing/a")
        self.assertEqual((r["result"], r["note"]), ("FAIL", "baseline drift"))

    def test_cleanup_block_failed_on_pass_row(self):
        work = self.work_with(**{"routing/a": "status=pass rc=0 duration=9 requires= cleanup_fail=1",
                                 "routing/c": "status=timeout rc=124 duration=9 requires= cleanup_fail=incomplete"})
        _, _, _, data = self.build(work=work, session=self.session_with())
        r = self.row(data, "routing/a")
        self.assertEqual((r["result"], r["note"]), ("PASS", "cleanup block failed"))
        r = self.row(data, "routing/c")
        self.assertEqual((r["result"], r["note"]), ("FAIL", "timeout; cleanup did not finish"))

    def test_declined_skip(self):
        work = self.work_with(**{"routing/b": "status=skip rc=0 duration=0 requires= reason=declined"})
        _, _, _, data = self.build(work=work)
        self.assertEqual(self.row(data, "routing/b")["result"], "SKIP (declined)")

    def test_sub_second_pass_is_0s(self):
        work = self.work_with(**{"routing/a": "status=pass rc=0 duration=0 requires="})
        _, _, _, data = self.build(work=work, session=self.session_with())
        self.assertEqual(self.row(data, "routing/a")["duration"], "0s")
        self.assertEqual(self.row(data, "routing/f", 1)["duration"], "-")

    def test_session_stopped_marks_unreached_labs(self):
        work = self.work_with(**{"routing/a": "status=fail rc=1 duration=9 requires="})
        s = self.session_with(("failure", "routing/a", "Broke."), ("decision", "routing/a", "stop"))
        _, _, _, data = self.build(work=work, session=s)
        self.assertEqual(self.row(data, "routing/c")["note"], "not run: session stopped")
        self.assertEqual(self.row(data, "routing/a")["note"], "")

    def test_failure_outside_track_is_reported(self):
        s = self.session_with(("failure", "llm/does-not-exist", "No such lab."))
        _, _, text, data = self.build(session=s)
        f = [x for x in data["failures"] if x["lab"] == "llm/does-not-exist"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["note"], "not a track lab")
        self.assertIn("- llm/does-not-exist (not a track lab): No such lab.", text)

    def test_report_names_never_collide(self):
        md1 = self.build()[0]
        md2 = self.build()[0]
        md3 = self.build()[0]
        self.assertEqual(len({md1, md2, md3}), 3)

    def test_adhoc_skips_base_keys(self):
        work = self.work_with(**{"_base/901-uninstall-monitoring": "status=pass rc=0 duration=3 requires=",
                                 "routing/a": "status=pass rc=0 duration=3 requires="})
        keys, _ = report.adhoc_use_cases(work)
        self.assertEqual(keys[0]["keys"], ["routing/a"])

    def test_filename_and_generated_at_share_utc_basis(self):
        md, _, _, data = self.build()
        stamp = data["generated_at"].replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")
        self.assertTrue(os.path.basename(md).startswith(stamp), (md, data["generated_at"]))


class TestChartVersion(unittest.TestCase):
    def fake_helm(self, body):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        path = os.path.join(d, "helm")
        with open(path, "w") as fh:
            fh.write("#!/usr/bin/env bash\n" + body + "\n")
        os.chmod(path, 0o755)
        return d

    def version(self, helm_dir):
        env = {k: v for k, v in os.environ.items() if k != "E2E_CHART_VERSION"}
        env["PATH"] = helm_dir + os.pathsep + env["PATH"]
        with mock.patch.dict(os.environ, env, clear=True):
            return report.chart_version()

    def test_skips_crds_chart_and_strips_prefix(self):
        out = json.dumps([{"chart": "enterprise-agentgateway-crds-v1.2.3"},
                          {"chart": "enterprise-agentgateway-v2026.10.1"}])
        self.assertEqual(self.version(self.fake_helm(f"echo '{out}'")), "v2026.10.1")

    def test_unknown_when_release_gone(self):
        self.assertEqual(self.version(self.fake_helm("echo '[]'")), "unknown")

    def test_unknown_on_failure(self):
        self.assertEqual(self.version(self.fake_helm("echo boom; exit 1")), "unknown")

    def test_env_override_skips_helm(self):
        with mock.patch.dict(os.environ, {"E2E_CHART_VERSION": "v9"}):
            self.assertEqual(report.chart_version(), "v9")


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
