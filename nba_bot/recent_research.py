"""Provider reconciliation and retrospective, prior-date-only baseline research."""
from collections import Counter,defaultdict,deque
from datetime import date,timedelta
from itertools import groupby
import json
import math

from .core import identity,iso,now
from .history import replay as game_replay,metrics as game_metrics,PARAMETERS as GAME_PARAMETERS
from .refresh import storage_status
from .store import Store

VERSION='recent-provider-retrospective-v1'
TARGETS=('minutes','points','rebounds','assists','threes')
PARAMETERS={'player_window':10,'rate_window':20,'minutes_window':5,'minimum_appearances':10,
            'validation_month':3,'final_month':4,'final_end_month':7,
            'same_day_updates':'after_all_forecasts','player_evaluation_condition':'recorded positive-minute appearances'}


def minute_uncertainty(row):
    """One reported unit, not assumed second-level accuracy for whole minutes."""
    label=str(row.get('source_minutes_label',''))
    if ':' in label: return 1/60
    if '.' in label: return 10**(-min(6,len(label.split('.',1)[1])))
    return 1.0


def reconcile(schedule,stats,franchises):
    issues=Counter(); examples=defaultdict(list)
    def issue(kind,detail):
        issues[kind]+=1
        if len(examples[kind])<20: examples[kind].append(detail)
    if len(franchises)!=30: issue('franchise_scope_not_30',{'identified_franchises':len(franchises)})
    in_scope=lambda game: {game['home_team_id'],game['visitor_team_id']}<=franchises
    expected={}; excluded_schedule=0
    for game in schedule:
        if not in_scope(game): excluded_schedule+=1; continue
        if game['id'] in expected: issue('duplicate_schedule_game',game['id'])
        if not game['final']:
            issue('nonfinal_franchise_game',game['id']); continue
        expected[game['id']]=game
        if not game.get('home_score') or not game.get('visitor_score') or game['home_score']==game['visitor_score']:
            issue('invalid_final_score',game['id'])
        if game.get('source_period') is None or not 4<=game['source_period']<=12:
            issue('unknown_or_invalid_final_period',game['id'])
    grouped=defaultdict(list); excluded_stats=0; seen=set(); eligible=[]
    unknown_participation=0; zero_minute_rows=0
    for row in stats:
        event=row['game']
        if not in_scope(event): excluded_stats+=1; continue
        key=(event['id'],row['player']['id'])
        if key in seen: issue('duplicate_player_game',{'game':key[0],'player':key[1]})
        seen.add(key)
        if event['id'] not in expected:
            issue('stat_game_not_in_final_schedule',event['id']); continue
        reference=expected[event['id']]
        fields=('source_calendar_date','home_team_id','visitor_team_id','season','postseason','final','source_period','home_score','visitor_score')
        if any(event.get(field)!=reference.get(field) for field in fields):
            issue('embedded_game_conflicts_with_schedule',event['id'])
        if row['team']['id'] not in (reference['home_team_id'],reference['visitor_team_id']):
            issue('stat_team_not_in_game',row['id'])
        grouped[event['id']].append(row)
        if row['minutes'] is None:
            unknown_participation+=1
            if any((row.get(target) or 0)>0 for target in TARGETS[1:]):
                issue('positive_stats_without_minutes',row['id'])
        elif row['minutes']==0:
            zero_minute_rows+=1
        elif not all(row.get(target) is not None for target in TARGETS):
            issue('played_row_missing_required_fields',row['id'])
        else:
            eligible.append(row)
        if row.get('points') is not None and row.get('threes') is not None and row['points']<3*row['threes']:
            issue('impossible_threes_and_points',row['id'])
        if all(row.get(field) is not None for field in ('rebounds','offensive_rebounds','defensive_rebounds')) and row['rebounds']!=row['offensive_rebounds']+row['defensive_rebounds']:
            issue('inconsistent_rebound_components',row['id'])
    missing=sorted(set(expected)-set(grouped))
    for game_id in missing: issue('missing_game_stats',game_id)
    minute_checks=[]
    for game_id,rows in grouped.items():
        reference=expected[game_id]
        for side in ('home','visitor'):
            team_id=reference[side+'_team_id']
            own=[row for row in rows if row['team']['id']==team_id]
            played=[row for row in own if row['minutes'] is not None and row['minutes']>0]
            if len(played)<5: issue('fewer_than_five_positive_minute_players',{'game':game_id,'team':team_id})
            points=sum(row['points'] for row in own if row.get('points') is not None)
            if points!=reference[side+'_score']:
                issue('player_points_do_not_sum_to_score',{'game':game_id,'team':team_id,'known_sum':points,'game_score':reference[side+'_score']})
            if reference.get('source_period') is not None:
                target=240+25*max(0,reference['source_period']-4)
                reported=sum(row['minutes'] for row in played)
                uncertainty=1+sum(minute_uncertainty(row) for row in played)
                consistent=abs(reported-target)<=uncertainty+1e-6
                if not consistent:
                    issue('minutes_outside_reported_precision',{'game':game_id,'team':team_id,'known_sum':reported,'expected':target,'tolerance':uncertainty})
                minute_checks.append(consistent)
    if not expected: issue('no_final_games_in_scope',None)
    report={'status':'passed_provider_checks' if not issues else 'blocked',
            'scope_franchise_count':len(franchises),'scheduled_final_games':len(expected),
            'games_with_stat_rows':len(grouped),'stat_rows_in_scope':sum(map(len,grouped.values())),
            'positive_minute_rows_with_required_fields':len(eligible),
            'unknown_minute_rows_excluded_from_player_evaluation':unknown_participation,
            'zero_minute_rows_excluded_from_player_evaluation':zero_minute_rows,
            'excluded_nonfranchise_schedule_rows':excluded_schedule,'excluded_nonfranchise_stat_rows':excluded_stats,
            'team_minute_checks':len(minute_checks),'team_minute_checks_passed':sum(minute_checks),
            'issues':dict(issues),'issue_examples':dict(examples),'complete_nba_coverage_verified':False,
            'note':'Reconciled only against this provider schedule and a 30-franchise scope derived from saved team/active-roster snapshots. Precision-aware minute checks cannot prove every appearance is present. Nonfranchise games are reported as exclusions; no historical player is filtered merely for being inactive now.'}
    games=[{'id':g['id'],'day':g['source_calendar_date'],'season':g['season'],
            'home':g['home_team_id'],'away':g['visitor_team_id'],'home_score':g['home_score'],
            'away_score':g['visitor_score']} for g in expected.values()]
    return report,games,eligible


def replay_players(rows):
    history=defaultdict(lambda:deque(maxlen=PARAMETERS['rate_window']))
    league_sums=Counter(); league_n=0; predictions=[]
    ordered=sorted(rows,key=lambda row:(row['game']['source_calendar_date'],row['game']['id'],row['id']))
    for day,batch in groupby(ordered,key=lambda row:row['game']['source_calendar_date']):
        batch=list(batch)
        for row in batch:
            prior=list(history[row['player']['id']])
            if len(prior)<PARAMETERS['minimum_appearances'] or not league_n: continue
            window=prior[-PARAMETERS['player_window']:]
            minute_rows=prior[-PARAMETERS['minutes_window']:]
            projected_minutes=sum(r['minutes'] for r in minute_rows)/len(minute_rows)
            prior_minutes=sum(r['minutes'] for r in prior)
            player_mean={target:sum(r[target] for r in window)/len(window) for target in TARGETS}
            exposure={target:projected_minutes*sum(r[target] for r in prior)/prior_minutes for target in TARGETS[1:]}
            exposure['minutes']=projected_minutes
            predictions.append({'stat_id':row['id'],'game_id':row['game']['id'],'player_id':row['player']['id'],
                                'day':day,'prior_window_appearances':len(prior),
                                'actual':{target:row[target] for target in TARGETS},
                                'forecasts':{'prior_league_appearance_mean':{t:league_sums[t]/league_n for t in TARGETS},
                                             'rolling_player_mean':player_mean,'projected_minutes_times_prior_rate':exposure}})
        # No target-day minutes/results enter ANY forecast made on that date.
        for row in batch:
            history[row['player']['id']].append(row)
            for target in TARGETS: league_sums[target]+=row[target]
            league_n+=1
    return predictions


def player_metrics(rows):
    totals={}
    for row in rows:
        for model,forecast in row['forecasts'].items():
            for target in TARGETS:
                key=(model,target)
                count,absolute,squared,bias=totals.get(key,(0,0,0,0))
                error=forecast[target]-row['actual'][target]
                totals[key]=(count+1,absolute+abs(error),squared+error*error,bias+error)
    return {model:{target:{'records':n,'mae':absolute/n,'rmse':math.sqrt(squared/n),'bias':bias/n}
                   for (candidate,target),(n,absolute,squared,bias) in totals.items() if candidate==model}
            for model in sorted({model for model,_ in totals})}


def evaluate(path):
    store=Store(path)
    try:
        runs={}
        for kind in ('teams','roster','history-games','history'):
            run=store.db.execute('SELECT * FROM stats_import_runs WHERE kind=? ORDER BY rowid DESC LIMIT 1',(kind,)).fetchone()
            last=store.db.execute('SELECT terminal FROM stats_import_pages WHERE run=? ORDER BY page DESC LIMIT 1',(run['id'],)).fetchone() if run else None
            if not run or not last or not last['terminal']:
                return {'status':'not_ready','reason':f'{kind} snapshot must finish provider pagination first','odds_api_requests':0,'statistics_api_requests':0}
            runs[kind]=run
        window=json.loads(runs['history']['params'])
        schedule_window=json.loads(runs['history-games']['params'])
        if any(window.get(key)!=schedule_window.get(key) for key in ('seasons[]','start_date','end_date','postseason')):
            return {'status':'not_ready','reason':'Historical schedule/stat snapshots use different season/date windows','odds_api_requests':0,'statistics_api_requests':0}
        year=window['seasons[]'][0]+1
        if date.fromisoformat(window['start_date'])>date(year,2,1) or date.fromisoformat(window['end_date'])<date(year,4,30):
            return {'status':'not_ready','reason':'The research needs February warm-up, March validation and April final-evaluation coverage'}
        key=identity([VERSION,{kind:run['id'] for kind,run in runs.items()},PARAMETERS,GAME_PARAMETERS])
        old=store.db.execute('SELECT payload FROM statistics_research_reports WHERE id=?',(key,)).fetchone()
        if old: return {**json.loads(old['payload']),'cached':True}
        if not storage_status(store.path)['collection_allowed']:
            return {'status':'not_ready','reason':'Insufficient disk headroom for research records'}
        data={kind:[json.loads(row[0]) for row in store.db.execute('SELECT payload FROM stats_import_objects WHERE run=? ORDER BY entity',(run['id'],))] for kind,run in runs.items()}
        known_teams={team['id'] for team in data['teams']}
        franchises={player['current_team_id'] for player in data['roster'] if player.get('current_team_id')}
        unknown_franchises=franchises-known_teams
        franchises&=known_teams
        coverage,games,played=reconcile(data['history-games'],data['history'],franchises)
        if unknown_franchises:
            coverage['status']='blocked'
            coverage['issues']['unknown_roster_team_references']=len(unknown_franchises)
            coverage['issue_examples']['unknown_roster_team_references']=sorted(unknown_franchises)[:20]
        result={'status':'blocked_data_quality','research_version':VERSION,'created':iso(now()),'cached':False,
                'provider_namespace':'balldontlie','source_snapshot_runs':{kind:run['id'] for kind,run in runs.items()},
                'requested_window':window,'coverage':coverage,'parameters':PARAMETERS,
                'game_parameters':{k:GAME_PARAMETERS[k] for k in ('initial_elo','elo_k','home_advantage_elo','score_window','minimum_team_games','home_score_adjustment')},
                'odds_api_requests':0,'statistics_api_requests':0,
                'limitations':['Retrospective provider snapshots were collected after the games; this is not a prospective/as-of backtest',
                               'Current roster identifies franchise scope only; inactive historical players are retained; franchise changes are not modeled',
                               'Player metrics condition on recorded positive-minute appearances; participation and DNP outcomes are not predicted',
                               'No historical pregame injuries, odds, prop lines, market baseline, ROI, selected-bet win rate or confidence intervals',
                               'Player minutes forecasts are not constrained jointly to team totals or opposing-player/role consistency',
                               'Fixed parameters; same-day forecasts frozen before updates; earlier evaluation-day results can update subsequent dates',
                               'No current forecasts, selections, automated settlement or live trial are enabled']}
        predictions=[]
        if coverage['status']=='passed_provider_checks':
            game_predictions=game_replay(games)
            player_predictions=replay_players(played)
            validation_start=f'{year}-03-01'; final_start=f'{year}-04-01'
            final_end=min(date(year,7,1),date.fromisoformat(window['end_date'])+timedelta(days=1)).isoformat()
            result['periods']={'warmup':{'start':window['start_date'],'end_exclusive':validation_start},
                               'validation':{'start':validation_start,'end_exclusive':final_start},
                               'final_evaluation':{'start':final_start,'end_exclusive':final_end}}
            for period,start,end in (('validation',validation_start,final_start),('final_evaluation',final_start,final_end)):
                game_rows=[r for r in game_predictions if start<=r['day']<end]
                player_rows=[r for r in player_predictions if start<=r['day']<end]
                result[period]={'game_metrics':game_metrics(game_rows),'player_metrics':player_metrics(player_rows),
                                'player_game_records_scored':len(player_rows),'played_rows_skipped_insufficient_history':sum(start<=r['game']['source_calendar_date']<end for r in played)-len(player_rows)}
            has_holdouts=all(result[period]['game_metrics']['winner_baselines']['elo']['games'] and result[period]['player_game_records_scored'] for period in ('validation','final_evaluation'))
            result['status']='research_evaluated' if has_holdouts else 'insufficient_evaluation_records'
            predictions=[('game',r['id'],r) for r in game_predictions]+[('player',r['stat_id'],r) for r in player_predictions]
        encoded=json.dumps(result,sort_keys=True,allow_nan=False)
        prediction_records=[(key,kind,entity,json.dumps(row,sort_keys=True,allow_nan=False)) for kind,entity,row in predictions]
        estimated_bytes=len(encoded.encode())+sum(len(row[3].encode()) for row in prediction_records)
        space=storage_status(store.path)
        if not space['collection_allowed'] or space['free_bytes']<space['required_free_bytes']+3*estimated_bytes:
            return {'status':'not_ready','reason':'Insufficient disk headroom for research records, WAL and the next backup'}
        with store.db:
            inserted=store.db.execute('INSERT OR IGNORE INTO statistics_research_reports VALUES (?,?,?)',(key,result['created'],encoded)).rowcount
            if inserted:
                store.db.executemany('INSERT INTO statistics_research_predictions VALUES (?,?,?,?)',prediction_records)
        # A simultaneous evaluation reuses the first committed report, rather
        # than overwriting it or attempting duplicate prediction rows.
        stored=json.loads(store.db.execute('SELECT payload FROM statistics_research_reports WHERE id=?',(key,)).fetchone()[0])
        return {**stored,'cached':not bool(inserted)}
    finally:
        store.close()
