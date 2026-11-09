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

    def read(self, rel):
        with open(os.path.join(self.root, rel)) as f:
            return f.read()

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))


if __name__ == "__main__":
    unittest.main()
