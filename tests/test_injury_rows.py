import unittest

from nba_bot.injury_rows import report_rows


STARTS=(24,120,200,265,425,586,667)


def line(y,**cells):
    fields=('date','time','matchup','team','player','status','reason')
    return [(STARTS[fields.index(key)],y,text) for key,text in cells.items()]


def page(number,total,fragments,header=False):
    words=[(280,780,'Injury Report: 10/20/26 07:30 PM'),(390,20,f'Page {number} of {total}')]
    if header:
        words += [(24,740,'Game'),(53,740,'Date'),(120,740,'Game'),(150,740,'Time'),
                  (200,740,'Matchup'),(265,740,'Team'),(425,740,'Player'),(457,740,'Name'),
                  (586,740,'Current'),(624,740,'Status'),(667,740,'Reason')]
    return {'page':number,'fragments':words+fragments}


def first_row(**changes):
    return {'date':'10/20/2026','time':'07:00 (ET)','matchup':'BOS@DET',
            'team':'Boston Celtics','player':'Example, First','status':'Out','reason':'Personal Reasons',**changes}


class InjuryRowTests(unittest.TestCase):
    def test_reason_above_and_below_player_stays_with_correct_row(self):
        fragments=line(720,**first_row())
        fragments+=line(697,reason='Injury/Illness - Low Back Stress')
        fragments+=line(690,player='Example, Second',status='Out')
        fragments+=line(683,reason='Reaction')
        result=report_rows([page(1,1,fragments,header=True)])
        self.assertEqual(result['status'],'parsed_source_labels')
        self.assertEqual(result['player_status_count'],2)
        self.assertEqual(result['rows'][0]['reason'],'Personal Reasons')
        self.assertEqual(result['rows'][1]['reason'],'Injury/Illness - Low Back Stress Reaction')
        self.assertEqual(result['rows'][1]['team'],'Boston Celtics')
        self.assertIsNone(result['rows'][1]['player_id'])
        self.assertIsNone(result['rows'][1]['event_id'])

    def test_page_continuations_preserve_context_and_explicit_new_game_replaces_it(self):
        first=page(1,2,line(720,**first_row()),header=True)
        second=page(2,2,line(730,player='Example, NextPage',status='Questionable',reason='Injury/Illness - Illness')+
                    line(690,**first_row(date='10/21/2026',time='08:00 (ET)',matchup='IND@NOP',team='Indiana Pacers',player='Example, NewGame')))
        result=report_rows([first,second])
        self.assertEqual(result['status'],'parsed_source_labels')
        continuation=result['rows'][1]
        self.assertEqual(continuation['source_page'],2)
        self.assertEqual(continuation['game_date'],'2026-10-20')
        self.assertEqual(continuation['matchup'],'BOS@DET')
        self.assertEqual(continuation['team'],'Boston Celtics')
        self.assertEqual(result['rows'][2]['game_date'],'2026-10-21')
        self.assertEqual(result['rows'][2]['matchup'],'IND@NOP')
        self.assertEqual(result['rows'][2]['team'],'Indiana Pacers')

    def test_unsubmitted_report_is_not_a_player_or_health_claim(self):
        fragments=line(720,**first_row())+line(690,team='Detroit Pistons',reason='NOT YET SUBMITTED')
        result=report_rows([page(1,1,fragments,header=True)])
        self.assertEqual(result['not_submitted_count'],1)
        self.assertEqual(result['player_status_count'],1)
        row=result['rows'][1]
        self.assertEqual(row['record_type'],'team_report_not_submitted')
        self.assertIsNone(row['player_name'])
        self.assertIsNone(row['status'])
        self.assertEqual(row['identity_match_status'],'unverified')

    def test_ambiguous_wrapped_line_returns_no_partial_success(self):
        fragments=line(720,**first_row())+line(690,player='Example, Second',status='Out',reason='Illness')
        fragments+=line(705,reason='Ambiguous continuation')
        result=report_rows([page(1,1,fragments,header=True)])
        self.assertEqual(result['status'],'unrecognized_layout')
        self.assertEqual(result['rows'],[])

    def test_unknown_status_or_duplicate_or_missing_game_context_returns_no_rows(self):
        cases=[line(720,**first_row(status='Likely')),
               line(720,**first_row(time='25:99 (ET)')),
               line(720,**first_row())+line(690,player='Example, First',status='Out',reason='Illness'),
               line(720,**first_row())+line(690,date='10/21/2026',player='Example, Second',status='Out',reason='Illness'),
               line(720,**first_row())+line(690,matchup='IND@NOP',player='Example, Second',status='Out',reason='Illness')]
        for fragments in cases:
            with self.subTest(fragments=fragments):
                result=report_rows([page(1,1,fragments,header=True)])
                self.assertEqual(result['status'],'unrecognized_layout')
                self.assertEqual(result['rows'],[])

    def test_missing_header_or_missing_page_identity_returns_no_rows(self):
        self.assertEqual(report_rows([page(1,1,line(720,**first_row()))])['rows'],[])
        source=page(1,1,line(720,**first_row()),header=True)
        source['fragments']=[f for f in source['fragments'] if f[2]!='Page 1 of 1']
        self.assertEqual(report_rows([source])['status'],'unrecognized_layout')
