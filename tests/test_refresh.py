import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from nba_bot.collector import reserve, schedule
from nba_bot.core import iso, now
from nba_bot.providers import mock_candidates
from nba_bot.refresh import policy, timestamp_ages
from nba_bot.store import Store


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.folder.name)/'fast.sqlite')
        self.at=now().replace(day=15,hour=12)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def quota(self, remaining=99900, used=100):
        with self.store.db:
            attempt=self.store.db.execute('INSERT INTO collection_attempts(started,region,estimated_cost) VALUES (?,?,?)',
                (iso(self.at-timedelta(minutes=10)),'us',2)).lastrowid
            self.store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)', (attempt,iso(self.at),remaining,used,2))

    def quotes(self, hours):
        quote=mock_candidates(self.at)[0][0]
        self.store.collect([replace(quote, simulated=False, collected=iso(self.at), quoted=iso(self.at), start=iso(self.at+timedelta(hours=hours)))])

    def test_free_quota_cannot_activate_fast_profile(self):
        self.quota(remaining=496,used=4)
        with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'fast'}):
            config=policy(self.store,self.at)
            self.assertEqual(config['effective'],'development')
            self.assertTrue(config['needs_quota_confirmation'])
            attempt,_,_=reserve(self.store,self.at,'us')
            self.assertIsNone(attempt)

    def test_paid_capacity_refreshes_upcoming_games_every_minute(self):
        # Separate databases preserve the same append-only rules as production.
        for offset,seconds in ((1,60),(3,60),(10,60)):
            store=Store(Path(self.folder.name)/f'phase-{offset}.sqlite')
            try:
                with store.db:
                    attempt=store.db.execute('INSERT INTO collection_attempts(started,region,estimated_cost) VALUES (?,?,?)', (iso(self.at-timedelta(minutes=10)),'us',2)).lastrowid
                    store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)', (attempt,iso(self.at),99900,100,2))
                q=mock_candidates(self.at)[0][0]
                store.collect([replace(q,simulated=False,start=iso(self.at+timedelta(hours=offset)))])
                with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'fast'}):
                    config=policy(store,self.at)
                self.assertEqual(config['effective'],'fast')
                self.assertEqual(config['interval_seconds'],seconds)
            finally:
                store.close()

    def test_fast_minute_reservations_persist(self):
        self.quota()
        self.quotes(1)
        with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'fast'}):
            attempt,_,_=reserve(self.store,self.at,'us')
            self.assertIsNotNone(attempt)
            with self.store.db:
                self.store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)', (attempt,iso(self.at),99898,102,2))
            self.store.close()
            self.store=Store(Path(self.folder.name)/'fast.sqlite')
            blocked,due,_=reserve(self.store,self.at+timedelta(seconds=30),'us')
            self.assertIsNone(blocked)
            self.assertEqual(due,self.at+timedelta(seconds=60))
            self.assertIsNotNone(reserve(self.store,self.at+timedelta(seconds=61),'us')[0])

    def test_change_notifications_are_small_and_mode_specific(self):
        before_live=self.store.change_version(False)
        before_mock=self.store.change_version(True)
        self.quotes(1)
        self.assertNotEqual(before_live,self.store.change_version(False))
        self.assertEqual(before_mock,self.store.change_version(True))
        stable=self.store.change_version(False)
        self.assertEqual(stable,self.store.change_version(False))
        self.store.collect([],simulated=False)
        self.assertNotEqual(stable,self.store.change_version(False))

    def test_no_upcoming_games_reduce_polling(self):
        self.quota()
        with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'fast'}):
            self.assertEqual(policy(self.store,self.at)['interval_seconds'],1800)

    def test_paid_profile_is_opt_in(self):
        self.quota()
        with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'development'}):
            self.assertEqual(policy(self.store,self.at)['interval_seconds'],21600)

    def test_fast_credit_reserve_halts_before_exhaustion(self):
        self.quota(remaining=1001,used=98999)
        with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'fast'}):
            due,reason=schedule(self.store,self.at)
            self.assertGreater(due,self.at)
            self.assertIn('1000-credit reserve',reason)

    def test_low_disk_space_pauses_collection(self):
        with patch('nba_bot.refresh.shutil.disk_usage') as usage:
            usage.return_value.free=1
            due,reason=schedule(self.store,self.at)
            self.assertGreater(due,self.at)
            self.assertIn('space',reason)

    def test_timestamp_age_metric_does_not_claim_delivery_latency(self):
        q=mock_candidates(self.at)[0][0]
        from dataclasses import asdict
        result=timestamp_ages([asdict(replace(q,quoted=iso(self.at-timedelta(seconds=30))))])
        self.assertEqual(result['p95_seconds'],30)
        self.assertIn('not a measurement',result['note'])

    def test_invalid_profile_fails_before_request_reservation(self):
        with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'typo'}):
            with self.assertRaises(ValueError):
                reserve(self.store,self.at,'us')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM collection_attempts').fetchone()[0],0)
