import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from nba_bot.current_data import (SCOREBOARD, check, fetch, latest_injury_link,
                                  archive, minutes, parse_boxscore, parse_scoreboard, season_start)
from nba_bot.store import Store


def scoreboard(status=2):
    return {'scoreboard':{'leagueId':'00','gameDate':'2026-10-20','games':[
        {'gameId':'0022600001','gameTimeUTC':'2026-10-21T00:00:00Z','gameStatus':status,'gameStatusText':'Live',
         'homeTeam':{'teamId':1,'teamCity':'Home','teamName':'Team','score':20},
         'awayTeam':{'teamId':2,'teamCity':'Away','teamName':'Team','score':18}}]}}


def boxscore():
    return {'game':{'gameId':'0022600001','gameStatus':2,'period':1,
        'homeTeam':{'teamId':1,'score':20,'players':[{'personId':101,'name':'Player A','played':'1','statistics':
            {'minutes':'PT05M30.00S','points':10,'reboundsTotal':3,'assists':2,'threePointersMade':2}}]},
        'awayTeam':{'teamId':2,'score':18,'players':[{'personId':102,'name':'Player B','played':'0','statistics':{}}]}}}


class CurrentDataTests(unittest.TestCase):
    def test_iso_minutes_and_unknown_values(self):
        self.assertAlmostEqual(minutes('PT25M01.00S'),25+1/60)
        self.assertEqual(minutes('PT1H02M30S'),62.5)
        self.assertIsNone(minutes(None))
        with self.assertRaises(ValueError): minutes('unknown')

    def test_scoreboard_identity_and_live_vs_final_boxscore(self):
        _,games=parse_scoreboard(scoreboard())
        result=parse_boxscore(boxscore(),games[0])
        self.assertFalse(result['final'])
        self.assertEqual(result['players'][0]['minutes'],5.5)
        self.assertIsNone(result['players'][1]['points'])
        self.assertIsNone(result['teams'][0]['expected_final_minutes'])
        wrong=copy.deepcopy(boxscore())
        wrong['game']['awayTeam']['teamId']=99
        with self.assertRaises(ValueError): parse_boxscore(wrong,games[0])

    def test_final_overtime_minutes_accounting(self):
        _,games=parse_scoreboard(scoreboard())
        source=boxscore()
        source['game']['gameStatus']=3
        source['game']['period']=5
        result=parse_boxscore(source,games[0])
        self.assertEqual(result['teams'][0]['expected_final_minutes'],265)
        self.assertFalse(result['teams'][0]['final_minutes_within_rounding_tolerance'])

    def test_latest_official_pdf_and_untrusted_link_rejection(self):
        html='''<a href="https://example.test/Injury-Report_2026-10-20_11_59PM.pdf">bad</a>
        <a href="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-20_11_00AM.pdf">old</a>
        <a href="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-20_06_45PM.pdf">new</a>'''
        link,label=latest_injury_link(html,'https://official.nba.com/')
        self.assertIn('06_45PM',link)
        self.assertEqual(label,'2026-10-20 06:45PM')
        with self.assertRaises(ValueError): latest_injury_link('<a href="https://example.test/not.pdf">bad</a>','https://official.nba.com/')

    def test_season_boundary_uses_eastern_date(self):
        self.assertEqual(season_start(datetime(2026,1,1,tzinfo=timezone.utc)),2025)
        self.assertEqual(season_start(datetime(2026,10,20,tzinfo=timezone.utc)),2026)

    def test_fetch_preserves_original_bytes_and_url_without_credentials(self):
        class Response(io.BytesIO):
            headers={'Date':'test HTTP date'}
            def geturl(self): return SCOREBOARD
        with tempfile.TemporaryDirectory() as folder, patch('nba_bot.current_data.urlopen',return_value=Response(b'{"sample":1}')):
            raw,metadata=fetch(SCOREBOARD,folder)
            self.assertEqual(Path(metadata['artifact_path']).read_bytes(),raw)
            self.assertEqual(metadata['url'],SCOREBOARD)

    def test_source_checks_do_not_use_odds_quota_and_repeats_are_deferred(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sources.sqlite'
            with patch('nba_bot.current_data.run_source',return_value={'status':'ok','artifacts':[]}), patch('nba_bot.providers.OddsAPI') as odds:
                result=check(path)
                self.assertEqual(result['odds_api_requests'],0)
                self.assertEqual(check(path)['status'],'deferred')
                odds.assert_not_called()
            store=Store(path)
            try:
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM collection_attempts').fetchone()[0],0)
                self.assertIsNotNone(store.report(False)['latest_current_data_check'])
                self.assertIsNone(store.report(True)['latest_current_data_check'])
            finally: store.close()

    def test_failed_artifact_publication_does_not_leave_partial_canonical_bytes(self):
        import hashlib
        raw=b'{"sample":1}'
        metadata={'sha256':hashlib.sha256(raw).hexdigest()}
        with tempfile.TemporaryDirectory() as folder,patch('nba_bot.current_data.os.link',side_effect=OSError('Interrupted publication')):
            with self.assertRaises(OSError): archive(raw,metadata,folder)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_failed_schema_still_retains_source_artifact_provenance(self):
        from nba_bot.current_data import run_source, history_check
        class Response(io.BytesIO):
            headers={}
            def geturl(self): return 'https://stats.nba.com/stats/leaguegamelog'
        with tempfile.TemporaryDirectory() as folder, patch('nba_bot.current_data.urlopen',return_value=Response(b'{"unexpected":1}')):
            result=run_source(history_check,folder,datetime(2026,10,20,tzinfo=timezone.utc))
            self.assertEqual(result['status'],'error')
            self.assertEqual(len(result['artifacts']),1)
            self.assertTrue(Path(result['artifacts'][0]['artifact_path']).exists())


if __name__=='__main__': unittest.main()
