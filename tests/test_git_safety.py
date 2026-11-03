"""Regression tests for pome's manuscript safety and Git sync.

Run with: python3 -m unittest discover -s tests -v
No remote Git repository or network connection is needed.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pome


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pome-git-safety-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "pome tests")
        self.path = self.repo / "原稿.md"
        self.other = self.repo / "資料.md"
        self.path.write_text("元の原稿", encoding="utf-8")
        self.other.write_text("人物ノート", encoding="utf-8")
        self.git("add", "--", "原稿.md", "資料.md")
        self.git("commit", "-qm", "initial")
        self.patchers = [
            patch.object(pome, "CONFIG_DIR", str(self.root / "config")),
            patch.object(pome, "CONFIG_PATH", str(self.root / "config/config.json")),
            patch.object(pome, "HISTORY_DIR", str(self.root / "history")),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.editor = pome.Editor(str(self.path))
        self.messages = []
        self.editor.msgbox = lambda title, lines: self.messages.append((title, lines))
        self.editor.flash_now = lambda *args: None

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True, capture_output=True, text=True)

    def test_staged_unrelated_file_blocks_push_without_commit(self):
        self.other.write_text("ステージ済みの無関係な変更", encoding="utf-8")
        self.git("add", "--", "資料.md")
        before = self.git("rev-parse", "HEAD").stdout.strip()
        self.editor.act_git_push()
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), before)
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.strip(), "資料.md")
        self.assertIn("失敗", self.messages[-1][0])

    def test_commit_contains_only_manuscript_and_push_failure_is_reported(self):
        self.path.write_text("外部変更", encoding="utf-8")
        # Reopen after external change, then edit in pome.
        self.editor.load_file(str(self.path))
        self.editor.lines = ["更新した原稿"]
        self.editor.dirty = True
        self.other.write_text("ステージしていない資料の変更", encoding="utf-8")
        self.editor.act_git_push()
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
        self.assertEqual(changed.stdout.splitlines(), ["原稿.md"])
        self.assertIn("失敗", self.messages[-1][0])  # no remote configured
        self.assertEqual(self.path.read_text(encoding="utf-8"), "更新した原稿")
        self.assertEqual(self.other.read_text(encoding="utf-8"), "ステージしていない資料の変更")

    def test_pull_refuses_uncommitted_work(self):
        self.other.write_text("未コミットの資料", encoding="utf-8")
        self.editor.act_git_pull()
        self.assertIn("失敗", self.messages[-1][0])
        self.assertTrue(any("未コミット" in line for line in self.messages[-1][1]))
        self.assertEqual(self.editor.lines, ["元の原稿"])

    def test_pull_refuses_external_edits_to_open_document(self):
        self.path.write_text("外部から変更された原稿", encoding="utf-8")
        self.editor.act_git_pull()
        self.assertIn("失敗", self.messages[-1][0])
        self.assertTrue(any("外部" in line for line in self.messages[-1][1]))
        self.assertEqual(self.editor.lines, ["元の原稿"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "外部から変更された原稿")

    def test_noop_pull_preserves_cursor_and_undo_stack(self):
        self.editor.cy = 0
        self.editor.cx = 3
        self.editor.undo_stack.append(("sentinel",))
        original_git_run = self.editor.git_run

        def fake_git_run(repo, args):
            if args[0] == "pull":
                return subprocess.CompletedProcess(args, 0, stdout="Already up to date.\n", stderr="")
            return original_git_run(repo, args)

        self.editor.git_run = fake_git_run
        with patch.object(self.editor, "load_file", side_effect=AssertionError("unnecessary reload")):
            self.editor.act_git_pull()
        self.assertEqual(self.editor.cx, 3)
        self.assertEqual(self.editor.undo_stack, [("sentinel",)])
        self.assertIn("完了", self.messages[-1][0])

    def test_pull_failure_does_not_reload_document(self):
        original_git_run = self.editor.git_run

        def fake_git_run(repo, args):
            if args[0] == "pull":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="no upstream")
            return original_git_run(repo, args)

        self.editor.git_run = fake_git_run
        with patch.object(self.editor, "load_file", side_effect=AssertionError("reload on error")):
            self.editor.act_git_pull()
        self.assertIn("失敗", self.messages[-1][0])


class RecoveryHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pome-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patchers = [
            patch.object(pome, "CONFIG_DIR", str(self.root / "config")),
            patch.object(pome, "CONFIG_PATH", str(self.root / "config/config.json")),
            patch.object(pome, "HISTORY_DIR", str(self.root / "history")),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = self.root / "原稿.txt"
        self.path.write_text("本文", encoding="utf-8")
        self.editor = pome.Editor(str(self.path))
        self.editor.msgbox = lambda *args: None

    def test_emergency_rescues_never_overwrite_previous_rescue(self):
        self.editor.lines = ["失いたくない一つ目"]
        self.editor.dirty = True
        first = self.editor.emergency_save()
        self.editor.lines = ["失いたくない二つ目"]
        second = self.editor.emergency_save()
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(encoding="utf-8"), "失いたくない一つ目")
        self.assertEqual(Path(second).read_text(encoding="utf-8"), "失いたくない二つ目")

    def test_config_replace_failure_preserves_previous_file(self):
        path = Path(pome.CONFIG_PATH)
        old_content = path.read_bytes()
        with patch.object(pome.os, "replace", side_effect=OSError("test failure")):
            self.editor.config["autosave"] = False
            self.editor.save_config()
        self.assertEqual(path.read_bytes(), old_content)
        self.assertEqual(list(path.parent.glob(".pome-config-*")), [])


if __name__ == "__main__":
    unittest.main()
