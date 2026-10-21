import copy
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_bot.__main__ import main
from nba_bot.statistics_identity import identify,match_rows,visible_check
from nba_bot.statistics_import import choose_run,save_page
from nba_bot.store import Store

AT=datetime(2026,10,21,17,tzinfo=timezone.utc)
TEAMS=[{'id':'5','name':'Chicago Bulls','abbreviation':'CHI'},
       {'id':'28','name':'Toronto Raptors','abbreviation':'TOR'}]
PLAYER={'id':'666508','first_name':'Nicolas','last_name':'Claxton','current_team_id':'5'}
GAME={'id':'21717926','source_calendar_date':'2026-10-21','home_team_id':'28','visitor_team_id':'5'}
ROW={'source_row':36,'source_page':3,'game_date':'2026-10-21','matchup':'CHI@TOR','team':'Chicago Bulls',
     'player_name':'Claxton, Nic','record_type':'player_status','status':'Out',
     'reason':'Injury/Illness - Left Hamstring Strain'}


class ReviewedAliasTests(unittest.TestCase):
    def test_reviewed_record_matches_with_alias_provenance(self):
        row=match_rows([ROW],TEAMS,[PLAYER],[GAME])[0]
        self.assertEqual(row['match_status'],'matched_provider_identity')
        self.assertEqual(row['provider_player_id'],'666508')
        self.assertEqual(row['provider_game_id'],'21717926')
        self.assertEqual(row['player_name'],'Claxton, Nic')
        self.assertEqual(row['player_name_match'],'reviewed_player_alias')
        self.assertEqual(row['reviewed_alias']['provider_first_name'],'Nicolas')
        self.assertEqual(row['reviewed_alias']['review_evidence']['roster_team_id'],'5')

    def test_alias_cannot_match_another_id_name_suffix_or_team(self):
        for changes in ({'id':'666509'},{'first_name':'Nicholas'},{'last_name':'Claxton Jr.'},{'current_team_id':'28'}):
            with self.subTest(changes=changes):
                result=match_rows([ROW],TEAMS,[{**PLAYER,**changes}],[GAME])[0]
                self.assertEqual(result['match_status'],'unmatched_player')
                self.assertIsNone(result['provider_player_id'])
        other={**ROW,'player_name':'Example, Nic'}
        athlete={**PLAYER,'last_name':'Example'}
        self.assertEqual(match_rows([other],TEAMS,[athlete],[GAME])[0]['match_status'],'unmatched_player')

    def test_alias_does_not_override_ambiguity_or_wrong_opponents(self):
        exact_other={**PLAYER,'id':'999','first_name':'Nic'}
        result=match_rows([ROW],TEAMS,[PLAYER,exact_other],[GAME])[0]
        self.assertEqual(result['match_status'],'ambiguous_player')
        self.assertIsNone(result['provider_player_id'])
        reversed_game={**GAME,'home_team_id':'5','visitor_team_id':'28'}
        self.assertEqual(match_rows([ROW],TEAMS,[PLAYER],[reversed_game])[0]['match_status'],'unmatched_game')

    def test_version_upgrade_appends_result_and_local_cli_reuses_it_without_api(self):
        with tempfile.TemporaryDirectory() as folder,patch('nba_bot.statistics_import.now',return_value=AT),patch('nba_bot.statistics_identity.now',return_value=AT):
            path=Path(folder)/'paper.sqlite'; store=Store(path)
            raw_teams=[{'id':int(t['id']),'full_name':t['name'],'abbreviation':t['abbreviation']} for t in TEAMS]
            raw_player={**PLAYER,'id':666508,'team_id':5}
            raw_game={'id':21717926,'date':'2026-10-21','season':2026,'status':'2026-10-21T23:40:00Z',
                      'postseason':False,'home_team_id':28,'visitor_team_id':5}
            fixtures={'teams':({},raw_teams),'roster':({'per_page':100},[raw_player]),
                      'games':({'per_page':100,'dates[]':['2026-10-21']},[raw_game])}
            for kind,(params,rows) in fixtures.items():
                run=choose_run(store,kind,params,False)
                save_page(store,run,{'data':rows,'meta':{'next_cursor':None}},[])
            evidence={'source_sha256':'7caffe199749674aa1418c96ee4985d9af415d4b1615ae71620ffd9b53588a01',
                      'parser_version':'pypdf-6.21.1-rows-v3','source_first_observed_at':'2026-10-20T23:37:02.055395+00:00',
                      'structured_report':{'status':'parsed_source_labels','rows':[ROW]}}
            original=json.dumps(evidence)
            with store.db: store.db.execute('INSERT INTO injury_texts VALUES (?,?)',('source',original))
            with patch('nba_bot.statistics_identity.VERSION','exact-name-team-date-opponents-v1'),patch('nba_bot.statistics_identity.REVIEWED_PLAYER_ALIASES',()):
                previous=identify(store)
            previous_copy=copy.deepcopy(previous)
            self.assertEqual(previous['counts'],{'unmatched_player':1})
            self.assertEqual(visible_check(previous,[],evidence)['status'],'not_ready')
            corrected=identify(store)
            self.assertEqual(corrected['counts'],{'matched_provider_identity':1})
            self.assertFalse(corrected['cached'])
            self.assertTrue(identify(store)['cached'])
            records=[json.loads(row[0]) for row in store.db.execute('SELECT payload FROM stats_identity_checks ORDER BY rowid')]
            self.assertEqual(len(records),2)
            self.assertEqual(records[0],previous_copy)
            self.assertEqual(store.db.execute('SELECT payload FROM injury_texts').fetchone()[0],original)
            store.close()
            output=io.StringIO()
            with patch('sys.argv',['nba_bot','--db',str(path),'identity-check']),patch('sys.stdout',output),patch('nba_bot.statistics_provider.Client') as client:
                main()
                client.assert_not_called()
            summary=json.loads(output.getvalue())
            self.assertEqual(summary['counts'],{'matched_provider_identity':1})
            self.assertTrue(summary['cached'])
            self.assertNotIn('rows',summary)
