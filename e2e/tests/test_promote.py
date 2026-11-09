"""Tests for promote.py: copy a staged custom lab into the repo, rewrite paths, manifest, undo."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
sys.path.insert(0, LIB)
PROMOTE = os.path.join(LIB, "promote.py")

LAB = """# Demo

The [JWT](../../labs/security/jwt-auth-with-rbac.md) lab is the base. See [above](#demo).

```bash
kubectl apply -f custom-labs/demo/assets/kc.yaml
kubectl create configmap realm \\
  --from-file=custom-labs/demo/assets/realm.json
```
"""

SPEC = """lab: custom-labs/demo/demo.md
description: demo
requirements:
  - id: R1
    text: "something"
steps:
  - probe: "x"
    proves: [R1]
    script: |
      true
"""


class TestPromote(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        for d in ("labs/security", "e2e/specs/security", "lib/keycloak", "custom-labs/demo/assets"):
            os.makedirs(os.path.join(self.root, d))
        self._write("custom-labs/demo/demo.md", LAB)
        self._write("custom-labs/demo/spec.yaml", SPEC)
        self._write("custom-labs/demo/assets/kc.yaml", "kind: Deployment\n")
        self._write("custom-labs/demo/assets/realm.json", "{}\n")
        self._write("labs/security/jwt-auth-with-rbac.md", "# JWT\n")
        self._write("README.md", "# Workshop\n")
        shutil.copytree(os.path.join(self.root, "custom-labs/demo"), self.work("stage"))

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def work(self, rel=""):
        return os.path.join(self.root, "e2e/.work/contribute/demo", rel)

    def run_cli(self, args, check=True):
        proc = subprocess.run([sys.executable, PROMOTE] + args + ["--root", self.root],
                              capture_output=True, text=True)
        if check and proc.returncode != 0:
            self.fail("promote failed (%d): %s%s" % (proc.returncode, proc.stdout, proc.stderr))
        return proc

    def snapshot(self, rel):
        out = {}
        base = os.path.join(self.root, rel)
        for dirpath, dirnames, filenames in os.walk(base):
            reldir = os.path.relpath(dirpath, self.root)
            if reldir == "e2e/.work" or reldir.startswith("e2e/.work/"):
                dirnames[:] = []
                continue
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                with open(p, "rb") as f:
                    out[os.path.relpath(p, self.root)] = f.read()
        return out

    def test_plain_copy_rewrites_paths_and_links(self):
        run, read, exists = self.run_cli, self.read, self.exists
        run(["demo", "--category", "security", "--lib", "kc.yaml=keycloak", "--lib", "realm.json=keycloak"])
        lab = read("labs/security/demo.md")
        self.assertIn("kubectl apply -f lib/keycloak/kc.yaml", lab)
        self.assertIn("--from-file=lib/keycloak/realm.json", lab)
        self.assertIn("[JWT](jwt-auth-with-rbac.md)", lab)
        self.assertNotIn("custom-labs/", lab)
        spec = read("e2e/specs/security/demo.yaml")
        self.assertIn("lab: labs/security/demo.md", spec)
        self.assertIn("requirements:", spec); self.assertIn("proves: [R1]", spec)
        self.assertTrue(exists("lib/keycloak/kc.yaml"))

    def test_subdir_mode(self):
        run, read = self.run_cli, self.read
        run(["demo", "--category", "security", "--subdir"])
        lab = read("labs/security/demo/README.md")
        self.assertIn("kubectl apply -f labs/security/demo/kc.yaml", lab)
        self.assertIn("[JWT](../jwt-auth-with-rbac.md)", lab)
        self.assertIn("lab: labs/security/demo/README.md", read("e2e/specs/security/demo.yaml"))

    def test_asset_without_destination_errors(self):
        run, exists = self.run_cli, self.exists
        proc = run(["demo", "--category", "security"], check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("asset kc.yaml has no destination", proc.stderr + proc.stdout)
        self.assertFalse(exists("labs/security/demo.md"))

    def test_collision_refuses_before_writing(self):
        run, write, exists = self.run_cli, self._write, self.exists
        write("labs/security/demo.md", "existing")
        proc = run(["demo", "--category", "security", "--subdir"], check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("refusing to overwrite labs/security/demo.md", proc.stdout + proc.stderr)
        self.assertFalse(exists("e2e/specs/security/demo.yaml"))

    def test_custom_lab_untouched_and_manifest_written(self):
        run, snapshot, work = self.run_cli, self.snapshot, self.work
        before = snapshot("custom-labs/demo")
        run(["demo", "--category", "security", "--subdir"])
        self.assertEqual(snapshot("custom-labs/demo"), before)
        with open(work("manifest.json")) as f:
            m = json.load(f)
        self.assertIn("labs/security/demo/README.md", m["created"])
        self.assertEqual(m["name"], "demo")

    def test_record_and_undo_restore_tree(self):
        run, snapshot, read, write, exists = self.run_cli, self.snapshot, self.read, self._write, self.exists
        before = snapshot(".")
        run(["demo", "--category", "security", "--subdir"])
        run(["demo", "--record", "README.md"])
        write("README.md", read("README.md") + "- [Demo](labs/security/demo/README.md)\n")
        run(["demo", "--undo"])
        after = snapshot(".")
        after.pop("e2e/.work", None); before.pop("e2e/.work", None)
        self.assertEqual(after, before)
        self.assertFalse(exists("labs/security/demo"))

    def test_dry_run_writes_nothing(self):
        run, exists, work = self.run_cli, self.exists, self.work
        proc = run(["demo", "--category", "security", "--subdir", "--dry-run"])
        self.assertIn("labs/security/demo/README.md", proc.stdout)
        self.assertFalse(exists("labs/security/demo/README.md"))
        self.assertFalse(exists(work("manifest.json")))

    def test_rewrite_lab_keeps_absolute_and_fragment_links(self):
        import promote
        text = "[a](/labs/x.md) [b](https://x.io/y) [c](#top) [d](../../labs/security/x.md#sec)"
        out = promote.rewrite_lab(text, "demo", {}, "custom-labs/demo", "labs/security")
        self.assertIn("[a](/labs/x.md)", out)
        self.assertIn("[b](https://x.io/y)", out)
        self.assertIn("[c](#top)", out)
        self.assertIn("[d](x.md#sec)", out)

    def test_nested_lib_concern_and_undo_keeps_existing_dir(self):
        run, read, exists = self.run_cli, self.read, self.exists
        run(["demo", "--category", "security", "--lib", "kc.yaml=keycloak/sub", "--lib", "realm.json=keycloak/sub"])
        self.assertTrue(exists("lib/keycloak/sub/kc.yaml"))
        self.assertIn("kubectl apply -f lib/keycloak/sub/kc.yaml", read("labs/security/demo.md"))
        run(["demo", "--undo"])
        self.assertFalse(exists("lib/keycloak/sub"))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "lib/keycloak")))

    def test_lib_rejects_traversal(self):
        before = self.snapshot(".")
        for lib in ("x.yaml=..", "../x=keycloak", "kc.yaml=..", "kc.yaml=keycloak/../..", "kc.yaml=/abs", "kc.yaml=a//b", ".=keycloak"):
            proc = self.run_cli(["demo", "--category", "security", "--subdir", "--lib", lib], check=False)
            self.assertEqual(proc.returncode, 1, lib)
        self.assertEqual(self.snapshot("."), before)
        self.assertFalse(self.exists(self.work("manifest.json")))

    def test_partial_failure_is_recorded_and_undoable(self):
        # lib/blocker is a file, so creating lib/blocker/ for the last asset fails.
        self._write("lib/blocker", "not a directory\n")
        proc = self.run_cli(["demo", "--category", "security", "--lib", "kc.yaml=keycloak",
                             "--lib", "realm.json=blocker"], check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(self.exists("labs/security/demo.md"))
        with open(self.work("manifest.json")) as f:
            m = json.load(f)
        self.assertIn("labs/security/demo.md", m["created"])
        self.assertIn("lib/keycloak/kc.yaml", m["created"])
        self.run_cli(["demo", "--undo"])
        self.assertFalse(self.exists("labs/security/demo.md"))
        self.assertFalse(self.exists("lib/keycloak/kc.yaml"))
        self.assertFalse(self.exists("e2e/specs/security/demo.yaml"))

    def test_non_ascii_and_crlf_survive(self):
        text = "# Caf\u00e9 \u2192 demo\r\nkubectl apply -f custom-labs/demo/assets/kc.yaml\r\n"
        with open(self.work("stage/demo.md"), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        self.run_cli(["demo", "--category", "security", "--subdir"])
        with open(os.path.join(self.root, "labs/security/demo/README.md"), "rb") as f:
            got = f.read()
        want = text.replace("custom-labs/demo/assets/kc.yaml", "labs/security/demo/kc.yaml").encode("utf-8")
        self.assertEqual(got, want)

    def test_asset_reference_must_end_the_path(self):
        import promote
        amap = {"custom-labs/demo/assets/kc.yaml": "lib/keycloak/kc.yaml"}
        out = promote.rewrite_lab("a custom-labs/demo/assets/kc.yaml.bak b custom-labs/demo/assets/kc.yaml.",
                                  "demo", amap, "custom-labs/demo", "labs/security")
        self.assertIn("custom-labs/demo/assets/kc.yaml.bak", out)
        self.assertIn("b lib/keycloak/kc.yaml.", out)
        with open(self.work("stage/demo.md"), "a") as f:
            f.write("cp custom-labs/demo/assets/kc.yaml.bak /tmp\n")
        proc = self.run_cli(["demo", "--category", "security", "--subdir"], check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("still references custom-labs/", proc.stderr)
        self.assertFalse(self.exists("labs/security/demo"))

    def test_titled_link_keeps_title(self):
        import promote
        out = promote.rewrite_lab('[JWT](../../labs/security/x.md "JWT lab")', "demo", {},
                                  "custom-labs/demo", "labs/security")
        self.assertEqual(out, '[JWT](x.md "JWT lab")')

    def test_rewrite_rows_deduplicated(self):
        with open(self.work("stage/demo.md"), "a") as f:
            f.write("[again](../../labs/security/jwt-auth-with-rbac.md)\nkubectl apply -f custom-labs/demo/assets/kc.yaml\n")
        proc = self.run_cli(["demo", "--category", "security", "--subdir"])
        self.assertEqual(proc.stdout.count("../../labs/security/jwt-auth-with-rbac.md"), 1)
        with open(self.work("manifest.json")) as f:
            m = json.load(f)
        froms = [(r["file"], r["from"]) for r in m["rewrites"]]
        self.assertEqual(len(froms), len(set(froms)))

    def test_record_rejects_paths_outside_repo(self):
        for path in (os.path.join(self.root, "README.md"), "../outside.md", "labs/../../x.md"):
            proc = self.run_cli(["demo", "--record", path], check=False)
            self.assertEqual(proc.returncode, 1, path)
            self.assertIn("record: %s is outside the repo" % path, proc.stderr)
        self.assertFalse(self.exists(self.work("manifest.json")))
        self.assertFalse(self.exists(self.work("before")))

    def test_undo_skips_missing_saved_copy_and_restores_rest(self):
        self.run_cli(["demo", "--category", "security", "--subdir"])
        self._write("CHANGELOG.md", "0.1.0\n")
        self.run_cli(["demo", "--record", "README.md"])
        self.run_cli(["demo", "--record", "CHANGELOG.md"])
        self._write("README.md", "edited\n")
        self._write("CHANGELOG.md", "edited\n")
        with open(self.work("manifest.json")) as f:
            m = json.load(f)
        os.remove(self.work(m["recorded"][0]["before"]))
        proc = self.run_cli(["demo", "--undo"])
        self.assertIn("skip README.md: no saved copy", proc.stdout)
        self.assertIn("check by hand, no saved copy was restored: README.md", proc.stdout)
        self.assertEqual(self.read("CHANGELOG.md"), "0.1.0\n")
        self.assertEqual(self.read("README.md"), "edited\n")
        self.assertFalse(self.exists("labs/security/demo"))
        self.assertFalse(self.exists(self.work("manifest.json")))

    def test_undo_failing_entry_keeps_manifest(self):
        self.run_cli(["demo", "--category", "security", "--subdir"])
        self._write("docs/notes.md", "notes\n")
        self.run_cli(["demo", "--record", "docs/notes.md"])
        # A file where the recorded file's directory was makes the restore fail with an OSError.
        shutil.rmtree(os.path.join(self.root, "docs"))
        self._write("docs", "not a directory\n")
        proc = self.run_cli(["demo", "--undo"], check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("undo incomplete; manifest kept", proc.stderr)
        self.assertFalse(self.exists("labs/security/demo/README.md"))
        self.assertTrue(self.exists(self.work("manifest.json")))
        self.assertTrue(os.listdir(self.work("before")))

    def test_invalid_name_and_category(self):
        cases = [
            (["../demo", "--category", "security", "--subdir"], "invalid name"),
            (["Demo", "--category", "security", "--subdir"], "invalid name"),
            (["demo", "--category", "../security", "--subdir"], "invalid category"),
            (["demo", "--category", "sec/urity", "--subdir"], "invalid category"),
            (["demo", "--category", "nosuch", "--subdir"], "no such category labs/nosuch/"),
        ]
        for args, msg in cases:
            proc = self.run_cli(args, check=False)
            self.assertEqual(proc.returncode, 1, args)
            self.assertIn(msg, proc.stderr, args)
        self.assertFalse(self.exists("labs/nosuch"))
        self.assertFalse(self.exists("labs/security/demo"))

    def test_leftover_in_text_asset_errors(self):
        with open(self.work("stage/assets/kc.yaml"), "a") as f:
            f.write("# copied from custom-labs/x/assets/y\n")
        proc = self.run_cli(["demo", "--category", "security", "--subdir"], check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("labs/security/demo/kc.yaml still references custom-labs/", proc.stderr)
        self.assertFalse(self.exists("labs/security/demo"))

    def test_gitignored_target_warns(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        subprocess.run(["git", "init", "-q", self.root], check=True)
        self._write(".gitignore", "*secret*\n")
        self._write("e2e/.work/contribute/demo/stage/assets/my-secret.yaml", "kind: Secret\n")
        proc = self.run_cli(["demo", "--category", "security", "--subdir", "--dry-run"])
        self.assertIn("warning: labs/security/demo/my-secret.yaml is gitignored and will not be committed", proc.stdout)
        self.assertNotIn("warning: labs/security/demo/kc.yaml", proc.stdout)

    def test_no_warning_outside_a_repo(self):
        proc = self.run_cli(["demo", "--category", "security", "--subdir", "--dry-run"])
        self.assertNotIn("warning:", proc.stdout + proc.stderr)

    def read(self, rel):
        with open(os.path.join(self.root, rel)) as f:
            return f.read()

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))


if __name__ == "__main__":
    unittest.main()
