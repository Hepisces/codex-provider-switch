"""Fixture tests: never write real ~/.codex data."""
import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('switcher', Path(__file__).resolve().with_name('codex_provider_switch.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.cfg = self.home / 'config.toml'
        self.cfg.write_text('# keep comment\nmodel = "same-model"\n[model_providers.vapi]\nname = "VAPI"\n[features]\nother = true\n')
        self.session = self.home / 'session.jsonl'
        self.body = b'{"type":"response_item","payload":{"text":"vapi stays in body"}}\n'
        self.session.write_bytes(json.dumps({'type': 'session_meta', 'payload': {'id': 'abc', 'model_provider': 'vapi', 'other': 42}}).encode() + b'\n' + self.body)
        self.db = self.home / 'state_5.sqlite'
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, model_provider TEXT, rollout_path TEXT, title TEXT)')
            con.execute('INSERT INTO threads VALUES (?, ?, ?, ?)', ('abc', 'vapi', str(self.session), 'keep title'))
        self.original = (self.cfg.read_bytes(), self.session.read_bytes())

    def args(self, target: str = 'openai', apply: bool = True) -> argparse.Namespace:
        return argparse.Namespace(provider=target, all=True, thread=None, source=None, model=None, apply=apply, skip_missing=False)

    def provider(self) -> str:
        with sqlite3.connect(self.db) as con:
            return con.execute('SELECT model_provider FROM threads').fetchone()[0]

    def test_preview_no_changes(self) -> None:
        m.switch(self.args(apply=False), self.home)
        self.assertEqual(self.original, (self.cfg.read_bytes(), self.session.read_bytes()))
        self.assertEqual(self.provider(), 'vapi')
        self.assertFalse((self.home / 'provider-switch-backups').exists())

    @patch.object(m, 'require_offline')
    def test_switch_restore(self, offline) -> None:
        m.switch(self.args(), self.home)
        self.assertEqual(self.provider(), 'openai')
        first, body = self.session.read_bytes().split(b'\n', 1)
        self.assertEqual(body, self.body)
        self.assertEqual(json.loads(first)['payload']['other'], 42)
        self.assertEqual(m.tomllib.loads(self.cfg.read_text())['model'], 'same-model')
        backup = next((self.home / 'provider-switch-backups').iterdir())
        m.restore(backup, self.home)
        self.assertEqual(self.original, (self.cfg.read_bytes(), self.session.read_bytes()))
        self.assertEqual(self.provider(), 'vapi')
        self.assertTrue((backup / 'state.snapshot.sqlite').is_file())

    @patch.object(m, 'require_offline')
    def test_both_directions(self, offline) -> None:
        m.switch(self.args(), self.home)
        m.switch(self.args('vapi'), self.home)
        self.assertEqual(self.provider(), 'vapi')
        self.assertEqual(self.session.read_bytes().split(b'\n', 1)[1], self.body)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT id,title FROM threads').fetchall(), [('abc', 'keep title')])
        self.assertTrue(self.session.is_file())

    @patch.object(m, 'require_offline')
    def test_restore_rejects_later_edits(self, offline) -> None:
        m.switch(self.args(), self.home)
        backup = next((self.home / 'provider-switch-backups').iterdir())
        self.session.write_bytes(self.session.read_bytes() + b'{}\n')
        with self.assertRaises(RuntimeError):
            m.restore(backup, self.home)
        self.assertEqual(self.provider(), 'openai')

    def test_missing_strict_and_skip(self) -> None:
        self.session.unlink()
        with self.assertRaises(RuntimeError):
            m.switch(self.args(apply=False), self.home)
        args = self.args(apply=False)
        args.skip_missing = True
        m.switch(args, self.home)
        self.assertEqual(self.provider(), 'vapi')

    @patch.object(m, 'require_offline')
    def test_write_failure_rolls_back(self, offline) -> None:
        actual = m.atomic_write
        failed = False
        def fail_once(path: Path, data: bytes) -> None:
            nonlocal failed
            if path == self.session and not failed:
                failed = True
                raise OSError('injected failure')
            actual(path, data)
        with patch.object(m, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                m.switch(self.args(), self.home)
        self.assertEqual(self.original, (self.cfg.read_bytes(), self.session.read_bytes()))
        self.assertEqual(self.provider(), 'vapi')


    @patch.object(m, 'require_offline')
    def test_reverse_failure_preserves_every_conversation(self, offline) -> None:
        m.switch(self.args(), self.home)
        before = (self.cfg.read_bytes(), self.session.read_bytes())
        original_write = m.atomic_write
        failed = False
        def fail_once(path: Path, data: bytes) -> None:
            nonlocal failed
            if path == self.session and not failed:
                failed = True
                raise OSError('reverse direction failure')
            original_write(path, data)
        with patch.object(m, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                m.switch(self.args('vapi'), self.home)
        self.assertEqual((self.cfg.read_bytes(), self.session.read_bytes()), before)
        self.assertEqual(self.provider(), 'openai')
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT id,title FROM threads').fetchall(), [('abc', 'keep title')])
        self.assertTrue(self.session.is_file())

    def test_running_client_blocked(self) -> None:
        with patch.object(m.subprocess, 'run', return_value=argparse.Namespace(stdout='123 /Applications/Codex.app/Contents/MacOS/Codex\n')):
            with self.assertRaises(RuntimeError):
                m.switch(self.args(), self.home)
        self.assertEqual(self.original, (self.cfg.read_bytes(), self.session.read_bytes()))


if __name__ == '__main__':
    unittest.main()
