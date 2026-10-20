import io
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from nba_bot.collector import (CREDIT_RESERVE, acquire_lock, collection_status,
                               cycle, daily_backup, next_month, reserve, schedule)
from nba_bot.core import iso, now
from nba_bot.providers import OddsAPI, mock_candidates
from nba_bot.store import Store


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)/'paper.sqlite'
        self.store = Store(self.path)
        self.at = now().replace(day=15, hour=12)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def usage(self, attempt, remaining=496, recorded=None):
        with self.store.db:
            self.store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)',
                                  (attempt, iso(recorded or self.at), remaining, 4, 2))

    def test_restart_preserves_six_hour_schedule(self):
        attempt, _, _ = reserve(self.store, self.at, 'us')
        self.usage(attempt)
        self.store.close()
        self.store = Store(self.path)
        second, due, _ = reserve(self.store, self.at+timedelta(minutes=1), 'us')
        self.assertIsNone(second)
        self.assertEqual(due, self.at+timedelta(hours=6))
        third, _, _ = reserve(self.store, due, 'us')
        self.assertIsNotNone(third)

    def test_failures_retry_but_attempts_capped_across_restarts(self):
        first, _, _ = reserve(self.store, self.at, 'us')
        self.assertIsNotNone(first)
        retry, due, why = reserve(self.store, self.at+timedelta(minutes=1), 'us')
        self.assertIsNone(retry)
        self.assertEqual(why, 'Retry backoff')
        self.assertEqual(due, self.at+timedelta(minutes=30))
        for index in range(1, 6):
            self.assertIsNotNone(reserve(self.store, self.at+timedelta(minutes=30*index), 'us')[0])
        attempt, due, reason = reserve(self.store, self.at+timedelta(hours=4), 'us')
        self.assertIsNone(attempt)
        self.assertEqual(reason, 'Daily request-attempt limit')
        self.assertEqual(due, self.at+timedelta(days=1))

    def test_quota_reserve_missing_headers_and_month_reset(self):
        attempt, _, _ = reserve(self.store, self.at, 'us')
        self.usage(attempt, remaining=CREDIT_RESERVE+1)
        self.assertIsNone(reserve(self.store, self.at+timedelta(hours=6), 'us')[0])
        due, why = schedule(self.store, self.at+timedelta(hours=6))
        self.assertEqual(due, next_month(self.at))
        self.assertIn('reserve', why)
        self.assertIsNotNone(reserve(self.store, next_month(self.at), 'us')[0])

    def test_missing_quota_headers_pause_paid_requests(self):
        attempt, _, _ = reserve(self.store, self.at, 'us')
        self.usage(attempt, remaining=None)
        attempt, _, why = reserve(self.store, self.at+timedelta(hours=6), 'us')
        self.assertIsNone(attempt)
        self.assertIn('did not report', why)

    def test_cycle_only_calls_provider_once_and_persists_quota(self):
        with patch('nba_bot.collector.OddsAPI') as provider, patch('nba_bot.collector.now', return_value=self.at):
            provider.return_value.collect.return_value = []
            provider.return_value.usage = {'remaining': 496, 'used': 4, 'last_cost': 2}
            self.assertTrue(cycle(self.path, True))
            self.assertFalse(cycle(self.path, True))
            self.assertEqual(provider.return_value.collect.call_count, 1)
        self.assertEqual(self.store.db.execute('SELECT remaining FROM provider_usage').fetchone()[0], 496)
        self.assertEqual(self.store.report(False)['coverage']['quotes'], 0)

    def test_failed_request_is_recorded_without_mock_fallback(self):
        with patch('nba_bot.collector.OddsAPI') as provider, patch('nba_bot.collector.now', return_value=self.at):
            provider.return_value.collect.side_effect = RuntimeError('upstream failed')
            provider.return_value.usage = None
            with self.assertRaises(RuntimeError):
                cycle(self.path, True)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM collection_attempts').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT ok FROM operations').fetchone()[0], 0)

    def test_collector_lock_prevents_duplicate_worker(self):
        with acquire_lock(self.path):
            with self.assertRaises(RuntimeError):
                acquire_lock(self.path)
        with acquire_lock(self.path):
            pass

    def test_empty_snapshot_does_not_resurrect_old_prices(self):
        old = mock_candidates(now()-timedelta(hours=1))[0][0]
        self.store.collect([old])
        self.store.collect([], simulated=True)
        self.assertEqual(self.store.report(True)['quotes'], [])

    def test_daily_backup_retention_preserves_manual_files(self):
        directory = self.path.parent/'backups'
        directory.mkdir()
        for index in range(1, 10):
            (directory/f'auto-2020-01-{index:02}.sqlite').write_text('old test backup')
        manual = directory/'manual.sqlite'
        manual.write_text('preserve this')
        daily_backup(self.path)
        self.assertTrue(manual.exists())
        self.assertEqual(len(list(directory.glob('auto-*.sqlite'))), 7)
        today = directory/f'auto-{now().date()}.sqlite'
        with sqlite3.connect(today) as backup:
            self.assertEqual(backup.execute('pragma integrity_check').fetchone()[0], 'ok')
        count = len(list(directory.iterdir()))
        daily_backup(self.path)
        self.assertEqual(len(list(directory.iterdir())), count)

    def test_usage_headers_parsed_without_exposing_credentials(self):
        class Response(io.BytesIO):
            headers = {'x-requests-remaining': '496', 'x-requests-used': '4', 'x-requests-last': '2'}
        with patch.dict('os.environ', {'ODDS_API_KEY': 'fixture-key'}), patch('nba_bot.providers.urlopen', return_value=Response(b'[]')):
            api = OddsAPI()
            self.assertEqual(api.collect(self.at), [])
            self.assertEqual(api.usage, {'remaining': 496, 'used': 4, 'last_cost': 2})


if __name__ == '__main__':
    unittest.main()
