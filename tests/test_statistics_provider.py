from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from nba_bot.statistics_provider import (BASE, Client, NoRedirect, StatisticsError,
                                         check, game, minutes, normalize, statistic)
from nba_bot.store import Store

AT=datetime(2026,10,21,0,30,tzinfo=timezone.utc)
SECRET='synthetic-test-key-never-a-real-credential'


def team(number=1):
    return {'id':number,'full_name':{1:'Boston Celtics',2:'New York Knicks',3:'Dallas Mavericks'}[number],
            'abbreviation':{1:'BOS',2:'NYK',3:'DAL'}[number]}


def player():
    return {'id':99,'first_name':'Test','last_name':'Player','team':team(2)}


def event(historical=False):
    return {'id':123,'date':'2026-02-15' if historical else '2026-10-21',
            'season':2025 if historical else 2026,'status':'Final' if historical else 'Scheduled',
            'period':4 if historical else 0,'postseason':False,'home_team':team(1),'visitor_team':team(2),
            'home_team_score':100 if historical else None,'visitor_team_score':98 if historical else None}


def stats():
    return {'id':1001,'player':player(),'team':team(1),'game':event(True),
            'min':'25:01','pts':20,'reb':8,'ast':4,'fg3m':0}


def response_for(endpoint):
    rows={'teams':[team(1),team(2)],'players/active':[player()],'games':[event()],
          'stats':[stats()],'player_injuries':[{'player':player(),'status':'Questionable',
                                            'description':'Provider test label','return_date':None}]}
    return {'data':rows[endpoint],'meta':{'next_cursor':None,'per_page':100}}


class StatisticsParsingTests(unittest.TestCase):
    def test_missing_and_zero_minutes_and_counts_are_distinct(self):
        self.assertAlmostEqual(minutes('25:01'),25+1/60)
        self.assertEqual(minutes('0'),0)
        self.assertIsNone(minutes(None))
        self.assertIsNone(minutes('DNP'))
        for value in (True,'12:75','unknown',float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError): minutes(value)
        row=statistic(stats())
        self.assertEqual(row['threes'],0)
        self.assertIsNone(row['field_goal_attempts'])
        missing=stats(); missing['min']=None
        self.assertFalse(statistic(missing)['baseline_fields_complete'])

    def test_historical_team_is_checked_against_game_without_rewriting_current_team(self):
        row=statistic(stats())
        self.assertEqual(row['team']['id'],'1')
        self.assertEqual(row['player']['current_team_id'],'2')
        wrong=stats(); wrong['team']=team(3)
        with self.assertRaises(ValueError): statistic(wrong)

    def test_conflicting_game_ids_and_identical_opponents_are_rejected(self):
        for change in ({'home_team_id':3},{'visitor_team':team(1)}):
            row={**event(),**change}
            with self.subTest(change=change),self.assertRaises(ValueError): game(row)

    def test_pagination_is_explicit_and_never_a_claim_of_full_import(self):
        data=response_for('players/active'); data['meta']['next_cursor']=100
        result=normalize(data,'players/active',{})
        self.assertTrue(result['more_pages'])
        self.assertFalse(result['pagination_exhausted'])
        self.assertFalse(result['full_roster_or_history_imported'])
        self.assertEqual(result['provider_namespace'],'balldontlie')
        data.pop('meta')
        result=normalize(data,'players/active',{})
        self.assertIsNone(result['more_pages'])
        self.assertIsNone(result['pagination_exhausted'])

    def test_duplicates_wrong_dates_and_wrong_history_window_are_rejected(self):
        duplicate=response_for('players/active'); duplicate['data']*=2
        with self.assertRaises(ValueError): normalize(duplicate,'players/active',{})
        with self.assertRaises(ValueError): normalize(response_for('games'),'games',{'dates[]':['2026-10-22']})
        params={'seasons[]':[2024],'start_date':'2025-02-01','end_date':'2025-06-30'}
        with self.assertRaises(ValueError): normalize(response_for('stats'),'stats',params)


class StatisticsAccessTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.path=Path(folder.name)/'paper.sqlite'

    def test_missing_key_makes_no_request_or_database(self):
        with patch.dict(os.environ,{'BALLDONTLIE_API_KEY':''}),patch('nba_bot.statistics_provider.build_opener') as opener:
            result=check(self.path)
            self.assertEqual(result['status'],'configuration_required')
            self.assertFalse(self.path.exists())
            opener.assert_not_called()

    def test_five_bounded_requests_are_persisted_and_repeats_are_deferred(self):
        client=Mock()
        client.request.side_effect=lambda endpoint,*args: response_for(endpoint)
        store=Store(self.path)
        before_live=store.change_version(False); before_mock=store.change_version(True)
        store.close()
        with patch('nba_bot.statistics_provider.Client',return_value=client),patch('nba_bot.statistics_provider.now',return_value=AT),patch('nba_bot.providers.OddsAPI') as odds:
            result=check(self.path)
            self.assertEqual(result['statistics_api_requests'],5)
            self.assertEqual(result['odds_api_requests'],0)
            self.assertEqual(result['requested_dates_eastern'],['2026-10-20','2026-10-21'])
            self.assertTrue(all(source['status']=='ok' for source in result['sources'].values()))
            self.assertEqual(result['sources']['historical_player_stats']['final_stats_with_required_fields'],1)
            self.assertEqual(check(self.path)['status'],'deferred')
            self.assertEqual(client.request.call_count,5)
            odds.assert_not_called()
        store=Store(self.path)
        try:
            self.assertNotEqual(store.change_version(False),before_live)
            self.assertEqual(store.change_version(True),before_mock)
            self.assertIsNotNone(store.report(False)['latest_stats_check'])
            self.assertIsNone(store.report(True)['latest_stats_check'])
            self.assertIsNone(store.report(False)['trial_started'])
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM collection_attempts').fetchone()[0],0)
            with self.assertRaises(sqlite3.DatabaseError): store.db.execute('DELETE FROM stats_checks')
        finally: store.close()

    def test_authentication_or_rate_limit_stops_without_retry(self):
        for status in (401,429):
            with tempfile.TemporaryDirectory() as folder:
                client=Mock(); client.request.side_effect=StatisticsError('HTTP error',status)
                with patch('nba_bot.statistics_provider.Client',return_value=client): result=check(Path(folder)/'db.sqlite')
                self.assertEqual(result['statistics_api_requests'],1)
                self.assertEqual(result['sources']['teams']['http_status'],status)
                self.assertTrue(all(r['status']=='not_checked' for name,r in result['sources'].items() if name!='teams'))
                client.request.assert_called_once()

    def test_each_response_is_archived_with_source_identity_without_credentials(self):
        class Response(io.BytesIO):
            headers={'Date':'Source HTTP date'}
            def __init__(self,url,data):
                super().__init__(json.dumps(data).encode()); self.url=url
            def geturl(self): return self.url
        def respond(request,**kwargs):
            endpoint=request.full_url.split('/nba/v1/',1)[1].split('?',1)[0]
            return Response(request.full_url,response_for(endpoint))
        opener=Mock(); opener.open.side_effect=respond
        with patch.dict(os.environ,{'BALLDONTLIE_API_KEY':SECRET}),patch('nba_bot.statistics_provider.build_opener',return_value=opener),patch('nba_bot.statistics_provider.now',return_value=AT):
            result=check(self.path)
        self.assertNotIn(SECRET,json.dumps(result))
        self.assertEqual(opener.open.call_count,5)
        for source in result['sources'].values():
            self.assertEqual(source['status'],'ok')
            artifact=source['artifacts'][0]
            self.assertTrue(Path(artifact['artifact_path']).is_file())
            self.assertTrue(artifact['first_observed_at'])
        store=Store(self.path)
        try: self.assertEqual(store.db.execute('SELECT COUNT(*) FROM source_artifacts').fetchone()[0],5)
        finally: store.close()

    def test_forbidden_paid_endpoint_is_visible_without_hiding_other_results(self):
        def request(endpoint,*args):
            if endpoint=='players/active': raise StatisticsError('HTTP error',403)
            return response_for(endpoint)
        client=Mock(); client.request.side_effect=request
        with patch('nba_bot.statistics_provider.Client',return_value=client): result=check(self.path)
        self.assertEqual(result['sources']['active_players']['http_status'],403)
        self.assertEqual(result['sources']['historical_player_stats']['status'],'ok')

    def test_authorization_is_header_only_and_original_bytes_are_archived(self):
        raw=json.dumps(response_for('teams')).encode()
        class Response(io.BytesIO):
            headers={'Date':'HTTP server date'}
            def geturl(self): return BASE+'teams'
        opener=Mock(); opener.open.return_value=Response(raw)
        with patch.dict(os.environ,{'BALLDONTLIE_API_KEY':SECRET}),patch('nba_bot.statistics_provider.build_opener',return_value=opener):
            artifacts=[]; result=Client().request('teams',{},self.path.parent/'artifacts',artifacts)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),SECRET)
        self.assertNotIn(SECRET,request.full_url)
        self.assertNotIn(SECRET,json.dumps(artifacts))
        self.assertEqual(Path(artifacts[0]['artifact_path']).read_bytes(),raw)
        self.assertEqual(result['data'][0]['full_name'],'Boston Celtics')

    def test_credential_echo_is_not_archived_and_redirects_are_not_followed(self):
        class Response(io.BytesIO):
            headers={}
            def geturl(self): return BASE+'teams'
        opener=Mock(); opener.open.return_value=Response(json.dumps({'key':SECRET}).encode())
        directory=self.path.parent/'artifacts'
        with patch.dict(os.environ,{'BALLDONTLIE_API_KEY':SECRET}),patch('nba_bot.statistics_provider.build_opener',return_value=opener):
            with self.assertRaises(StatisticsError) as raised: Client().request('teams',{},directory,[])
        self.assertNotIn(SECRET,str(raised.exception))
        self.assertFalse(directory.exists())
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'redirect',{},'https://example.test/'))

    def test_transport_error_does_not_print_provider_body_or_credentials(self):
        opener=Mock(); opener.open.side_effect=HTTPError(BASE+'teams',401,SECRET,{},None)
        with patch.dict(os.environ,{'BALLDONTLIE_API_KEY':SECRET}),patch('nba_bot.statistics_provider.build_opener',return_value=opener):
            with self.assertRaises(StatisticsError) as raised: Client().request('teams',{},self.path.parent,[])
        self.assertEqual(raised.exception.http_status,401)
        self.assertNotIn(SECRET,str(raised.exception))
