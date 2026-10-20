import copy
import csv
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from nba_bot.history import load_games, metrics, replay, evaluate


def game(index, day=None, home='A', away='B', hs=110, aws=100):
    return {'id':str(index), 'day':day or (date(2014,1,1)+timedelta(days=index)).isoformat(),
            'season':2014, 'home':home, 'away':away, 'home_score':hs, 'away_score':aws}


class HistoryTests(unittest.TestCase):
    def test_future_results_cannot_change_earlier_predictions(self):
        original = [game(i, hs=105+i%5, aws=100) for i in range(30)]
        modified = copy.deepcopy(original)
        modified[-1]['home_score']=50
        modified[-1]['away_score']=150
        a,b=replay(original),replay(modified)
        self.assertEqual(a[:-1],b[:-1])
        self.assertEqual(a[-1]['home_win_probability'],b[-1]['home_win_probability'])
        self.assertEqual(a[-1]['projected_home_score'],b[-1]['projected_home_score'])

    def test_same_day_results_cannot_leak(self):
        games=[game(1,day='2014-01-01'),game(2,day='2014-01-01',home='A',away='C')]
        changed=copy.deepcopy(games)
        changed[0]['home_score']=60
        changed[0]['away_score']=160
        a,b=replay(games),replay(changed)
        self.assertEqual(a[1]['home_win_probability'],b[1]['home_win_probability'])
        self.assertEqual(a[1]['historical_home_probability'],b[1]['historical_home_probability'])

    def test_initial_predictions_and_score_warmup(self):
        predictions=replay([game(i) for i in range(12)])
        self.assertEqual(predictions[0]['historical_home_probability'],.5)
        self.assertTrue(all(p['projected_home_score'] is None for p in predictions[:10]))
        self.assertIsNotNone(predictions[10]['projected_home_score'])
        report=metrics(predictions)
        self.assertEqual(report['winner_baselines']['elo']['games'],12)
        self.assertEqual(report['scoring_baselines']['rolling_team_scoring']['games'],2)
        self.assertEqual(report['score_games_skipped_insufficient_history'],10)

    def test_copy_rows_and_neutral_games_excluded(self):
        fields=['game_id','lg_id','_iscopy','year_id','date_game','is_playoffs','team_id','opp_id','pts','opp_pts','game_location']
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=fields)
        writer.writeheader()
        row=dict(zip(fields,['one','NBA','0','2014','1/1/2014','0','A','B','100','110','A']))
        writer.writerow(row)
        writer.writerow({**row,'_iscopy':'1'})
        writer.writerow({**row,'game_id':'neutral','game_location':'N'})
        games=load_games(output.getvalue().encode())
        self.assertEqual(len(games),1)
        self.assertEqual(games[0]['home'],'B')
        self.assertEqual(games[0]['home_score'],110)
        writer.writerow(row)
        with self.assertRaises(ValueError):
            load_games(output.getvalue().encode())

    def test_unknown_snapshot_cannot_be_published_as_reviewed_research(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'unreviewed.csv'
            source.write_text('unreviewed')
            with self.assertRaises(ValueError):
                evaluate(source)


if __name__=='__main__':
    unittest.main()
