"""Manuscript recovery regressions; no terminal or third-party packages needed."""
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

import pome


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patchers = [
            patch.object(pome, 'CONFIG_DIR', str(self.root / 'config')),
            patch.object(pome, 'CONFIG_PATH', str(self.root / 'config/config.json')),
            patch.object(pome, 'HISTORY_DIR', str(self.root / 'history')),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = self.root / '原稿.txt'
        self.original = '失いたくない原稿\n第二段落'
        self.path.write_text(self.original, encoding='utf-8')
        self.ed = self.editor()

    def editor(self):
        editor = pome.Editor(str(self.path))
        editor.msgbox = lambda *args: None
        return editor

    def replace(self, text):
        self.ed.act_select_all()
        self.ed.edit_insert(text)

    def autosave(self):
        self.ed.last_change_time = time.time() - 10
        self.ed.tick()

    def test_undo_after_autosave_is_unsaved_and_quit_can_be_cancelled(self):
        self.replace('誤操作')
        self.autosave()
        self.assertFalse(self.ed.dirty)
        self.ed.act_undo()
        self.assertEqual('\n'.join(self.ed.lines), self.original)
        self.assertTrue(self.ed.dirty)
        self.ed.choice = lambda *args: 'esc'
        self.ed.act_quit()
        self.assertFalse(self.ed.quitting)
        self.autosave()
        self.assertEqual(self.path.read_text(), self.original)

    def test_redo_tracks_current_saved_content(self):
        self.replace('誤操作')
        self.assertTrue(self.ed.save_file())
        self.ed.act_undo()
        self.assertTrue(self.ed.dirty)
        self.ed.act_redo()
        self.assertFalse(self.ed.dirty)
        self.ed.act_undo()
        self.assertTrue(self.ed.save_file())
        self.ed.act_redo()
        self.assertTrue(self.ed.dirty)

    def test_multiple_autosaves_keep_original_after_restart(self):
        self.replace('削除後')
        self.autosave()
        self.replace('さらに編集')
        self.autosave()
        self.ed = self.editor()
        history = self.ed.history_files()
        self.assertEqual(len(history), 2)
        self.assertEqual(Path(history[-1]).read_text(), self.original)
        self.ed.prompt = lambda *args: '2'
        self.ed.choice = lambda *args: 'y'
        self.ed.act_file_history()
        self.assertEqual('\n'.join(self.ed.lines), self.original)
        self.assertTrue(self.ed.dirty)
        self.assertEqual(self.path.read_text(), 'さらに編集')
        self.ed.act_undo()
        self.assertEqual(self.ed.lines, ['さらに編集'])
        self.ed.act_redo()
        self.assertTrue(self.ed.save_file())
        self.assertEqual(self.path.read_text(), self.original)

    def test_cancel_recovery_leaves_draft_and_history_intact(self):
        self.replace('編集後')
        self.ed.save_file()
        self.ed.prompt = lambda *args: '1'
        self.ed.choice = lambda *args: 'esc'
        self.ed.act_file_history()
        self.assertEqual(self.ed.lines, ['編集後'])
        self.assertFalse(self.ed.dirty)
        self.assertEqual(len(self.ed.history_files()), 1)

    def test_invalid_history_number_leaves_manuscript_unchanged(self):
        self.replace('編集後')
        self.ed.save_file()
        for value in ['0', '-1', '²', '999', 'abc']:
            self.ed.prompt = lambda *args: value
            self.ed.act_file_history()
            self.assertEqual(self.ed.lines, ['編集後'])
            self.assertFalse(self.ed.dirty)

    def test_history_write_failure_prevents_manuscript_replacement(self):
        self.replace('編集後')
        with patch.object(pome.Editor, 'keep_history', side_effect=OSError('disk full')):
            self.assertFalse(self.ed.save_file())
        self.assertEqual(self.path.read_text(), self.original)
        self.assertTrue(self.ed.dirty)

    def test_replace_failure_keeps_original_and_cleans_temp(self):
        self.replace('編集後')
        with patch.object(pome.os, 'replace', side_effect=OSError('replace failed')):
            self.assertFalse(self.ed.save_file())
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(list(self.root.glob('.pome-*')), [])
        self.assertTrue(self.ed.dirty)

    def test_external_change_or_deletion_is_not_overwritten(self):
        self.replace('このエディタの変更')
        self.path.write_text('他のエディタの変更')
        self.assertFalse(self.ed.save_file())
        self.assertEqual(self.path.read_text(), '他のエディタの変更')
        self.path.unlink()
        self.assertFalse(self.ed.save_file())
        self.assertFalse(self.path.exists())
        alternate = self.root / '別名.txt'
        self.assertTrue(self.ed.save_file(str(alternate)))
        self.assertEqual(alternate.read_text(), 'このエディタの変更')

    def test_failed_autosave_pauses_until_successful_manual_save(self):
        self.replace('編集中')
        self.path.write_text('外部の編集')
        with patch.object(self.ed, 'msgbox') as message:
            self.autosave()
            self.ed.tick()
            self.ed.tick()
            self.assertEqual(message.call_count, 1)
        self.assertTrue(self.ed._autosave_failed)
        self.assertTrue(self.ed.save_file(str(self.root / '退避.txt')))
        self.assertFalse(self.ed._autosave_failed)
        self.replace('続き')
        self.autosave()
        self.assertEqual((self.root / '退避.txt').read_text(), '続き')

    def test_symlink_target_and_permissions_are_preserved(self):
        self.path.chmod(0o640)
        link = self.root / 'link.txt'
        link.symlink_to(self.path)
        self.ed = pome.Editor(str(link))
        self.replace('編集後')
        self.assertTrue(self.ed.save_file())
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.path.read_text(), '編集後')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(Path(self.ed.history_files()[0]).stat().st_mode), 0o600)

    def test_cp932_crlf_preserved_in_file_and_history(self):
        old = '日本語\r\n原稿'.encode('cp932')
        self.path.write_bytes(old)
        self.ed = self.editor()
        self.replace('日本語\n編集後')
        self.assertTrue(self.ed.save_file())
        self.assertEqual(self.path.read_bytes(), '日本語\r\n編集後'.encode('cp932'))
        self.assertEqual(Path(self.ed.history_files()[0]).read_bytes(), old)

    def test_save_as_existing_file_preserves_destination_history(self):
        other = self.root / 'other.txt'
        other.write_text('既存の別原稿')
        self.assertTrue(self.ed.save_file(str(other)))
        self.assertEqual(Path(self.ed.history_files()[0]).read_text(), '既存の別原稿')
        self.assertEqual(self.path.read_text(), self.original)

    def test_unchanged_save_does_not_duplicate_history(self):
        self.assertTrue(self.ed.save_file())
        self.assertEqual(self.ed.history_files(), [])
        self.replace('編集後')
        self.ed.save_file()
        self.ed.save_file()
        self.assertEqual(len(self.ed.history_files()), 1)

    def test_new_document_resets_saved_baseline(self):
        self.ed.act_file_new()
        self.ed.edit_insert('新しい原稿')
        self.ed.act_undo()
        self.assertFalse(self.ed.dirty)
        self.ed.act_redo()
        self.assertTrue(self.ed.dirty)


if __name__ == '__main__':
    unittest.main()
