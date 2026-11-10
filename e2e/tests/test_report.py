"""Tests for report.py: result mapping, markdown layout, compare."""
import json
import os
import re
import shutil
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

    def build(self, work=None, session=None, extra=()):
        work = work or os.path.join(FIX, "work")
        proc = subprocess.run(
            [sys.executable, os.path.join(LIB, "report.py"), "build", "--work", work,
             "--track", "t", "--env-json", os.path.join(FIX, "env.json"),
             "--session", session or self.session, "--out", self.tmp.name,
             "--specs-dir", os.path.join(FIX, "specs"), "--tracks-dir", os.path.join(FIX, "tracks"), *extra],
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

    def test_env_class_overrides_detected_class(self):
        md, _, text, data = self.build(extra=("--env-class", "eks"))
        self.assertRegex(os.path.basename(md), r"^\d{8}-\d{6}-eks-t\.md$")
        self.assertIn("track on eks", text.splitlines()[0])
        self.assertEqual((data["environment"]["class"], data["environment"]["detected_class"]), ("eks", "vind"))
        # With no outcome file, the plan for the run's class decides the row.
        work = os.path.join(self.tmp.name, "w")
        shutil.copytree(os.path.join(FIX, "work"), work)
        os.remove(os.path.join(work, "routing", "e", "outcome"))
        _, _, _, vind = self.build(work=work)
        _, _, _, eks = self.build(work=work, extra=("--env-class", "eks"))
        self.assertEqual(self.row(vind, "routing/e", 1)["result"], "NOT APPLICABLE (only on eks)")
        self.assertNotIn("NOT APPLICABLE", self.row(eks, "routing/e", 1)["result"])

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

    def test_adhoc_report_ignores_custom_outcomes(self):
        work = os.path.join(self.tmp.name, "work")
        os.makedirs(os.path.join(work, "custom", "x"))
        os.makedirs(os.path.join(work, "routing", "a"))
        for k in ("custom/x", "routing/a"):
            with open(os.path.join(work, k, "outcome"), "w") as fh:
                fh.write("status=pass rc=0 duration=1 requires=\n")
        ucs, _ = report.adhoc_use_cases(work)
        self.assertEqual(ucs[0]["keys"], ["routing/a"])

    def test_filename_and_generated_at_share_utc_basis(self):
        md, _, _, data = self.build()
        stamp = data["generated_at"].replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")
        self.assertTrue(os.path.basename(md).startswith(stamp), (md, data["generated_at"]))


def requirement_rows_in(text):
    """The rows the contribute-lab preflight reads: `| R<n> ...` lines under ## Requirements."""
    sect = text.split("\n## Requirements\n", 1)[1]
    sect = re.split(r"(?m)^## ", sect, maxsplit=1)[0]
    return [ln for ln in sect.splitlines() if re.match(r"^\| R[0-9]+ ", ln)]


class TestRequirements(ReportCase):
    REQ_SPEC = ("lab: labs/x.md\ntier: t0\nrequirements:\n"
                "  - id: R1\n    text: the route answers\n"
                "  - id: R2\n    text: a bad key | gets 401\n"
                "steps:\n  - section: A\n    proves: [R1]\n    assert: [{status: 200}]\n"
                "  - section: B\n    proves: [R2]\n    assert: [{status: 401}]\n")

    def setUp(self):
        super().setUp()
        self.specs = os.path.join(self.tmp.name, "specs")
        shutil.copytree(os.path.join(FIX, "specs"), self.specs)
        self.work = os.path.join(self.tmp.name, "w")
        shutil.copytree(os.path.join(FIX, "work"), self.work)
        for key in ("routing/a", "routing/b"):
            with open(os.path.join(self.specs, key + ".yaml"), "w") as fh:
                fh.write(self.REQ_SPEC)

    def steps(self, key, *lines):
        with open(os.path.join(self.work, key, "steps"), "w") as fh:
            fh.write("".join(ln + "\n" for ln in lines))

    def build_reqs(self):
        return self.build(work=self.work, extra=("--specs-dir", self.specs))

    def rows(self, outcome, steps, expected=None):
        reqs = [{"id": "R1", "text": "one"}, {"id": "R2", "text": "two"}]
        row = report.lab_row("k", outcome, {}, self.specs) if outcome else \
            {"key": "k", "result": "FAIL", "duration": "-", "note": "no outcome recorded"}
        return [(r["result"], r["detail"]) for r in
                report.requirement_rows("k", outcome, row, reqs, expected or {"R1": 1, "R2": 1}, steps)]

    def test_rows_follow_the_proving_steps(self):
        self.steps("routing/a", "step=1 status=pass req=R1", "step=2 status=fail req=R2")
        _, _, text, data = self.build_reqs()
        self.assertEqual(requirement_rows_in(text)[:2], ["| R1 | the route answers | PASS |",
                                                     "| R2 | a bad key \\| gets 401 | FAIL |"])
        self.assertIn("- R2: FAIL, failed in step 2", text)
        a = [r for r in data["requirements"] if r["lab"] == "routing/a"]
        self.assertEqual([(r["id"], r["result"]) for r in a], [("R1", "PASS"), ("R2", "FAIL")])

    def test_lab_in_two_use_cases_is_listed_once_and_skipped_lab_is_skip(self):
        self.steps("routing/a", "step=1 status=pass req=R1", "step=2 status=pass req=R2")
        _, _, text, data = self.build_reqs()
        self.assertEqual([r["lab"] for r in data["requirements"]], ["routing/a"] * 2 + ["routing/b"] * 2)
        self.assertEqual({r["result"] for r in data["requirements"] if r["lab"] == "routing/b"}, {"SKIP"})
        self.assertEqual(text.count("### routing/a"), 1)
        self.assertEqual([ln.endswith("| PASS |") for ln in requirement_rows_in(text)], [True, True, False, False])

    def test_section_sits_after_the_lab_tables(self):
        _, _, text, _ = self.build_reqs()
        heads = re.findall(r"^## (.+)$", text, re.M)
        self.assertEqual(heads[:5], ["Environment", "First", "Second", "Requirements", "Deviations"])

    def test_no_requirements_no_section(self):
        _, _, text, data = self.build()
        self.assertNotIn("## Requirements", text)
        self.assertEqual(data["requirements"], [])

    def test_unreached_step_fails(self):
        self.assertEqual(self.rows({"status": "timeout"}, [("1", "pass", "R1")]),
                         [("PASS", ""), ("FAIL", "1 of 1 proving step(s) did not run")])

    def test_no_steps_file_fails(self):
        self.assertEqual({r for r, _ in self.rows({"status": "pass"}, None)}, {"FAIL"})

    def test_lab_that_did_not_run(self):
        self.assertEqual(self.rows({"status": "skip", "requires": "K"}, None)[0], ("SKIP", "skip (missing k)"))
        self.assertEqual(self.rows({"status": "excluded"}, None)[0][0], "SKIP")
        self.assertEqual(self.rows(None, None)[0], ("FAIL", "no outcome recorded"))

    def test_every_proving_step_skipped_is_skip(self):
        got = self.rows({"status": "pass"}, [("1", "skip", "R1"), ("2", "pass", "R2")])
        self.assertEqual(got, [("SKIP", "every proving step was skipped"), ("PASS", "")])

    def test_any_failed_step_fails_a_requirement_with_two_steps(self):
        got = self.rows({"status": "fail"}, [("1", "pass", "R1"), ("3", "fail", "R1")], {"R1": 2, "R2": 0})
        self.assertEqual(got, [("FAIL", "failed in step 3"), ("FAIL", "no step proves it")])

    def test_custom_report_reads_the_custom_root(self):
        root = os.path.join(self.tmp.name, "custom-labs")
        os.makedirs(os.path.join(root, "demo"))
        with open(os.path.join(root, "demo", "spec.yaml"), "w") as fh:
            fh.write(self.REQ_SPEC)
        work = os.path.join(self.tmp.name, "cw")
        os.makedirs(os.path.join(work, "custom", "demo"))
        with open(os.path.join(work, "custom", "demo", "outcome"), "w") as fh:
            fh.write("status=pass rc=0 duration=5 requires= leftover_warn=0\n")
        with open(os.path.join(work, "custom", "demo", "steps"), "w") as fh:
            fh.write("step=1 status=pass req=R1\nstep=2 status=pass req=R2\n")
        proc = subprocess.run(
            [sys.executable, os.path.join(LIB, "report.py"), "build", "--work", work, "--custom", "demo",
             "--env-json", os.path.join(FIX, "env.json"), "--session", self.session, "--out", self.tmp.name,
             "--custom-root", root], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(proc.stdout.split()[0], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("| custom/demo | PASS |", text)
        rows = requirement_rows_in(text)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.endswith("| PASS |") for r in rows), rows)

    def test_custom_report_reads_the_run_in_the_lab_folder(self):
        # run-e2e.sh writes a custom lab's outcome, steps and leftovers to
        # custom-labs/<name>/.work/run, and the work root has no custom/ dir.
        root = os.path.join(self.tmp.name, "custom-labs")
        run = os.path.join(root, "demo", ".work", "run")
        os.makedirs(run)
        with open(os.path.join(root, "demo", "spec.yaml"), "w") as fh:
            fh.write(self.REQ_SPEC)
        with open(os.path.join(run, "outcome"), "w") as fh:
            fh.write("status=pass rc=0 duration=5 requires= leftover=0\n")
        with open(os.path.join(run, "steps"), "w") as fh:
            fh.write("step=1 status=pass req=R1\nstep=2 status=pass req=R2\n")
        with open(os.path.join(run, "leftovers.txt"), "w") as fh:
            fh.write("LEFTOVER: agentgateway-system secret/x\n")
        work = os.path.join(self.tmp.name, "empty-work")
        os.makedirs(work)
        proc = subprocess.run(
            [sys.executable, os.path.join(LIB, "report.py"), "build", "--work", work, "--custom", "demo",
             "--env-json", os.path.join(FIX, "env.json"), "--session", self.session, "--out", self.tmp.name,
             "--custom-root", root], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(proc.stdout.split()[0], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("| custom/demo | PASS |", text)
        self.assertEqual(len(requirement_rows_in(text)), 2)
        self.assertIn("secret/x", text)


class TestLeftoverWarnings(ReportCase):
    def test_warnings_get_their_own_section(self):
        work = os.path.join(self.tmp.name, "w")
        shutil.copytree(os.path.join(FIX, "work"), work)
        with open(os.path.join(work, "routing", "a", "outcome"), "w") as fh:
            fh.write("status=pass rc=0 duration=5 requires= leftover_warn=1\n")
        with open(os.path.join(work, "leftovers.txt"), "w") as fh:
            fh.write("LEFTOVER (warning): namespace/mcp (left by routing/a)\n")
        _, _, text, data = self.build(work=work)
        self.assertEqual(data["leftovers"], [])
        self.assertEqual(data["leftover_warnings"], ["LEFTOVER (warning): namespace/mcp (left by routing/a)"])
        self.assertEqual(self.row(data, "routing/a")["result"], "PASS (deviation)")
        self.assertIn("1 leftover warning(s)", self.row(data, "routing/a")["note"])
        heads = re.findall(r"^## (.+)$", text, re.M)
        self.assertEqual(heads[-3:], ["Leftovers", "Leftover warnings", "Questions"])
        self.assertIn("- LEFTOVER (warning): namespace/mcp (left by routing/a)", text)
        self.assertIn(report.NO_LEFTOVERS_BUT_WARNINGS, text)

    def test_zero_warnings_add_no_note(self):
        work = os.path.join(self.tmp.name, "w")
        os.makedirs(os.path.join(work, "routing", "a"))
        with open(os.path.join(work, "routing", "a", "outcome"), "w") as fh:
            fh.write("status=pass rc=0 duration=5 requires= leftover_warn=0\n")
        empty = os.path.join(self.tmp.name, "empty.jsonl")
        open(empty, "w").close()
        _, _, text, data = self.build(work=work, session=empty)
        self.assertEqual(self.row(data, "routing/a")["note"], "")
        self.assertNotIn("## Leftover warnings", text)


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


class TestCustom(ReportCase):
    def build_custom(self, outcome_line=None, session=None):
        work = os.path.join(self.tmp.name, "work")
        if outcome_line is not None:
            os.makedirs(os.path.join(work, "custom", "demo"), exist_ok=True)
            with open(os.path.join(work, "custom", "demo", "outcome"), "w") as fh:
                fh.write(outcome_line + "\n")
        else:
            os.makedirs(work, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, os.path.join(LIB, "report.py"), "build", "--work", work,
             "--custom", "demo", "--env-json", os.path.join(FIX, "env.json"),
             "--session", session or self.session, "--out", self.tmp.name,
             "--specs-dir", os.path.join(FIX, "specs"), "--tracks-dir", os.path.join(FIX, "tracks"),
             "--custom-root", os.path.join(self.tmp.name, "no-custom-labs")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        md, js = proc.stdout.split()
        with open(md, encoding="utf-8") as fh:
            text = fh.read()
        with open(js, encoding="utf-8") as fh:
            data = json.load(fh)
        return md, text, data

    def test_filename_and_title(self):
        md, text, data = self.build_custom("status=pass rc=0 duration=5 requires=")
        self.assertRegex(os.path.basename(md), r"^\d{8}-\d{6}-[a-z0-9-]+-custom-demo\.md$")
        self.assertTrue(text.startswith("# Agentgateway custom lab report: demo on "))
        self.assertEqual(data["use_cases"][0]["name"], "Custom lab")
        self.assertEqual(self.row(data, "custom/demo")["result"], "PASS")

    def test_keep_and_leftover_notes(self):
        _, _, data = self.build_custom("status=pass rc=0 duration=5 requires= keep=1")
        self.assertEqual(self.row(data, "custom/demo")["note"], "kept on the cluster")
        _, _, data = self.build_custom("status=pass rc=0 duration=5 requires= leftover=2")
        self.assertEqual(self.row(data, "custom/demo")["note"], "2 leftover(s)")

    def test_missing_outcome_is_fail_row(self):
        _, _, data = self.build_custom(None)
        row = self.row(data, "custom/demo")
        self.assertEqual((row["result"], row["note"]), ("FAIL", "no outcome recorded"))

    def test_run_events_and_keep_custom(self):
        session = os.path.join(self.tmp.name, "s.jsonl")
        with open(session, "w") as fh:
            fh.write(json.dumps({"ts": "t", "kind": "run", "lab": "custom/demo", "text": "status=fail rc=1"}) + "\n")
            fh.write(json.dumps({"ts": "t", "kind": "keep-custom", "lab": "custom/demo", "text": "kept"}) + "\n")
        _, text, _ = self.build_custom("status=pass rc=0 duration=5 requires=", session=session)
        self.assertIn("## Runs\n\n- custom/demo: status=fail rc=1", text)
        self.assertIn("The custom lab was kept on the cluster.", text)

    def test_custom_report_is_scoped_to_its_lab(self):
        work = os.path.join(self.tmp.name, "work")
        os.makedirs(os.path.join(work, "custom", "demo"))
        with open(os.path.join(work, "leftovers.txt"), "w") as fh:
            fh.write("BASELINE DRIFT: routing/other\n")
        with open(os.path.join(work, "custom", "demo", "leftovers.txt"), "w") as fh:
            fh.write("LEFTOVER: agentgateway-system secret/x\n")
        session = os.path.join(self.tmp.name, "s.jsonl")
        events = [
            {"kind": "failure", "lab": "routing/other", "text": "unrelated failure"},
            {"kind": "deviation", "lab": "routing/other", "text": "unrelated deviation"},
            {"kind": "decision", "lab": "routing/other", "text": "unrelated decision"},
            {"kind": "run", "lab": "custom/other", "text": "other custom run"},
            {"kind": "question", "lab": "custom/other", "text": "other question"},
            {"kind": "failure", "lab": "custom/demo", "text": "demo failure"},
            {"kind": "question", "lab": "", "text": "general question"},
        ]
        with open(session, "w") as fh:
            for e in events:
                fh.write(json.dumps(dict(ts="t", **e)) + "\n")
        _, text, data = self.build_custom("status=fail rc=0 duration=5 requires= leftover=1", session=session)
        self.assertEqual(data["leftovers"], ["LEFTOVER: agentgateway-system secret/x"])
        self.assertNotIn("routing/other", text)
        self.assertNotIn("custom/other", text)
        self.assertNotIn("other question", text)
        self.assertIn("demo failure", text)
        self.assertIn("general question", text)
        self.assertEqual([f["lab"] for f in data["failures"]], ["custom/demo"])

    def test_leftover_check_not_run_note(self):
        _, _, data = self.build_custom("status=fail rc=0 duration=5 requires= leftover_check=not-run")
        self.assertEqual(self.row(data, "custom/demo")["note"], "leftover check not run")

    def test_track_and_custom_exclusive(self):
        proc = subprocess.run([sys.executable, os.path.join(LIB, "report.py"), "build", "--work", self.tmp.name,
                               "--track", "llm", "--custom", "x", "--env-json", os.path.join(FIX, "env.json"),
                               "--session", self.session, "--out", self.tmp.name], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
