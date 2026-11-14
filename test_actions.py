"""Regression tests for short-lived Actions runners and access failures."""
import base64
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import state_sync
import watcher
from sources import AccessDenied, visit


class ActionsTests(unittest.TestCase):
    def test_state_round_trip_and_optimistic_write(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = watcher.database(root / 'original')
            db.execute("INSERT INTO health VALUES ('BookMyShow/ALUC', 2)")
            db.commit()
            db.close()
            remote = {'encoding': 'base64', 'content': base64.b64encode((root / 'original').read_bytes()).decode(), 'sha': 'previous-blob'}
            with patch.object(state_sync, 'api', return_value=remote):
                state_sync.restore(root / 'restored')
            with patch.object(state_sync, 'api', return_value={}) as api:
                state_sync.save(root / 'restored')
                payload = api.call_args.args[2]
                self.assertEqual(payload['sha'], 'previous-blob')
                self.assertEqual(base64.b64decode(payload['content']), (root / 'original').read_bytes())

    def test_failed_restore_does_not_initialize_history(self):
        with TemporaryDirectory() as tmp:
            with patch.object(state_sync, 'api', side_effect=RuntimeError('HTTP 500')):
                with self.assertRaises(RuntimeError):
                    state_sync.restore(Path(tmp))
            self.assertFalse((Path(tmp) / 'revision.json').exists())

    def test_corrupt_remote_state_is_rejected(self):
        with TemporaryDirectory() as tmp:
            with patch.object(state_sync, 'api', return_value={'encoding': 'base64', 'content': base64.b64encode(b'invalid').decode(), 'sha': 'x'}):
                with self.assertRaises(Exception):
                    state_sync.restore(Path(tmp))
            self.assertFalse((Path(tmp) / 'revision.json').exists())

    def test_denial_and_rate_limit_are_not_empty_inventories(self):
        for status in (401, 403, 429):
            page = MagicMock()
            page.goto.return_value.status = status
            with self.assertRaises(AccessDenied):
                visit(page, 'https://in.bookmyshow.com/')
            page.goto.assert_called_once()

    def test_once_exits_after_one_cycle_and_keeps_failure_status(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'123:test', 'TELEGRAM_CHAT_ID':'123', 'STATE_DIR':tmp}):
            with patch('sys.argv', ['watcher.py', 'once']), patch.object(watcher, 'sync_playwright'), patch.object(watcher, 'cycle', return_value=2) as cycle, patch.object(watcher.time, 'sleep') as sleep:
                self.assertEqual(watcher.main(), 1)
                cycle.assert_called_once()
                sleep.assert_not_called()
            state_sync.check(Path(tmp) / 'watch.sqlite3')

    def test_normal_browser_and_optional_proxy(self):
        with patch.dict(os.environ, {'BROWSER_HEADLESS':'false', 'HTTPS_PROXY':'http://localhost:8080'}, clear=True):
            options = watcher.browser_options()
            self.assertFalse(options['headless'])
            self.assertEqual(options['channel'], 'chromium')
            self.assertEqual(options['proxy']['server'], 'http://localhost:8080')

    def test_container_browser_path(self):
        with patch.dict(os.environ, {'CHROMIUM_PATH': '/usr/bin/chromium'}, clear=True):
            options = watcher.browser_options()
            self.assertEqual(options['executable_path'], '/usr/bin/chromium')
            self.assertNotIn('channel', options)

    def test_legacy_history_migrates_without_losing_events(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = watcher.database(root / 'showtime.db')
            old.execute("INSERT INTO health VALUES ('BookMyShow/ALUC', 2)")
            old.commit()
            old.close()
            target = watcher.state_path(root)
            self.assertEqual(target.name, state_sync.FILE)
            state_sync.check(target)
            with watcher.database(target) as db:
                self.assertEqual(db.execute('SELECT failures FROM health').fetchone(), (2,))
                db.execute('UPDATE health SET failures=3')
            watcher.state_path(root)
            with watcher.database(target) as db:
                self.assertEqual(db.execute('SELECT failures FROM health').fetchone(), (3,))


if __name__ == '__main__':
    unittest.main()

