"""Strict source-label joins to versioned BALLDONTLIE snapshots, not NBA IDs."""
from collections import Counter
from datetime import timedelta
import json
import unicodedata

from .core import dt,identity,iso,now

VERSION='exact-name-team-date-opponents-v2-reviewed-aliases'

# Each exception belongs to one verified provider record, not a global nickname
# rule. The source report and operator-supplied persisted roster entry established
# this spelling difference while agreeing on current Chicago team membership.
REVIEWED_PLAYER_ALIASES=(
    {'id':'bdl-666508-nic-nicolas-claxton-v1','provider_player_id':'666508',
     'source_first_name':'Nic','source_last_name':'Claxton',
     'provider_first_name':'Nicolas','provider_last_name':'Claxton',
     'review_evidence':{'review_date':'2026-10-21',
                       'source_pdf_sha256':'7caffe199749674aa1418c96ee4985d9af415d4b1615ae71620ffd9b53588a01',
                       'roster_snapshot_run':'75b0227e4fcd8bf563775e5a19bf44ff09c75cd48eae923d7e34f6a1cc207963',
                       'roster_team_id':'5','basis':'Official report spelling and user-supplied persisted BALLDONTLIE roster record'}},
)


def name(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value)).casefold().split())


def team_name(value):
    normalized=name(value)
    return {'la clippers':'los angeles clippers'}.get(normalized,normalized)


def reviewed_alias(player,first,last):
    return next((alias for alias in REVIEWED_PLAYER_ALIASES
                 if player['id']==alias['provider_player_id']
                 and name(first)==name(alias['source_first_name'])
                 and name(last)==name(alias['source_last_name'])
                 and name(player['first_name'])==name(alias['provider_first_name'])
                 and name(player['last_name'])==name(alias['provider_last_name'])),None)


def match_rows(rows,teams,players,games):
    team_by_id={team['id']:team for team in teams}
    current_ids={p['current_team_id'] for p in players if p.get('current_team_id')}
    current_ids.update(g[side] for g in games for side in ('home_team_id','visitor_team_id'))
    result=[]
    for row in rows:
        candidates=[team for team in teams if team['id'] in current_ids and team_name(team['name'])==team_name(row['team'])]
        item={'source_row':row['source_row'],'source_page':row['source_page'],'game_date':row['game_date'],
              'matchup':row['matchup'],'team':row['team'],'player_name':row['player_name'],
              'report_status':row['status'],'report_reason':row['reason'],'provider_namespace':'balldontlie',
              'provider_team_id':None,'provider_player_id':None,'provider_game_id':None}
        if len(candidates)!=1:
            item['match_status']='ambiguous_team' if candidates else 'unmatched_team'
            result.append(item); continue
        chosen=candidates[0]; item['provider_team_id']=chosen['id']
        away,home=row['matchup'].split('@')
        events=[event for event in games if event['source_calendar_date']==row['game_date']
                and team_by_id.get(event['visitor_team_id'],{}).get('abbreviation')==away
                and team_by_id.get(event['home_team_id'],{}).get('abbreviation')==home
                and chosen['id'] in (event['home_team_id'],event['visitor_team_id'])]
        if len(events)==1: item['provider_game_id']=events[0]['id']
        if row['record_type']=='team_report_not_submitted':
            item['match_status']='team_report_only'
            result.append(item); continue
        last,first=row['player_name'].split(',',1)
        athletes=[p for p in players if p.get('current_team_id')==chosen['id']
                  and (name(p['first_name'])==name(first) and name(p['last_name'])==name(last)
                       or reviewed_alias(p,first,last) is not None)]
        if len(athletes)==1:
            item['provider_player_id']=athletes[0]['id']
            alias=reviewed_alias(athletes[0],first,last)
            item['player_name_match']='reviewed_player_alias' if alias else 'exact_name'
            if alias: item['reviewed_alias']=alias
        if len(athletes)!=1:
            item['match_status']='ambiguous_player' if athletes else 'unmatched_player'
        elif len(events)!=1:
            item['match_status']='ambiguous_game' if events else 'unmatched_game'
        else:
            item['match_status']='matched_provider_identity'
        result.append(item)
    return result


def identify(store):
    runs={}
    for kind in ('teams','roster','games'):
        run=store.db.execute('SELECT * FROM stats_import_runs WHERE kind=? ORDER BY rowid DESC LIMIT 1',(kind,)).fetchone()
        last=store.db.execute('SELECT terminal FROM stats_import_pages WHERE run=? ORDER BY page DESC LIMIT 1',(run['id'],)).fetchone() if run else None
        if not run or not last or not last['terminal']:
            return {'status':'not_ready','reason':f'{kind} snapshot has not finished provider pagination'}
        age=now()-dt(run['created'])
        if not timedelta(0)<=age<=timedelta(hours=24):
            return {'status':'not_ready','reason':f'{kind} snapshot is over 24 hours old or has a future observation time'}
        runs[kind]=run
    original=store.db.execute('SELECT payload FROM injury_texts ORDER BY rowid DESC LIMIT 1').fetchone()
    evidence=json.loads(original['payload']) if original else None
    if not evidence or evidence.get('structured_report',{}).get('status')!='parsed_source_labels':
        return {'status':'not_ready','reason':'Extract an official injury report into structured source rows first'}
    observed=evidence.get('source_first_observed_at')
    if not observed or not timedelta(0)<=now()-dt(observed)<=timedelta(hours=24):
        return {'status':'not_ready','reason':'Official report observation is over 24 hours old or in the future; obtain a fresh source report'}
    key=identity([VERSION,{kind:run['id'] for kind,run in runs.items()},evidence['source_sha256'],evidence['parser_version']])
    previous=store.db.execute('SELECT payload FROM stats_identity_checks WHERE id=?',(key,)).fetchone()
    if previous: return {**json.loads(previous['payload']),'cached':True}
    data={kind:[json.loads(row['payload']) for row in store.db.execute('SELECT payload FROM stats_import_objects WHERE run=? ORDER BY entity',(run['id'],))] for kind,run in runs.items()}
    rows=match_rows(evidence['structured_report']['rows'],data['teams'],data['roster'],data['games'])
    counts=Counter(row['match_status'] for row in rows)
    payload={'status':'checked','mapping_version':VERSION,'recorded':iso(now()),
             'source_sha256':evidence['source_sha256'],'source_parser_version':evidence['parser_version'],'source_first_observed_at':observed,
             'source_report_label':evidence.get('filename_timestamp_label'),
             'snapshot_runs':{kind:run['id'] for kind,run in runs.items()},'counts':dict(counts),'rows':rows,
             'canonical_nba_ids_verified':False,'odds_api_event_ids_matched':False,'cached':False,
             'note':'Exact source names or explicitly reviewed, provider-ID-bound spelling aliases, plus current team, game date and ordered opponents. No generic nickname expansion, fuzzy match, suffix removal or historical-roster guess. Matches identify BALLDONTLIE records in these snapshots, not verified NBA/Odds API IDs, fresh availability, minutes limits or predictions. Source entries and prior matches remain unchanged.'}
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO stats_identity_checks VALUES (?,?,?)',(key,payload['recorded'],json.dumps(payload,sort_keys=True)))
    return json.loads(store.db.execute('SELECT payload FROM stats_identity_checks WHERE id=?',(key,)).fetchone()[0])


def visible_check(check,imports,evidence):
    """Do not present an older match as valid for newly imported/source versions."""
    if not check:
        return None
    if check.get('mapping_version')!=VERSION:
        return {'status':'not_ready','reason':'Identity rules have changed; run identity-check to create a new audit result from the saved snapshots.'}
    active={item['kind']:item for item in imports}
    for kind,run in check['snapshot_runs'].items():
        snapshot=active.get(kind)
        if not snapshot or snapshot['run_id']!=run or not snapshot['pagination_finished']:
            return {'status':'not_ready','reason':'New or unfinished snapshots need a new identity check; prior matches remain in the audit history.'}
        if not timedelta(0)<=now()-dt(snapshot['created'])<=timedelta(hours=24):
            return {'status':'not_ready','reason':'The matched snapshots are over 24 hours old or have future observation times.'}
    if not evidence or evidence.get('source_sha256')!=check['source_sha256'] or evidence.get('parser_version')!=check['source_parser_version']:
        return {'status':'not_ready','reason':'The official source report has changed; a new identity check is needed.'}
    if not timedelta(0)<=now()-dt(check['source_first_observed_at'])<=timedelta(hours=24):
        return {'status':'not_ready','reason':'The official source observation is over 24 hours old or in the future.'}
    return check
