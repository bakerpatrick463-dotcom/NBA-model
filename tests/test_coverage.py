import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from nba_bot.collector import schedule
from nba_bot.core import iso, now
from nba_bot.coverage import check, reserve_check
from nba_bot.providers import mock_candidates
from nba_bot.refresh import policy
from nba_bot.store import Store


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'coverage.sqlite'
        self.store=Store(self.path)
        self.at=now()
        q=mock_candidates(self.at)[0][0]
        self.store.collect([replace(q,simulated=False,event='real-event-id',event_name='B at A',book='fanduel')])
        with self.store.db:
            attempt=self.store.db.execute('INSERT INTO collection_attempts(started,region,estimated_cost) VALUES (?,?,?)',
                (iso(self.at-timedelta(hours=7)),'us',2)).lastrowid
            self.store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)',(attempt,iso(self.at),496,4,2))

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def response(self, event='real-event-id',book='fanduel'):
        q=self.store.latest_quotes(False)[1][0]
        return {'id':event,'commence_time':q['start'],'home_team':'A','away_team':'B','bookmakers':[
            {'key':book,'title':'FanDuel','last_update':iso(self.at),'markets':[
                {'key':'player_points','outcomes':[{'name':'Over','description':'Test Player','point':20.5,'price':1.91}]}]}]}

    def fake_provider(self, mock, response):
        from nba_bot.providers import OddsAPI
        mock.return_value.request.return_value=response
        mock.return_value.normalize.side_effect=OddsAPI.normalize
        mock.return_value.usage={'remaining':492,'used':8,'last_cost':4}

    def test_one_request_confirms_prices_without_overwriting_bulk(self):
        before=self.store.latest_quotes(False)
        with patch('nba_bot.coverage.OddsAPI') as provider:
            self.fake_provider(provider,self.response())
            result=check(self.path)
            self.assertEqual(provider.return_value.request.call_count,1)
            self.assertIn('/events/real-event-id/odds',provider.return_value.request.call_args.args[0])
            self.assertEqual(provider.return_value.request.call_args.kwargs['bookmakers'],'fanduel')
        self.assertEqual(result['status'],'prices_returned')
        self.assertEqual(result['quote_count'],1)
        self.assertEqual(result['provider_usage']['last_cost'],4)
        self.assertEqual(before,self.store.latest_quotes(False))
        self.assertEqual(self.store.report(False)['summary']['selections'],0)
        self.assertIsNone(self.store.report(True)['latest_prop_coverage_check'])

    def test_wrong_event_is_an_error_and_other_book_is_not_substituted(self):
        with patch('nba_bot.coverage.OddsAPI') as provider:
            self.fake_provider(provider,self.response(event='wrong-event'))
            result=check(self.path)
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['error_type'],'ValueError')

    def test_reference_book_does_not_prove_fanduel_availability(self):
        with patch('nba_bot.coverage.OddsAPI') as provider:
            self.fake_provider(provider,self.response(book='reference'))
            result=check(self.path)
        self.assertEqual(result['status'],'no_prices_for_this_game')
        self.assertEqual(result['quote_count'],0)

    def test_repeats_are_deferred_before_network_call(self):
        with patch('nba_bot.coverage.OddsAPI') as provider:
            self.fake_provider(provider,self.response())
            check(self.path)
            second=check(self.path)
            self.assertEqual(provider.return_value.request.call_count,1)
        self.assertEqual(second['status'],'deferred')

    def test_probe_does_not_delay_bulk_or_raise_bulk_cost_estimate(self):
        with patch('nba_bot.coverage.OddsAPI') as provider:
            self.fake_provider(provider,self.response())
            check(self.path)
        due,_=schedule(self.store,now())
        self.assertLessEqual(due,now())
        with self.store.db:
            self.store.db.execute('INSERT INTO collection_attempts(started,region,estimated_cost) VALUES (?,?,?)',
                                  (iso(self.at),'us',2))
            attempt=self.store.db.execute('SELECT MAX(id) FROM collection_attempts').fetchone()[0]
            self.store.db.execute('INSERT INTO request_types VALUES (?,?)',(attempt,'coverage'))
            self.store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)',(attempt,iso(self.at),99900,100,4))
        with patch.dict('os.environ',{'ODDS_REFRESH_PROFILE':'fast'}):
            settings=policy(self.store,self.at)
            self.assertEqual(settings['effective'],'fast')
            self.assertEqual(settings['required_fast_capacity'],100000)

    def test_low_quota_blocks_probe(self):
        with self.store.db:
            attempt=self.store.db.execute('INSERT INTO collection_attempts(started,region,estimated_cost) VALUES (?,?,?)',
                (iso(self.at),'us',2)).lastrowid
            self.store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)',(attempt,iso(self.at),103,397,2))
        attempt,reason=reserve_check(self.store,self.at)
        self.assertIsNone(attempt)
        self.assertIn('reserve',reason)
