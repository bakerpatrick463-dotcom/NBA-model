import copy
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch

from nba_bot.core import iso
from nba_bot.statistics_import import import_data,import_lock
from nba_bot.statistics_identity import VERSION,identify,match_rows,visible_check
from nba_bot.statistics_provider import StatisticsError,reserve_request
from nba_bot.store import Store
from test_statistics_provider import AT,event,player,response_for,stats,team


def source_row(**changes):
    return {'source_row':1,'source_page':1,'game_date':'2026-10-21','game_time_label':'07:00 (ET)',
            'matchup':'NYK@BOS','team':'New York Knicks','player_name':'Player, Test',
            'status':'Out','reason':'Test reason','record_type':'player_status',**changes}


class StatisticsImportTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'paper.sqlite'
        self.client=Mock()
        self.client.request.side_effect=lambda endpoint,*args: ({'data':[event(True)],'meta':{'next_cursor':None}}
                                                              if endpoint=='games' and 'start_date' in args[0] else response_for(endpoint))
        for target,value in [('nba_bot.statistics_import.Client',self.client),
                             ('nba_bot.statistics_import.now',AT),('nba_bot.statistics_identity.now',AT)]:
            patcher=patch(target,return_value=value); patcher.start(); self.addCleanup(patcher.stop)
        sleeper=patch('nba_bot.statistics_import.time.sleep'); sleeper.start(); self.addCleanup(sleeper.stop)

    def two_pages(self,endpoint,params,*args):
        self.assertEqual(endpoint,'stats')
        row=stats()
        if params.get('cursor') is None:
            cursor=1001
        else:
            self.assertEqual(params['cursor'],'1001')
            row['id']=1002; row['player']['id']=100
            cursor=None
        return {'data':[row],'meta':{'next_cursor':cursor}}

    def test_page_limit_resume_and_finished_snapshot_reuse(self):
        self.client.request.side_effect=self.two_pages
        first=import_data(self.path,scope='history',max_pages=1)
        self.assertEqual(first['status'],'paused_page_limit')
        self.assertEqual(first['imports'][0]['rows'],1)
        run=first['imports'][0]['run_id']
        second=import_data(self.path,scope='history',max_pages=2)
        self.assertEqual(second['status'],'pagination_finished')
        self.assertEqual(second['imports'][0]['run_id'],run)
        self.assertEqual(second['imports'][0]['rows'],2)
        self.assertEqual(import_data(self.path,scope='history')['statistics_api_requests'],0)
        self.assertEqual(self.client.request.call_count,2)

    def test_failed_page_preserves_prior_rows_and_cursor_for_resume(self):
        count=0
        def fail_second(endpoint,params,*args):
            nonlocal count
            count+=1
            if count==2: raise StatisticsError('Timeout')
            return self.two_pages(endpoint,params)
        self.client.request.side_effect=fail_second
        result=import_data(self.path,scope='history')
        self.assertEqual(result['status'],'paused_error')
        self.assertEqual(result['imports'][0]['rows'],1)
        self.assertEqual(result['imports'][0]['next_cursor'],'1001')
        self.assertIsNotNone(result['imports'][0]['latest_event'])
        result=import_data(self.path,scope='history')
        self.assertEqual(result['imports'][0]['rows'],2)
        self.assertIsNone(result['imports'][0]['latest_event'])

    def test_semantic_duplicate_rolls_back_the_entire_page(self):
        def duplicates(endpoint,params,*args):
            row=stats()
            if params.get('cursor') is not None: row['id']=1002
            return {'data':[row],'meta':{'next_cursor':1001 if params.get('cursor') is None else None}}
        self.client.request.side_effect=duplicates
        result=import_data(self.path,scope='history')
        self.assertEqual(result['status'],'paused_error')
        self.assertEqual(result['imports'][0]['rows'],1)
        self.assertFalse(result['imports'][0]['pagination_finished'])
        store=Store(self.path)
        try:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM stats_import_objects').fetchone()[0],1)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM stats_import_pages').fetchone()[0],1)
        finally: store.close()

    def test_nonadvancing_cursor_and_full_page_without_cursor_are_withheld(self):
        self.client.request.side_effect=lambda *args: {'data':[stats()],'meta':{'next_cursor':1001}}
        result=import_data(self.path,scope='history')
        self.assertEqual(result['status'],'paused_error')
        self.assertEqual(result['imports'][0]['pages'],1)
        with tempfile.TemporaryDirectory() as folder:
            rows=[]
            for i in range(100):
                row=player(); row['id']=i+1; rows.append(row)
            self.client.request.side_effect=lambda *args: {'data':rows,'meta':{'per_page':100}}
            result=import_data(Path(folder)/'paper.sqlite',scope='roster')
            self.assertEqual(result['status'],'paused_error')
            self.assertEqual(result['imports'][0]['rows'],0)

    def test_short_terminal_page_is_labeled_without_asserting_reconciled_coverage(self):
        data=response_for('players/active'); data.pop('meta')
        self.client.request.side_effect=lambda *args: data
        result=import_data(self.path,scope='roster')
        self.assertEqual(result['imports'][0]['terminal_basis'],'short_page_without_next_cursor')
        self.assertFalse(result['imports'][0]['coverage_reconciled'])

    def test_refresh_creates_new_snapshot_without_changing_original_objects(self):
        first=import_data(self.path,scope='roster')['imports'][0]['run_id']
        second=import_data(self.path,scope='roster',refresh=True)['imports'][0]['run_id']
        self.assertNotEqual(first,second)
        store=Store(self.path)
        try:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM stats_import_objects').fetchone()[0],2)
            with self.assertRaises(sqlite3.DatabaseError): store.db.execute('DELETE FROM stats_import_objects')
        finally: store.close()

    def test_import_lock_and_shared_rate_budget_prevent_extra_requests(self):
        with import_lock(self.path):
            self.assertEqual(import_data(self.path)['status'],'deferred')
        self.client.request.assert_not_called()
        store=Store(self.path)
        try:
            for _ in range(30): self.assertIsNone(reserve_request(store,'stats',AT))
            self.assertIsNotNone(reserve_request(store,'stats',AT))
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM statistics_requests').fetchone()[0],30)
        finally: store.close()

    def test_low_disk_pauses_without_network_request(self):
        space={'collection_allowed':False,'free_bytes':0,'required_free_bytes':1}
        with patch('nba_bot.statistics_import.storage_status',return_value=space):
            self.assertEqual(import_data(self.path)['status'],'paused_storage')
        self.client.request.assert_not_called()

    def test_all_snapshots_enable_versioned_identity_check_without_live_forecasts(self):
        store=Store(self.path)
        evidence={'source_sha256':'a'*64,'parser_version':'test-source','source_first_observed_at':iso(AT),
                  'structured_report':{'status':'parsed_source_labels','rows':[source_row()]}}
        with store.db: store.db.execute('INSERT INTO injury_texts VALUES (?,?)',('original',json.dumps(evidence)))
        before_mock=store.change_version(True); before_live=store.change_version(False)
        store.close()
        result=import_data(self.path)
        self.assertEqual(result['status'],'pagination_finished')
        self.assertEqual(result['statistics_api_requests'],5)
        self.assertEqual(result['odds_api_requests'],0)
        matches=result['identity_check']
        self.assertEqual(matches['counts'],{'matched_provider_identity':1})
        self.assertEqual(matches['rows'][0]['provider_player_id'],'99')
        self.assertEqual(matches['rows'][0]['provider_game_id'],'123')
        store=Store(self.path)
        try:
            self.assertTrue(identify(store)['cached'])
            self.assertEqual(store.db.execute('SELECT payload FROM injury_texts').fetchone()[0],json.dumps(evidence))
            report=store.report(False)
            self.assertIsNone(report['trial_started'])
            self.assertEqual(report['bets'],[])
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM collection_attempts').fetchone()[0],0)
            self.assertEqual(store.change_version(True),before_mock)
            self.assertNotEqual(store.change_version(False),before_live)
            self.assertEqual(store.report(True)['statistics_imports'],[])
            self.assertIsNone(store.report(True)['latest_statistics_identity_check'])
            with patch('nba_bot.statistics_identity.now',return_value=AT+timedelta(days=2)):
                self.assertEqual(identify(store)['status'],'not_ready')
        finally: store.close()


class StatisticsIdentityTests(unittest.TestCase):
    def match(self,rows=None,players=None,games=None):
        teams=[{'id':str(i),'name':team(i)['full_name'],'abbreviation':team(i)['abbreviation']} for i in (1,2,3)]
        people=players if players is not None else [{'id':'99','first_name':'Test','last_name':'Player','current_team_id':'2'}]
        fixtures=games if games is not None else [{'id':'123','source_calendar_date':'2026-10-21','home_team_id':'1','visitor_team_id':'2'}]
        return match_rows(rows or [source_row()],teams,people,fixtures)

    def test_name_alone_wrong_team_and_missing_suffix_do_not_match(self):
        players=[{'id':'99','first_name':'Test','last_name':'Player','current_team_id':'1'}]
        self.assertEqual(self.match(players=players)[0]['match_status'],'unmatched_player')
        self.assertEqual(self.match(rows=[source_row(player_name='Player Jr., Test')])[0]['match_status'],'unmatched_player')

    def test_ambiguous_names_or_opponents_are_not_guessed(self):
        players=[{'id':str(i),'first_name':'Test','last_name':'Player','current_team_id':'2'} for i in (99,100)]
        self.assertEqual(self.match(players=players)[0]['match_status'],'ambiguous_player')
        games=[{'id':str(i),'source_calendar_date':'2026-10-21','home_team_id':'1','visitor_team_id':'2'} for i in (123,124)]
        self.assertEqual(self.match(games=games)[0]['match_status'],'ambiguous_game')
        reversed_game=[{'id':'123','source_calendar_date':'2026-10-21','home_team_id':'2','visitor_team_id':'1'}]
        self.assertEqual(self.match(games=reversed_game)[0]['match_status'],'unmatched_game')

    def test_unsubmitted_team_report_never_becomes_a_player_claim(self):
        row=source_row(record_type='team_report_not_submitted',player_name=None,status=None,reason='NOT YET SUBMITTED')
        result=self.match(rows=[row])[0]
        self.assertEqual(result['match_status'],'team_report_only')
        self.assertIsNone(result['provider_player_id'])

    def test_new_partial_or_stale_snapshots_hide_previous_match_without_deleting_it(self):
        check={'mapping_version':VERSION,'snapshot_runs':{'roster':'original'},'source_sha256':'a','source_parser_version':'v3',
               'source_first_observed_at':iso(AT)}
        original=copy.deepcopy(check)
        evidence={'source_sha256':'a','parser_version':'v3'}
        snapshots=[{'kind':'roster','run_id':'original','pagination_finished':True,'created':iso(AT)}]
        with patch('nba_bot.statistics_identity.now',return_value=AT):
            self.assertEqual(visible_check(check,snapshots,evidence),check)
            snapshots[0]['run_id']='new-partial'; snapshots[0]['pagination_finished']=False
            self.assertEqual(visible_check(check,snapshots,evidence)['status'],'not_ready')
        snapshots[0]['run_id']='original'; snapshots[0]['pagination_finished']=True
        with patch('nba_bot.statistics_identity.now',return_value=AT+timedelta(days=2)):
            self.assertEqual(visible_check(check,snapshots,evidence)['status'],'not_ready')
        self.assertEqual(check,original)
