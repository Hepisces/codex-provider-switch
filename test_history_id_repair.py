"""Regression tests for lossless transport ID repair using temporary rollouts."""
import argparse
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import history_id_repair as repair
import test_switcher


class HistoryIdTests(unittest.TestCase):
    def setUp(self) -> None:
        """Reuse the isolated fixture without touching real Codex files."""
        self.fixture = test_switcher.SwitchTests('test_preview_no_changes')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.home
        folder = self.home / 'sessions'
        folder.mkdir()
        self.source = folder / 'base.jsonl'
        meta = self.fixture.session.read_bytes().split(b'\n', 1)[0] + b'\n'
        self.line = (json.dumps({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'id': 'ctc_foreign_123', 'call_id': 'call_original',
            'input': 'literal ctc_foreign_123 stays',
            'nested': {'id': 'ctc_foreign_123'}, 'encrypted_content': 'opaque_untouched'
        }}, ensure_ascii=False).encode() + b'\n')
        self.source.write_bytes(meta + self.line)
        self.args = argparse.Namespace(all=False, thread=['abc'], apply=True)

    def test_only_transport_id_changes_with_same_byte_length(self) -> None:
        """Messages, nested IDs and tool pairing must remain exact."""
        result, count = repair.repair_line(self.line)
        self.assertEqual(count, 1)
        self.assertEqual(len(result), len(self.line))
        before, after = json.loads(self.line), json.loads(result)
        before['payload']['id'] = 'ctc-foreign-123'
        self.assertEqual(before, after)
        self.assertEqual(repair.repair_line(result), (result, 0))

    def test_compaction_and_retained_links_preserved(self) -> None:
        """Change raw response IDs and their retained references consistently."""
        d = {'type': 'compacted', 'payload': {
            'replacement_history': [{'type': 'message', 'id': 'msg_foreign', 'content': '中文'}],
            'guardian_history': [{'type': 'reasoning', 'id': 'rs_old', 'encrypted_content': 'opaque'}],
            'retained_context': {'user_messages': [{'message_id': 'msg_foreign', 'text': 'unchanged'}]}}}
        raw = json.dumps(d, ensure_ascii=False, indent=1).encode()
        changed, count = repair.repair_line(raw)
        self.assertEqual(count, 3)
        self.assertEqual(len(raw), len(changed))
        result = json.loads(changed)['payload']
        self.assertEqual(result['retained_context']['user_messages'][0]['message_id'], 'msg-foreign')
        self.assertEqual(result['replacement_history'][0]['content'], '中文')

    def test_preview_is_read_only(self) -> None:
        """Preview leaves the original rollout unchanged and creates no backups."""
        original = self.source.read_bytes()
        self.args.apply = False
        repair.perform(self.args, self.home)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse((self.home / 'provider-switch-backups').exists())

    @patch.object(repair.core, 'require_offline')
    def test_lineage_apply_and_restore(self, offline) -> None:
        """Repair all base segments, retain databases, and undo byte-for-byte."""
        second = self.source.with_name('continuation.jsonl')
        second.write_bytes(json.dumps({'type': 'session_meta', 'payload': {
            'id': 'abc', 'history_base': {'thread_id': 'abc', 'end_byte_offset': len(self.source.read_bytes())}
        }}).encode() + b'\n' + self.line)
        original = {p: p.read_bytes() for p in (self.source, second)}
        db = self.fixture.db.read_bytes()
        repair.perform(self.args, self.home)
        for path, data in original.items():
            self.assertTrue(path.is_file())
            self.assertEqual(len(path.read_bytes()), len(data))
            self.assertNotEqual(path.read_bytes(), data)
        self.assertEqual(db, self.fixture.db.read_bytes())
        backup = next((self.home / 'provider-switch-backups').iterdir())
        repair.core.restore(backup, self.home)
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    @patch.object(repair.core, 'require_offline')
    def test_failure_restores_all_files(self, offline) -> None:
        """A second-file write failure restores the first without deleting either."""
        second = self.source.with_name('second.jsonl')
        shutil.copy2(self.source, second)
        original = {p: p.read_bytes() for p in (self.source, second)}
        write = repair.core.atomic_write
        failed = False
        def fail_once(path: Path, data: bytes) -> None:
            """Inject an error only for one original fixture file."""
            nonlocal failed
            if path == second and not failed:
                failed = True
                raise OSError('injected repair failure')
            write(path, data)
        with patch.object(repair.core, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                repair.perform(self.args, self.home)
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertTrue(list((self.home / 'provider-switch-backups').glob('*/manifest.json')))

    def test_other_records_keep_all_bytes(self) -> None:
        """User events and session/thread IDs are outside the repair scope."""
        line = b'{"type":"session_meta","payload":{"id":"msg_thread_id"}}\n'
        self.assertEqual(repair.repair_line(line), (line, 0))


if __name__ == '__main__':
    unittest.main()
