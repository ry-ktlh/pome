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
        self.assertEqual(self.git("diff", "--cached", "--name-only", "-z").stdout.strip("\0"), "資料.md")
        self.assertIn("失敗", self.messages[-1][0])

    def test_commit_contains_only_manuscript_and_push_failure_is_reported(self):
        self.path.write_text("外部変更", encoding="utf-8")
        # Reopen after external change, then edit in pome.
        self.editor.load_file(str(self.path))
        self.editor.lines = ["更新した原稿"]
        self.editor.dirty = True
        self.other.write_text("ステージしていない資料の変更", encoding="utf-8")
        self.editor.act_git_push()
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-z", "-r", "HEAD")
        self.assertEqual([p for p in changed.stdout.split("\0") if p], ["原稿.md"])
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


    def attach_bare_remote(self):
        """Give the test repository an entirely local bare origin."""
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)],
                       check=True, capture_output=True)
        self.git("branch", "-M", "main")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-u", "origin", "main")
        subprocess.run(["git", "-C", str(remote), "symbolic-ref",
                        "HEAD", "refs/heads/main"], check=True, capture_output=True)
        return remote

    def clone_remote(self, remote):
        clone = self.root / "other"
        subprocess.run(["git", "clone", "-q", str(remote), str(clone)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone), "config",
                        "user.email", "other@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(clone), "config",
                        "user.name", "other test"], check=True)
        return clone

    def test_push_refuses_external_change_without_sending(self):
        self.attach_bare_remote()
        original_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertFalse(self.editor.dirty)
        self.path.write_text("外部アプリが書き換えた原稿", encoding="utf-8")
        self.editor.act_git_push()
        self.assertIn("失敗", self.messages[-1][0])
        self.assertTrue(any("外部" in x for x in self.messages[-1][1]))
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), original_head)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "外部アプリが書き換えた原稿")
        self.assertEqual(self.git("status", "--porcelain", "-z").stdout.split("\0")[0], " M 原稿.md")

    def test_push_refuses_deleted_file_without_recreating(self):
        self.attach_bare_remote()
        before = self.git("rev-parse", "HEAD").stdout.strip()
        self.path.unlink()
        self.editor.act_git_push()
        self.assertIn("失敗", self.messages[-1][0])
        self.assertFalse(self.path.exists())
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), before)

    def test_commit_failure_then_retry_sends_only_manuscript(self):
        remote = self.attach_bare_remote()
        original_git_run = self.editor.git_run
        failed_once = [False]
        self.editor.lines = ["一度目は失敗しても失わない原稿"]
        self.editor.dirty = True
        self.other.write_text("ステージしていない別ファイル", encoding="utf-8")

        def fail_commit_once(repo, args):
            if args[0] == "commit" and not failed_once[0]:
                failed_once[0] = True
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="simulated commit hook failure")
            return original_git_run(repo, args)

        self.editor.git_run = fail_commit_once
        self.editor.act_git_push()
        self.assertIn("失敗", self.messages[-1][0])
        self.assertEqual([x for x in self.git("diff", "--cached", "--name-only", "-z").stdout.split("\0") if x],
                         ["原稿.md"])
        self.editor.git_run = original_git_run
        self.editor.act_git_push()
        self.assertIn("完了", self.messages[-1][0])
        other = self.clone_remote(remote)
        self.assertEqual((other / "原稿.md").read_text(encoding="utf-8"),
                         "一度目は失敗しても失わない原稿")
        self.assertEqual((other / "資料.md").read_text(encoding="utf-8"), "人物ノート")
        self.assertEqual(self.other.read_text(encoding="utf-8"), "ステージしていない別ファイル")

    def test_preexisting_partial_staging_remains_unchanged(self):
        self.path.write_text("ステージ済みの原稿", encoding="utf-8")
        self.git("add", "--", "原稿.md")
        staged_blob = self.git("rev-parse", ":原稿.md").stdout.strip()
        self.path.write_text("後からディスクに入れた変更", encoding="utf-8")
        self.editor.load_file(str(self.path))
        self.editor.act_git_push()
        self.assertIn("失敗", self.messages[-1][0])
        self.assertEqual(self.git("rev-parse", ":原稿.md").stdout.strip(), staged_blob)
        self.assertTrue(any("ステージ内容" in x for x in self.messages[-1][1]))

    def test_bare_remote_push_then_pull_success(self):
        remote = self.attach_bare_remote()
        other = self.clone_remote(remote)
        self.editor.lines = ["ポメラから送った文章"]
        self.editor.dirty = True
        self.editor.act_git_push()
        self.assertIn("完了", self.messages[-1][0])
        subprocess.run(["git", "-C", str(other), "pull", "--ff-only"],
                       check=True, capture_output=True, text=True)
        self.assertEqual((other / "原稿.md").read_text(encoding="utf-8"),
                         "ポメラから送った文章")

        (other / "原稿.md").write_text("PCで編集した文章", encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "--", "原稿.md"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "edit on PC"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "push"],
                       check=True, capture_output=True)
        self.editor.cy = 0
        self.editor.cx = 2
        self.editor.act_git_pull()
        self.assertIn("完了", self.messages[-1][0])
        self.assertEqual(self.editor.lines, ["PCで編集した文章"])
        self.assertEqual(self.editor.cx, 2)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "PCで編集した文章")

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

    def test_long_japanese_filename_still_gets_emergency_rescue(self):
        long_path = self.root / ("あ" * 79 + ".txt")
        long_path.write_text("元の原稿", encoding="utf-8")
        editor = pome.Editor(str(long_path))
        editor.lines = ["退避したい内容"]
        editor.dirty = True
        rescue = editor.emergency_save()
        self.assertIsNotNone(rescue)
        self.assertLessEqual(len(Path(rescue).name.encode("utf-8")), 255)
        self.assertEqual(Path(rescue).read_text(encoding="utf-8"), "退避したい内容")
        self.assertEqual(long_path.read_text(encoding="utf-8"), "元の原稿")

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
