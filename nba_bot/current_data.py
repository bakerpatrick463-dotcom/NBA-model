"""Bounded public NBA source diagnostics; no Odds API calls or automatic forecasts."""
from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, unquote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .core import dt, identity, iso, now
from .refresh import storage_status
from .store import Store

SCOREBOARD = 'https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json'
NBA_STATS = 'https://stats.nba.com/stats/leaguegamelog'
ALLOWED_HOSTS = {'cdn.nba.com','stats.nba.com','official.nba.com','ak-static.cms.nba.com'}
MAX_BODY = 24*1024*1024


class SourceError(RuntimeError):
    def __init__(self, kind, http_status=None):
        self.kind, self.http_status = kind, http_status
        super().__init__(kind)


def season_start(at):
    local=at.astimezone(ZoneInfo('America/New_York'))
    return local.year if local.month>=7 else local.year-1


def allowed_url(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
        raise SourceError('Unexpected source destination')
    return url


def fetch(url, directory, artifacts=None):
    allowed_url(url)
    try:
        request=Request(url,headers={'User-Agent':'NBAResearchPrototype/0.1','Accept':'application/json,text/html,application/pdf'})
        with urlopen(request,timeout=15) as response:
            allowed_url(response.geturl())
            body=response.read(MAX_BODY+1)
            if len(body)>MAX_BODY:
                raise SourceError('Response exceeds diagnostic size limit')
            metadata={'url':url,'observed_at_this_check':iso(now()),'sha256':hashlib.sha256(body).hexdigest(),
                      'byte_count':len(body),'http_date':response.headers.get('Date'),
                      'http_last_modified':response.headers.get('Last-Modified'),
                      'cache_age_header':response.headers.get('Age')}
    except HTTPError as error:
        raise SourceError('HTTP error',error.code) from None
    except URLError as error:
        raise SourceError(type(error.reason).__name__) from None
    except TimeoutError:
        raise SourceError('Timeout') from None
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    destination=directory/(metadata['sha256']+'.source')
    if destination.is_symlink():
        raise SourceError('Artifact path is a symlink')
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=metadata['sha256']:
            raise SourceError('Existing artifact integrity mismatch')
    else:
        # Exclusive creation preserves any existing local work and content identity.
        try:
            with destination.open('xb') as output:
                output.write(body)
        except FileExistsError:
            if hashlib.sha256(destination.read_bytes()).hexdigest()!=metadata['sha256']:
                raise SourceError('Concurrent artifact integrity mismatch')
    metadata['artifact_path']=str(destination)
    if artifacts is not None:
        artifacts.append(metadata)
    return body,metadata


def integer(value):
    if value is None:
        return None
    if isinstance(value,bool):
        raise ValueError('Boolean is not a statistic')
    result=float(value)
    if not math.isfinite(result) or result<0 or not result.is_integer():
        raise ValueError('Invalid count statistic')
    return int(result)


def minutes(value):
    if value is None or value=='':
        return None
    match=re.fullmatch(r'PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?',str(value))
    if not match or not any(match.groups()):
        raise ValueError('Unknown minutes format')
    hours,mins,secs=(float(part or 0) for part in match.groups())
    return hours*60+mins+secs/60


def parse_scoreboard(data):
    board=data['scoreboard']
    if str(board['leagueId'])!='00':
        raise ValueError('Unexpected league')
    game_date=date.fromisoformat(board['gameDate'])
    games=[]
    for game in board['games']:
        if not re.fullmatch(r'\d{10}',game['gameId']):
            raise ValueError('Invalid NBA game identifier')
        home,away=game['homeTeam'],game['awayTeam']
        if not home.get('teamId') or not away.get('teamId') or home['teamId']==away['teamId']:
            raise ValueError('Invalid team matching')
        games.append({'id':game['gameId'],'start':iso(game['gameTimeUTC']),
                      'home_team_id':str(home['teamId']),'away_team_id':str(away['teamId']),
                      'home':str(home['teamCity'])+' '+str(home['teamName']),
                      'away':str(away['teamCity'])+' '+str(away['teamName']),
                      'home_score':integer(home.get('score')),'away_score':integer(away.get('score')),
                      'status_code':integer(game.get('gameStatus')),'status_text':game.get('gameStatusText')})
    return game_date,games


def parse_boxscore(data, expected):
    game=data['game']
    if game['gameId']!=expected['id']:
        raise ValueError('Mismatched boxscore event')
    players=[]
    teams=[]
    for side in ('home','away'):
        team=game[side+'Team']
        if str(team['teamId'])!=expected[side+'_team_id']:
            raise ValueError('Mismatched boxscore team')
        if len({p['personId'] for p in team.get('players',[])})!=len(team.get('players',[])):
            raise ValueError('Duplicate player identity')
        for player in team.get('players',[]):
            if not player.get('personId') or not player.get('name'):
                raise ValueError('Missing player identity')
            stats=player.get('statistics') or {}
            row={'player_id':str(player['personId']),'name':player['name'],'team_id':str(team['teamId']),
                 'source_status':player.get('status'),'played_flag':player.get('played'),
                 'starter_flag':player.get('starter'),'minutes':minutes(stats.get('minutes'))}
            fields={'points':'points','rebounds':'reboundsTotal','assists':'assists','threes':'threePointersMade',
                    'field_goal_attempts':'fieldGoalsAttempted','three_point_attempts':'threePointersAttempted',
                    'free_throw_attempts':'freeThrowsAttempted','turnovers':'turnovers','personal_fouls':'foulsPersonal'}
            row.update({name:integer(stats.get(key)) for name,key in fields.items()})
            players.append(row)
        team_rows=[p for p in players if p['team_id']==str(team['teamId'])]
        known_minutes=[p['minutes'] for p in team_rows if p['minutes'] is not None]
        period=integer(game.get('period'))
        expected_minutes=240+25*max(0,(period or 4)-4) if game.get('gameStatus')==3 else None
        total_minutes=sum(known_minutes) if known_minutes else None
        teams.append({'team_id':str(team['teamId']),'reported_score':integer(team.get('score')),
                      'sum_known_player_minutes':total_minutes,'expected_final_minutes':expected_minutes,
                      'final_minutes_within_rounding_tolerance':abs(total_minutes-expected_minutes)<=1 if total_minutes is not None and expected_minutes is not None else None})
    return {'game_id':game['gameId'],'source_game_status':game.get('gameStatus'),
            'final':game.get('gameStatus')==3,'players':players,'teams':teams,
            'note':'In-progress statistics are not final settlement data; missing fields remain unknown. No injury cause is inferred from DNP status.'}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        if tag=='a':
            href=dict(attrs).get('href')
            if href:
                self.hrefs.append(href)


def latest_injury_link(html, index_url):
    parser=Links()
    parser.feed(html)
    candidates=[]
    for href in parser.hrefs:
        url=urljoin(index_url,href)
        try:
            allowed_url(url)
        except (SourceError,ValueError):
            continue
        filename=unquote(urlparse(url).path.rsplit('/',1)[-1])
        match=re.fullmatch(r'Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf',filename,re.IGNORECASE)
        if not match:
            continue
        day,hour,minute,half=match.groups()
        try:
            stamp=datetime.strptime(f'{day} {hour}:{minute}{half.upper()}','%Y-%m-%d %I:%M%p')
        except ValueError:
            continue
        candidates.append((stamp,url))
    if not candidates:
        raise ValueError('No recognized official injury-report PDF link found')
    stamp,url=max(candidates)
    return url,stamp.strftime('%Y-%m-%d %I:%M%p')


def json_body(body):
    data=json.loads(body)
    if not isinstance(data,dict):
        raise ValueError('Unexpected JSON schema')
    return data


def scoreboard_check(directory, at, artifacts=None):
    artifacts=[] if artifacts is None else artifacts
    body,metadata=fetch(SCOREBOARD,directory,artifacts)
    data=json_body(body)
    day,games=parse_scoreboard(data)
    result={'status':'ok','game_date':day.isoformat(),'date_matches_today_eastern':day==at.astimezone(ZoneInfo('America/New_York')).date(),
            'source_update_time_raw':data.get('meta',{}).get('time'),'games':games,'artifacts':artifacts}
    eligible=[g for g in games if g['status_code'] in (2,3)]
    if eligible:
        chosen=max(eligible,key=lambda g:(g['status_code'],g['start']))
        url='https://cdn.nba.com/static/json/liveData/boxscore/boxscore_'+chosen['id']+'.json'
        try:
            body,metadata=fetch(url,directory,artifacts)
            result['boxscore']={'status':'ok',**parse_boxscore(json_body(body),chosen)}
        except Exception as error:
            result['boxscore']=failure(error)
    else:
        result['boxscore']={'status':'not_checked','reason':'No live or final game in the returned scoreboard; pregame zeroes are not treated as played statistics'}
    return result


def history_check(directory, at, artifacts=None):
    artifacts=[] if artifacts is None else artifacts
    start=season_start(at)-1
    season=f'{start}-{str(start+1)[-2:]}'
    params={'Counter':0,'Direction':'DESC','LeagueID':'00','PlayerOrTeam':'P','Season':season,
            'SeasonType':'Regular Season','Sorter':'DATE','DateFrom':f'{start+1}-02-01','DateTo':f'{start+1}-06-30'}
    url=NBA_STATS+'?'+urlencode(params)
    body,metadata=fetch(url,directory,artifacts)
    data=json_body(body)
    sets=data.get('resultSets',[])
    required={'PLAYER_ID','PLAYER_NAME','GAME_ID','GAME_DATE','MIN','PTS','REB','AST','FG3M'}
    table=next((t for t in sets if required.issubset(t.get('headers',[]))),None)
    if table is None:
        raise ValueError('NBA player game-log schema missing required fields')
    rows=[]
    for row in table['rowSet']:
        if len(row)!=len(table['headers']):
            raise ValueError('Mismatched game-log columns')
        item=dict(zip(table['headers'],row))
        for key in ('PTS','REB','AST','FG3M'):
            integer(item[key])
        if not item['PLAYER_ID'] or not re.fullmatch(r'\d{10}',str(item['GAME_ID'])):
            raise ValueError('Missing game-log identity')
        if item['MIN'] is not None:
            parsed_minutes=float(item['MIN'])
            if not math.isfinite(parsed_minutes) or parsed_minutes<0:
                raise ValueError('Invalid historical minutes')
        rows.append({key:item[key] for key in sorted(required)})
    return {'status':'ok' if rows else 'empty','season':season,'row_count':len(rows),
            'unique_games':len({r['GAME_ID'] for r in rows}),'unique_players':len({r['PLAYER_ID'] for r in rows}),
            'sample_rows':rows[:5],'artifacts':artifacts,
            'note':'Previous completed season, February onward, for source-access testing; revised historical observations are not an as-of backtest or current injury/role evidence.'}


def injury_check(directory, at, artifacts=None):
    artifacts=[] if artifacts is None else artifacts
    year=season_start(at)
    url=f'https://official.nba.com/nba-injury-report-{year}-{str(year+1)[-2:]}-season/'
    body,index_metadata=fetch(url,directory,artifacts)
    pdf_url,label=latest_injury_link(body.decode('utf-8',errors='replace'),url)
    body,pdf_metadata=fetch(pdf_url,directory,artifacts)
    if not body.lstrip().startswith(b'%PDF-'):
        raise ValueError('Injury report response is not a PDF')
    return {'status':'pdf_archived','source_url':pdf_url,'filename_timestamp_label':label,
            'publication_time_utc':None,'claims_extracted':False,'artifacts':artifacts,
            'note':'Filename time is preserved without assuming a timezone or actual publication instant. Injury text has not been parsed or mapped to players.'}


def failure(error):
    return {'status':'error','error_type':type(error).__name__,'http_status':getattr(error,'http_status',None),
            'reason':error.kind if isinstance(error,SourceError) else 'Source response schema/processing failed; no replacement data supplied'}


def run_source(job, directory, at):
    artifacts=[]
    try:
        result=job(directory,at,artifacts)
    except Exception as error:
        result=failure(error)
    result['artifacts']=artifacts
    return result


def check(path):
    store=Store(path)
    try:
        at=now()
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            recent=store.db.execute('SELECT started FROM source_check_attempts ORDER BY id DESC LIMIT 1').fetchone()
            if recent and dt(recent['started'])>at-timedelta(minutes=5):
                return {'status':'deferred','reason':'A public source check was attempted within the past five minutes'}
            space=storage_status(store.path)
            if not space['collection_allowed'] or space['free_bytes'] < space['required_free_bytes']+5*MAX_BODY:
                return {'status':'deferred','reason':'Insufficient disk space for source artifacts and a backup'}
            attempt=store.db.execute('INSERT INTO source_check_attempts(started) VALUES (?)',(iso(at),)).lastrowid
        directory=store.path.parent/'source-artifacts'
        jobs={'scoreboard_and_boxscore':scoreboard_check,'historical_player_stats':history_check,'official_injury_report':injury_check}
        results={}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures={name:pool.submit(run_source,job,directory,at) for name,job in jobs.items()}
            for name,future in futures.items():
                try:
                    results[name]=future.result()
                except Exception as error:
                    results[name]=failure(error)
        for result in results.values():
            for artifact in result.get('artifacts',[]):
                key=identity([artifact['url'],artifact['sha256']])
                with store.db:
                    store.db.execute('INSERT OR IGNORE INTO source_artifacts VALUES (?,?,?,?)',
                                     (key,artifact['url'],artifact['sha256'],artifact['observed_at_this_check']))
                artifact['first_observed_at']=store.db.execute('SELECT first_observed FROM source_artifacts WHERE id=?',(key,)).fetchone()[0]
        payload={'status':'completed','processed_at':iso(now()),'sources':results,'odds_api_requests':0,
                 'note':'Explicit source-access diagnostic only. No automatic stats polling, predictions, settlement or paper-trial activation. Public reachability does not establish commercial reuse rights or a latency SLA.'}
        with store.db:
            store.db.execute('INSERT INTO source_checks VALUES (?,?)',(attempt,json.dumps(payload,sort_keys=True,allow_nan=False)))
        return payload
    finally:
        store.close()
