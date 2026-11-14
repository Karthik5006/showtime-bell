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
        with patch.dict(os.environ, {'BROWSER_HEADLESS':'false', 'BROWSER_PROXY_SERVER':'http://localhost:8080', 'BROWSER_PROXY_USERNAME':'user', 'BROWSER_PROXY_PASSWORD':'password'}, clear=True):
            options = watcher.browser_options()
            self.assertFalse(options['headless'])
            self.assertEqual(options['channel'], 'chromium')
            self.assertEqual(options['proxy']['username'], 'user')

class CompatibilityTests(unittest.TestCase):
    def test_calendar_accepts_spaced_and_compact_labels(self):
        from datetime import date
        from sources import bms_calendar
        for label in ("Sat 14 Nov", "Sat14Nov", "Sat  14  Nov"):
            page = MagicMock()
            page.url = 'https://in.bookmyshow.com/cinemas/hyderabad/allu-cinemas-kokapet/buytickets/ALUC/20261114'
            page.goto.return_value.status = 200
            page.get_by_role.return_value.aria_snapshot.return_value = (
                'ALLU Cinemas Kokapet beside Allu Studios\n'
                f'- button "{label}" [pressed]:\n'
                '- button "Sun15Nov" [disabled]:'
            )
            days = bms_calendar(page, "ALUC", date(2026, 11, 14))
            self.assertTrue(days[date(2026, 11, 14)].endswith('/20261114'))
            self.assertIsNone(days[date(2026, 11, 15)])
            date_pattern = page.get_by_role.call_args_list[0].kwargs["name"]
            self.assertIsNotNone(date_pattern.match(label))

    def test_selected_date_accepts_both_formats(self):
        from datetime import date
        from sources import bms_rows
        import test_watch
        for number in (4,14):
            page = MagicMock()
            day = date(2026,11,number)
            page.url = "https://in.bookmyshow.com/test"
            page.get_by_role.return_value.first.aria_snapshot.return_value = test_watch.BMS
            page.get_by_role.return_value.first.evaluate.return_value = False
            bms_rows(page,"ALUC",day,page.url)
            kwargs = page.get_by_role.call_args_list[0].kwargs
            self.assertTrue(kwargs["pressed"])
            pattern = kwargs["name"]
            for label in (day.strftime("%a %d %b"), day.strftime("%a%d%b"), f'{day:%a}{number}{day:%b}'):
                self.assertIsNotNone(pattern.fullmatch(label))
            self.assertIsNone(pattern.fullmatch("Sun15Nov"))

    def test_once_reopens_state_dir_and_inspection_keeps_it_untouched(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"STATE_DIR":tmp,"TELEGRAM_BOT_TOKEN":"123:test","TELEGRAM_CHAT_ID":"123"}):
                def write_state(browser,db,inspect):
                    db.execute("INSERT OR REPLACE INTO health VALUES('test',7)")
                    db.commit()
                    return 0
                with patch('sys.argv',['watcher.py','once']), patch.object(watcher,'sync_playwright'), patch.object(watcher,'cycle',side_effect=write_state):
                    self.assertEqual(watcher.main(),0)
                path = Path(tmp)/"watch.sqlite3"
                before = path.read_bytes()
                with patch('sys.argv',['watcher.py','inspect']), patch.object(watcher,'sync_playwright'), patch.object(watcher,'cycle',return_value=0):
                    self.assertEqual(watcher.main(),0)
                self.assertEqual(path.read_bytes(),before)
                with watcher.database(path) as db:
                    self.assertEqual(db.execute("SELECT failures FROM health WHERE scope='test'").fetchone()[0],7)


if __name__ == '__main__':
    unittest.main()
