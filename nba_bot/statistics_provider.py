"""Bounded BALLDONTLIE ALL-STAR access check, not a training-feed import."""
from datetime import date, timedelta
import hashlib
import json
import math
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .core import dt, identity, iso, now
from .current_data import archive, integer, season_start
from .refresh import storage_status
from .store import Store

BASE='https://api.balldontlie.io/nba/v1/'
ENDPOINTS={'teams','players/active','games','stats','player_injuries'}
MAX_BODY=2*1024*1024


class StatisticsError(RuntimeError):
    def __init__(self,kind,http_status=None):
        self.kind,self.http_status=kind,http_status
        super().__init__(kind)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        # Never forward an Authorization header to a redirect destination.
        return None


def identifier(value):
    if value is None or isinstance(value,bool) or not re.fullmatch(r'[1-9]\d*',str(value)):
        raise ValueError('Missing or invalid provider identifier')
    return str(value)


def team(row):
    if not isinstance(row,dict) or not row.get('full_name') or not row.get('abbreviation'):
        raise ValueError('Incomplete provider team')
    return {'id':identifier(row['id']),'name':str(row['full_name']),
            'abbreviation':str(row['abbreviation'])}


def player(row):
    if not isinstance(row,dict) or not row.get('first_name') or not row.get('last_name'):
        raise ValueError('Incomplete provider player')
    current=team(row['team']) if row.get('team') else None
    direct=identifier(row['team_id']) if row.get('team_id') is not None else None
    if current and direct and current['id']!=direct:
        raise ValueError('Conflicting current-team identifiers')
    return {'id':identifier(row['id']),'first_name':str(row['first_name']),
            'last_name':str(row['last_name']),'current_team':current,
            'current_team_id':current['id'] if current else direct}


def game(row):
    teams={}
    for side in ('home','visitor'):
        nested=team(row[side+'_team']) if row.get(side+'_team') else None
        direct=identifier(row[side+'_team_id']) if row.get(side+'_team_id') is not None else None
        if nested and direct and nested['id']!=direct:
            raise ValueError('Conflicting game-team identifiers')
        teams[side+'_team_id']=nested['id'] if nested else identifier(direct)
    if teams['home_team_id']==teams['visitor_team_id']:
        raise ValueError('A game cannot have identical opponents')
    calendar=date.fromisoformat(str(row['date'])[:10]).isoformat()
    season=integer(row['season'])
    if season is None or not isinstance(row.get('postseason'),bool):
        raise ValueError('Missing game season or postseason flag')
    status=row.get('status')
    return {'id':identifier(row['id']),**teams,'source_date_raw':row['date'],
            'source_calendar_date':calendar,'season':season,'postseason':row['postseason'],
            'source_status':status,'final':isinstance(status,str) and bool(re.fullmatch(r'Final(?:/\d*OT)?',status)),
            'source_period':integer(row.get('period')),
            'home_score':integer(row.get('home_team_score')),'visitor_score':integer(row.get('visitor_team_score')),
            'note':'Source game date is not assumed to be a scheduled UTC tipoff.'}


def minutes(value):
    if value is None or value=='' or str(value).upper() in {'DNP','DND','N/A','--'}:
        return None
    if isinstance(value,bool):
        raise ValueError('Boolean minutes are invalid')
    text=str(value)
    if re.fullmatch(r'\d+:[0-5]\d',text):
        whole,seconds=text.split(':')
        result=int(whole)+int(seconds)/60
    elif re.fullmatch(r'\d+(?:\.\d+)?',text):
        result=float(text)
    else:
        raise ValueError('Unknown provider minutes format')
    if not math.isfinite(result) or result>120:
        raise ValueError('Implausible minutes value requires source review')
    return result


def statistic(row):
    event=game(row['game'])
    athlete=player(row['player'])
    historical_team=team(row['team'])
    if historical_team['id'] not in (event['home_team_id'],event['visitor_team_id']):
        raise ValueError('Statistic team does not belong to its game')
    result={'id':identifier(row['id']),'player':athlete,'game':event,'team':historical_team,
            'source_minutes_label':row.get('min'),'minutes':minutes(row.get('min'))}
    fields={'points':'pts','rebounds':'reb','assists':'ast','threes':'fg3m','field_goal_attempts':'fga',
            'three_point_attempts':'fg3a','free_throw_attempts':'fta','turnovers':'turnover',
            'personal_fouls':'pf','offensive_rebounds':'oreb','defensive_rebounds':'dreb'}
    result.update({name:integer(row.get(source)) for name,source in fields.items()})
    # Current roster membership can differ after a trade. The stat's own team
    # must match the historical game, not the player's current team field.
    result['baseline_fields_complete']=all(result[key] is not None for key in ('minutes','points','rebounds','assists','threes'))
    return result


def injury(row):
    if not row.get('status'):
        raise ValueError('Missing provider injury status')
    return {'player':player(row['player']),'source_status':str(row['status']),
            'source_description':row.get('description'),'source_return_date_raw':row.get('return_date'),
            'source_updated_at':None,
            'note':'Provider-reported injury label; return date is not a verified return or minutes restriction.'}


class Client:
    def __init__(self):
        self.key=os.environ.get('BALLDONTLIE_API_KEY','').strip()
        if len(self.key)<8 or re.search(r'\s',self.key):
            raise StatisticsError('Set BALLDONTLIE_API_KEY in Render Environment; do not paste it in chat')
        self.opener=build_opener(NoRedirect())

    def request(self,endpoint,params,directory,artifacts):
        if endpoint not in ENDPOINTS:
            raise StatisticsError('Unrecognized provider endpoint')
        url=BASE+endpoint+('?' + urlencode(params,doseq=True) if params else '')
        request=Request(url,headers={'Authorization':self.key,'Accept':'application/json','User-Agent':'NBAResearchPrototype/0.1'})
        try:
            with self.opener.open(request,timeout=15) as response:
                if response.geturl()!=url:
                    raise StatisticsError('Unexpected response destination')
                body=response.read(MAX_BODY+1)
                if len(body)>MAX_BODY:
                    raise StatisticsError('Response exceeds diagnostic size limit')
                if self.key.encode() in body:
                    raise StatisticsError('Response contains sensitive authentication material; not retained')
                metadata={'url':url,'observed_at_this_check':iso(now()),'sha256':hashlib.sha256(body).hexdigest(),
                          'byte_count':len(body),'http_date':response.headers.get('Date'),
                          'http_last_modified':response.headers.get('Last-Modified'),
                          'cache_age_header':response.headers.get('Age')}
        except HTTPError as error:
            raise StatisticsError('HTTP error',error.code) from None
        except URLError as error:
            raise StatisticsError(type(error.reason).__name__) from None
        except TimeoutError:
            raise StatisticsError('Timeout') from None
        archive(body,metadata,directory,artifacts)
        try:
            data=json.loads(body)
            if not isinstance(data,dict) or not isinstance(data.get('data'),list):
                raise ValueError('Unexpected response envelope')
            return data
        except (ValueError,TypeError):
            raise StatisticsError('Response JSON schema is unsupported') from None


def normalize(data,endpoint,params):
    parser={'teams':team,'players/active':player,'games':game,'stats':statistic,'player_injuries':injury}[endpoint]
    rows=[parser(row) for row in data['data']]
    ids=[r['player']['id'] if endpoint=='player_injuries' else r['id'] for r in rows]
    if len(ids)!=len(set(ids)):
        raise ValueError('Duplicate provider identities within the sample')
    if endpoint=='games' and any(r['source_calendar_date'] not in params['dates[]'] for r in rows):
        raise ValueError('Returned games do not match the requested dates')
    if endpoint=='stats':
        for row in rows:
            event=row['game']
            if event['season']!=params['seasons[]'][0] or not params['start_date']<=event['source_calendar_date']<=params['end_date'] or event['postseason']:
                raise ValueError('Historical sample does not match its requested season/date window')
    meta=data.get('meta') or {}
    cursor=meta.get('next_cursor')
    if cursor is not None:
        identifier(cursor)
    # Missing pagination metadata is never proof that a roster/history is complete.
    pagination_known=endpoint=='teams' or isinstance(data.get('meta'),dict) and 'next_cursor' in data['meta']
    return {'status':'ok' if rows else 'empty','row_count':len(rows),'sample_rows':rows[:5],
            'provider_namespace':'balldontlie','pagination_metadata_present':pagination_known,
            'more_pages':cursor is not None if pagination_known else None,
            'pagination_exhausted':cursor is None if pagination_known else None,
            'next_cursor':cursor,'full_roster_or_history_imported':False,
            'final_stats_with_required_fields':sum(r['game']['final'] and r['baseline_fields_complete'] for r in rows) if endpoint=='stats' else None,
            'source_update_time':None,
            'note':'One bounded response page only. Observation time is not a source-update timestamp or latency measurement. IDs belong to BALLDONTLIE, not NBA or Odds API.'}


def check(path):
    try:
        client=Client()
    except StatisticsError as error:
        return {'status':'configuration_required','reason':error.kind,'odds_api_requests':0,'statistics_api_requests':0}
    store=Store(path)
    try:
        at=now()
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            previous=store.db.execute('SELECT started FROM stats_check_attempts ORDER BY id DESC LIMIT 1').fetchone()
            if previous and dt(previous['started'])>at-timedelta(minutes=5):
                return {'status':'deferred','reason':'A statistics check was attempted within the past five minutes','statistics_api_requests':0,'odds_api_requests':0}
            space=storage_status(store.path)
            if not space['collection_allowed'] or space['free_bytes']<space['required_free_bytes']+5*MAX_BODY:
                return {'status':'deferred','reason':'Insufficient disk space for source artifacts and a backup','statistics_api_requests':0,'odds_api_requests':0}
            attempt=store.db.execute('INSERT INTO stats_check_attempts(started) VALUES (?)',(iso(at),)).lastrowid
        day=at.astimezone(ZoneInfo('America/New_York')).date()
        season=season_start(at)-1
        jobs=[('teams','teams',{}),('active_players','players/active',{'per_page':100}),
              ('games','games',{'dates[]':[day.isoformat(),(day+timedelta(days=1)).isoformat()],'per_page':100}),
              ('historical_player_stats','stats',{'seasons[]':[season],'start_date':f'{season+1}-02-01',
                                                'end_date':f'{season+1}-06-30','postseason':'false','per_page':5}),
              ('player_injuries','player_injuries',{'per_page':100})]
        results={}
        requests=0
        halted=False
        for name,endpoint,params in jobs:
            if halted:
                results[name]={'status':'not_checked','reason':'Stopped after authentication or rate-limit response; no retry'}
                continue
            artifacts=[]
            requests+=1
            try:
                response=client.request(endpoint,params,store.path.parent/'source-artifacts',artifacts)
                result=normalize(response,endpoint,params)
            except Exception as error:
                status=getattr(error,'http_status',None)
                result={'status':'error','error_type':type(error).__name__,'http_status':status,
                        'reason':error.kind if isinstance(error,StatisticsError) else 'Provider data schema or processing failed; no replacement data supplied'}
                halted=status in (401,429)
            result['requested_parameters']=params
            result['artifacts']=artifacts
            for artifact in artifacts:
                key=identity([artifact['url'],artifact['sha256']])
                with store.db:
                    store.db.execute('INSERT OR IGNORE INTO source_artifacts VALUES (?,?,?,?)',
                                     (key,artifact['url'],artifact['sha256'],artifact['observed_at_this_check']))
                artifact['first_observed_at']=store.db.execute('SELECT first_observed FROM source_artifacts WHERE id=?',(key,)).fetchone()[0]
            results[name]=result
        payload={'status':'completed','provider':'BALLDONTLIE NBA','provider_namespace':'balldontlie',
                 'processed_at':iso(now()),'requested_dates_eastern':[day.isoformat(),(day+timedelta(days=1)).isoformat()],
                 'historical_season_start_year':season,'sources':results,'statistics_api_requests':requests,'odds_api_requests':0,
                 'note':'Manual five-request access diagnostic only. Read each endpoint status. No automatic stats polling, full historical import, cross-provider identity match, predictions, settlement or trial activation. Provider update latency remains unverified.'}
        with store.db:
            store.db.execute('INSERT INTO stats_checks VALUES (?,?)',(attempt,json.dumps(payload,sort_keys=True,allow_nan=False)))
        return payload
    finally:
        store.close()
