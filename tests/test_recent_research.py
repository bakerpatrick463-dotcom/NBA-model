import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from nba_bot.recent_research import evaluate,reconcile,replay_players
from nba_bot.statistics_import import choose_run,save_page,specifications
from nba_bot.statistics_provider import game,statistic
from nba_bot.store import Store
from test_statistics_provider import AT


def fixtures():
    teams=[{'id':i,'full_name':f'Synthetic Team {i}','abbreviation':f'T{i:02}'} for i in range(1,31)]
    roster=[{'id':i,'first_name':f'Player{i}','last_name':'Example','team_id':1 if i<=5 else 2} for i in range(1,11)]
    roster += [{'id':100+i,'first_name':f'Other{i}','last_name':'Example','team_id':i} for i in range(3,31)]
    days=[f'2026-02-{i:02}' for i in range(1,13)]+[f'2026-03-{i:02}' for i in range(1,5)]+[f'2026-04-{i:02}' for i in range(1,5)]
    games=[]; stats=[]
    for index,day in enumerate(days):
        event={'id':index+100,'date':day,'season':2025,'postseason':False,'status':'Final','period':4,
               'home_team_id':1,'visitor_team_id':2,'home_team_score':60,'visitor_team_score':50}
        games.append(event)
        for player in roster[:10]:
            own=player['team_id']
            stats.append({'id':10000+index*10+player['id'],'game':copy.deepcopy(event),'player':copy.deepcopy(player),
                          'team':teams[own-1],'min':'48','pts':12 if own==1 else 10,'reb':4,'oreb':1,'dreb':3,'ast':3,'fg3m':1})
    return teams,roster,games,stats


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        _,_,games,stats=fixtures()
        self.games=[game(g) for g in games]; self.stats=[statistic(r) for r in stats]
        self.scope={str(i) for i in range(1,31)}

    def test_valid_score_and_precision_checks_reconcile_without_claiming_independent_nba_coverage(self):
        result,games,players=reconcile(self.games,self.stats,self.scope)
        self.assertEqual(result['status'],'passed_provider_checks')
        self.assertEqual(result['scheduled_final_games'],20)
        self.assertEqual(result['team_minute_checks_passed'],40)
        self.assertEqual(len(players),200)
        self.assertFalse(result['complete_nba_coverage_verified'])
        rounded=copy.deepcopy(self.stats); rounded[0]['minutes']=47; rounded[0]['source_minutes_label']='47'
        self.assertEqual(reconcile(self.games,rounded,self.scope)[0]['status'],'passed_provider_checks')

    def test_missing_games_or_players_and_conflicting_scores_block_research(self):
        without_game=[row for row in self.stats if row['game']['id']!=self.games[0]['id']]
        result,_,_=reconcile(self.games,without_game,self.scope)
        self.assertEqual(result['issues']['missing_game_stats'],1)
        self.assertEqual(result['status'],'blocked')
        result,_,_=reconcile(self.games,self.stats[1:],self.scope)
        self.assertIn('player_points_do_not_sum_to_score',result['issues'])
        self.assertIn('minutes_outside_reported_precision',result['issues'])
        conflicting=copy.deepcopy(self.stats); conflicting[0]['game']['home_score']=61
        self.assertIn('embedded_game_conflicts_with_schedule',reconcile(self.games,conflicting,self.scope)[0]['issues'])

    def test_nonfranchise_games_are_counted_as_exclusions_and_unknown_fields_stay_unknown(self):
        extra=copy.deepcopy(self.games[0]); extra.update(id='999',home_team_id='77')
        result,_,_=reconcile(self.games+[extra],self.stats,self.scope)
        self.assertEqual(result['excluded_nonfranchise_schedule_rows'],1)
        self.assertEqual(result['status'],'passed_provider_checks')
        missing=copy.deepcopy(self.stats); missing[0]['points']=None
        result,_,_=reconcile(self.games,missing,self.scope)
        self.assertIn('played_row_missing_required_fields',result['issues'])
        self.assertIsNone(missing[0]['points'])
        self.assertEqual(reconcile(self.games,self.stats,{'1','2'})[0]['status'],'blocked')

    def test_future_or_same_day_results_and_target_minutes_cannot_change_current_forecasts(self):
        base=replay_players(self.stats)
        changed=copy.deepcopy(self.stats)
        first=next(row for row in changed if row['game']['source_calendar_date']=='2026-03-01' and row['player']['id']=='1')
        first['minutes']=5; first['points']=100
        later=replay_players(changed)
        before={row['stat_id']:row['forecasts'] for row in base if row['day']<='2026-03-01'}
        after={row['stat_id']:row['forecasts'] for row in later if row['day']<='2026-03-01'}
        self.assertEqual(before,after)
        extra=copy.deepcopy(first); extra['id']='99999'; extra['game']['id']='999'
        same_day=replay_players(self.stats+[extra])
        expected=next(row['forecasts'] for row in base if row['player_id']=='1' and row['day']=='2026-03-01')
        actual=next(row['forecasts'] for row in same_day if row['stat_id']=='99999')
        self.assertEqual(actual,expected)


class RecentResearchStorageTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'paper.sqlite'

    def seed(self,missing_game=False):
        teams,roster,games,stats=fixtures()
        if missing_game: stats=[row for row in stats if row['game']['id']!=games[0]['id']]
        specs=specifications(AT); store=Store(self.path)
        for kind,rows in [('teams',teams),('roster',roster),('history-games',games),('history',stats)]:
            run=choose_run(store,kind,specs[kind],False)
            for start in range(0,len(rows),100):
                batch=rows[start:start+100]
                next_cursor=batch[-1]['id'] if start+100<len(rows) else None
                save_page(store,run,{'data':batch,'meta':{'next_cursor':next_cursor}},[])
        store.close()

    def test_research_requires_schedule_and_scores_chronological_holdouts_without_live_effects(self):
        self.assertEqual(evaluate(self.path)['status'],'not_ready')
        self.seed()
        with patch('nba_bot.statistics_provider.Client') as client:
            result=evaluate(self.path)
            self.assertEqual(result['status'],'research_evaluated')
            self.assertEqual(result['validation']['player_game_records_scored'],40)
            self.assertEqual(result['final_evaluation']['game_metrics']['winner_baselines']['elo']['games'],4)
            self.assertEqual(result['final_evaluation']['player_game_records_scored'],40)
            self.assertEqual(len(result['final_evaluation']['player_metrics']),3)
            self.assertEqual(result['odds_api_requests'],0)
            self.assertTrue(evaluate(self.path)['cached'])
            client.assert_not_called()
        store=Store(self.path)
        try:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM statistics_research_reports').fetchone()[0],1)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM statistics_research_predictions').fetchone()[0],120)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM forecasts').fetchone()[0],0)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM bets').fetchone()[0],0)
            self.assertEqual(store.report(False)['recent_statistics_research']['status'],'research_evaluated')
            self.assertTrue(store.report(False)['recent_statistics_research']['matches_current_import_versions'])
            self.assertIsNone(store.report(True)['recent_statistics_research'])
            self.assertIsNone(store.report(False)['trial_started'])
            with self.assertRaises(sqlite3.DatabaseError): store.db.execute('DELETE FROM statistics_research_predictions')
        finally: store.close()

    def test_failed_reconciliation_is_saved_without_publishing_evaluation_metrics(self):
        self.seed(missing_game=True)
        result=evaluate(self.path)
        self.assertEqual(result['status'],'blocked_data_quality')
        self.assertIn('missing_game_stats',result['coverage']['issues'])
        self.assertNotIn('final_evaluation',result)
        store=Store(self.path)
        try: self.assertEqual(store.db.execute('SELECT COUNT(*) FROM statistics_research_predictions').fetchone()[0],0)
        finally: store.close()

    def test_simultaneous_evaluations_commit_one_complete_research_version(self):
        self.seed()
        barrier=threading.Barrier(2)
        def synchronized(*args):
            barrier.wait(timeout=10)
            return reconcile(*args)
        with patch('nba_bot.recent_research.reconcile',side_effect=synchronized),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(evaluate,[self.path,self.path]))
        self.assertEqual(sorted(result['cached'] for result in results),[False,True])
        store=Store(self.path)
        try:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM statistics_research_reports').fetchone()[0],1)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM statistics_research_predictions').fetchone()[0],120)
        finally: store.close()
