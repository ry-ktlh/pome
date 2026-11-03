"""Exercise the user-local installer without writing to the user's home."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pome-install-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.prefix = self.base / "user prefix"
        self.source = self.base / "source checkout"
        self.source.mkdir()
        for name in ("install.sh", "writing-env.sh", "pome.py"):
            shutil.copyfile(ROOT / name, self.source / name)
        self.env = dict(os.environ, POME_PREFIX=str(self.prefix))

    def install(self, success=True):
        result = subprocess.run(["sh", str(self.source / "install.sh")],
                                cwd=self.base, env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def stub_editor(self, version=1):
        (self.source / "pome.py").write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            "print(json.dumps([%d, sys.argv[1:]]))\n" % version)

    def test_install_reinstall_and_real_version(self):
        self.install()
        self.install()
        result = subprocess.run([str(self.prefix / "bin/write"), "--version"],
                                text=True, capture_output=True, check=True)
        self.assertTrue(result.stdout.startswith("pome "), result.stdout)
        self.assertEqual((self.prefix / "bin/pome").read_bytes(),
                         (self.source / "pome.py").read_bytes())

    def test_launcher_forwards_paths_and_no_arguments_and_upgrades(self):
        self.stub_editor()
        self.install()
        launcher = str(self.prefix / "bin/write")
        for args in ([], ["日本語 原稿.txt"]):
            result = subprocess.run([launcher] + args, text=True,
                                    capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout), [1, args])
        result = subprocess.run([launcher, "one.txt", "two.txt"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.stub_editor(version=2)
        self.install()
        result = subprocess.run([launcher], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [2, []])

    def test_existing_unrelated_file_prevents_partial_install(self):
        target = self.prefix / "bin/write"
        target.parent.mkdir(parents=True)
        target.write_text("user's own write command")
        self.install(success=False)
        self.assertEqual(target.read_text(), "user's own write command")
        self.assertFalse((self.prefix / "bin/pome").exists())

    def test_local_edits_and_symlinks_are_preserved(self):
        self.install()
        target = self.prefix / "bin/pome"
        target.write_text("locally edited editor")
        self.install(success=False)
        self.assertEqual(target.read_text(), "locally edited editor")
        target.unlink()
        elsewhere = self.base / "elsewhere"
        elsewhere.write_text("keep")
        target.symlink_to(elsewhere)
        self.install(success=False)
        self.assertTrue(target.is_symlink())
        self.assertEqual(elsewhere.read_text(), "keep")

    def test_activation_prefers_pome_and_is_repeatable(self):
        self.install()
        competing = self.base / "competing"
        competing.mkdir()
        competitor = competing / "pome"
        competitor.write_text("#!/bin/sh\nexit 1\n")
        competitor.chmod(0o755)
        env = dict(self.env, EDITOR="vim", VISUAL="vim",
                   PATH=str(competing) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(
            ["sh", "-c", '. "$POME_PREFIX/share/pome/writing-env.sh"; '
             'first=$PATH; . "$POME_PREFIX/share/pome/writing-env.sh"; '
             '[ "$PATH" = "$first" ] || exit 9; '
             'printf "%s\\n" "$EDITOR" "$VISUAL"; command -v pome'],
            env=env, text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.splitlines(),
                         ["pome", "pome", str(self.prefix / "bin/pome")])


if __name__ == "__main__":
    unittest.main()
