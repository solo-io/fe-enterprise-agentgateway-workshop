"""Tests for labdoc.py — pure parse/plan logic, no cluster needed."""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import labdoc  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
LAB = os.path.join(FIX, "mini-lab.md")
SPEC = os.path.join(FIX, "mini-spec.yaml")


class TestNormalize(unittest.TestCase):
    def test_strips_markdown(self):
        self.assertEqual(labdoc.normalize("Deploy `echo` **now** [here](x.md)"),
                         "deploy echo now here")

    def test_collapses_space(self):
        self.assertEqual(labdoc.normalize("  A   B  "), "a b")


class TestParseLab(unittest.TestCase):
    def setUp(self):
        self.sections = labdoc.parse_lab(LAB)

    def test_only_bash_blocks_collected(self):
        send = [s for s in self.sections if s["norm"] == "send a request"][0]
        self.assertEqual(len(send["blocks"]), 1)  # json block ignored

    def test_indented_fence_dedented(self):
        send = [s for s in self.sections if s["norm"] == "send a request"][0]
        self.assertTrue(send["blocks"][0].startswith("curl -i"))

    def test_block_order_preserved(self):
        deploy = [s for s in self.sections if s["norm"] == "deploy the echo server"][0]
        self.assertIn("create namespace", deploy["blocks"][0])
        self.assertIn("apply", deploy["blocks"][1])


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.sections = labdoc.parse_lab(LAB)

    def test_missing_section_is_drift(self):
        with self.assertRaises(SystemExit) as cm:
            labdoc.find_section(self.sections, "No Such Heading", LAB)
        self.assertIn("DRIFT", str(cm.exception))

    def test_out_of_range_ordinal_is_drift(self):
        deploy = labdoc.find_section(self.sections, "Deploy the echo server", LAB)
        with self.assertRaises(SystemExit) as cm:
            labdoc.select_blocks(deploy, [3], LAB, "t")
        self.assertIn("DRIFT", str(cm.exception))

    def test_run_selects_one_based(self):
        deploy = labdoc.find_section(self.sections, "Deploy the echo server", LAB)
        chosen = labdoc.select_blocks(deploy, [2], LAB, "t")
        self.assertEqual(chosen[0][0], 2)
        self.assertIn("apply", chosen[0][1])


class TestDriver(unittest.TestCase):
    def build(self):
        self.workdir = tempfile.mkdtemp(prefix="labdoc-test-")
        return labdoc.build_driver(LAB, SPEC, self.workdir, repo_root="/REPO")

    def test_block_numbering_monotonic_across_cleanup(self):
        driver = self.build()
        nums = re.findall(r"blocks/(\d{4})\.sh", driver)
        # e2e_define_cleanup() {...} is written into the driver BEFORE the
        # steps phase (bash needs the function defined before the EXIT trap
        # can fire), so blocks/NNNN.sh references appear in the text as
        # cleanup, then steps ('0003', '0001', '0002') — not ascending. The
        # BlockCounter itself is still monotonic: ordinals are unique, and
        # cleanup's block gets the highest number since it is numbered AFTER
        # every step regardless of where its text lands.
        self.assertEqual(len(nums), len(set(nums)), "block ordinals must be unique")
        # steps write blocks 0001 (run:[2]) and 0002; cleanup continues at 0003
        self.assertEqual(max(nums), "0003")

    def test_retry_only_on_last_block(self):
        driver = self.build()
        runs = [l for l in driver.splitlines() if l.startswith("e2e_run_block")]
        step_runs = runs[:2]  # cleanup's block is the third
        # e2e_run_block <block> <tag> <attempts> <delay> <until> <allow>
        # shlex.quote leaves shell-safe tokens (1, 2, HTTP/) unquoted and
        # renders the empty until-regex as ''.
        # step 1 (run: [2], no retry): attempts 1, delay 2, empty until
        self.assertIn(" 1 2 '' ", step_runs[0])
        # step 2 (retry attempts 3, delay 1, until HTTP/)
        self.assertIn(" 3 1 HTTP/ ", step_runs[1])

    def test_wait_after_last_block_and_assertions_present(self):
        driver = self.build()
        lines = driver.splitlines()
        run_idx = [i for i, l in enumerate(lines) if l.startswith("e2e_run_block")]
        wait_idx = [i for i, l in enumerate(lines) if l.startswith("e2e_wait")]
        self.assertTrue(wait_idx and wait_idx[0] > run_idx[0])
        # shlex.quote leaves 200/echo unquoted (shell-safe tokens)
        self.assertTrue(any(l.startswith("assert_status 200 ") for l in lines))
        self.assertTrue(any(l.startswith("assert_contains echo ") for l in lines))

    def test_cleanup_wrapped_in_function(self):
        driver = self.build()
        body = driver.split("e2e_define_cleanup() {")[1].split("}")[0]
        self.assertIn("e2e_phase cleanup", body)


class TestUncovered(unittest.TestCase):
    def test_uncovered_sections_lists_only_unnamed(self):
        self.assertEqual(labdoc.uncovered_sections(LAB, SPEC), [])


class TestResourceVerb(unittest.TestCase):
    def build(self):
        workdir = tempfile.mkdtemp(prefix="labdoc-test-")
        spec = os.path.join(FIX, "resource-spec.yaml")
        return labdoc.build_driver(LAB, spec, workdir, repo_root="/REPO")

    def test_emits_assert_call_with_defaults(self):
        driver = self.build()
        lines = [l for l in driver.splitlines()
                 if l.startswith("e2e_assert_resource_field")]
        self.assertEqual(len(lines), 2)
        # shlex.quote leaves these tokens unquoted (shell-safe chars only)
        self.assertIn(" mini ", lines[0])
        self.assertIn(" deploy/echo ", lines[0])
        self.assertIn(" .status.readyReplicas ", lines[0])
        # default namespace + explicit name as the (space-bearing, quoted) label
        self.assertIn(" agentgateway-system ", lines[1])
        self.assertIn("'gateway listens on 8080'", lines[1])

    def test_resource_step_writes_no_block(self):
        driver = self.build()
        self.assertEqual(driver.count("e2e_run_block"), 2)  # only the section step

    def test_missing_expect_is_error(self):
        import yaml as _y
        bad = {"lab": "e2e/tests/fixtures/mini-lab.md", "tier": "t0",
               "steps": [{"resource": "deploy/echo", "jsonpath": ".spec.replicas"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            _y.safe_dump(bad, f); path = f.name
        with self.assertRaises(SystemExit):
            labdoc.build_driver(LAB, path, tempfile.mkdtemp(), repo_root="/REPO")


if __name__ == "__main__":
    unittest.main()
