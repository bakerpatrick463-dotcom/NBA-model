"""Versioned, resumable provider snapshots. No forecasts or trial activation."""
from contextlib import contextmanager
from datetime import timedelta
import fcntl
import json
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

from .core import dt,identity,iso,now
from .current_data import season_start
from .refresh import storage_status
from .statistics_provider import Client,MAX_BODY,StatisticsError,normalize,reserve_request
from .store import Store

ENDPOINTS={'teams':'teams','roster':'players/active','games':'games','history-games':'games','history':'stats'}


@contextmanager
def import_lock(path):
    handle=Path(str(path)+'.statistics-import.lock').open('a')
    try:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def progress(store,run):
    last=store.db.execute('SELECT * FROM stats_import_pages WHERE run=? ORDER BY page DESC LIMIT 1',(run['id'],)).fetchone()
    count=store.db.execute('SELECT COALESCE(SUM(row_count),0) FROM stats_import_pages WHERE run=?',(run['id'],)).fetchone()[0]
    event=store.db.execute('SELECT recorded,payload FROM stats_import_events WHERE run=? ORDER BY seq DESC LIMIT 1',(run['id'],)).fetchone()
    event_payload=json.loads(event['payload']) if event else None
    return {'run_id':run['id'],'kind':run['kind'],'created':run['created'],'parameters':json.loads(run['params']),
            'pages':last['page'] if last else 0,'rows':count,'next_cursor':last['next_cursor'] if last else None,
            'pagination_finished':bool(last and last['terminal']),
            'terminal_basis':json.loads(last['payload']).get('terminal_basis') if last and last['terminal'] else None,
            'coverage_reconciled':False,'latest_event':event_payload if event_payload and event_payload.get('page',1)>(last['page'] if last else 0) else None}


def latest_runs(store):
    runs=store.db.execute('SELECT r.* FROM stats_import_runs r WHERE rowid=(SELECT MAX(rowid) FROM stats_import_runs WHERE kind=r.kind) ORDER BY kind').fetchall()
    return [progress(store,run) for run in runs]


def specifications(at):
    day=at.astimezone(ZoneInfo('America/New_York')).date()
    season=season_start(at)-1
    return {'teams':{},'roster':{'per_page':100},
            'games':{'dates[]':[(day+timedelta(days=i)).isoformat() for i in range(-2,3)],'per_page':100},
            'history-games':{'seasons[]':[season],'start_date':f'{season+1}-02-01','end_date':f'{season+1}-06-30',
                             'postseason':'false','per_page':100},
            'history':{'seasons[]':[season],'start_date':f'{season+1}-02-01','end_date':f'{season+1}-06-30',
                       'postseason':'false','per_page':100}}


def choose_run(store,kind,params,refresh):
    encoded=json.dumps(params,sort_keys=True)
    previous=store.db.execute('SELECT * FROM stats_import_runs WHERE kind=? AND params=? ORDER BY rowid DESC LIMIT 1',(kind,encoded)).fetchone()
    if previous and not refresh and (kind in ('history','history-games') or now()-dt(previous['created'])<timedelta(hours=24)):
        return previous
    with store.db:
        run_id=identity([kind,params,iso(now()),uuid.uuid4().hex])
        store.db.execute('INSERT INTO stats_import_runs VALUES (?,?,?,?)',(run_id,kind,iso(now()),encoded))
    return store.db.execute('SELECT * FROM stats_import_runs WHERE id=?',(run_id,)).fetchone()


def record_event(store,run,payload):
    with store.db:
        store.db.execute('INSERT INTO stats_import_events(run,recorded,payload) VALUES (?,?,?)',
                         (run['id'],iso(now()),json.dumps(payload,sort_keys=True)))


def retain_artifacts(store,artifacts):
    for artifact in artifacts:
        key=identity([artifact['url'],artifact['sha256']])
        with store.db:
            store.db.execute('INSERT OR IGNORE INTO source_artifacts VALUES (?,?,?,?)',
                             (key,artifact['url'],artifact['sha256'],artifact['observed_at_this_check']))
        artifact['first_observed_at']=store.db.execute('SELECT first_observed FROM source_artifacts WHERE id=?',(key,)).fetchone()[0]


def save_page(store,run,response,artifacts):
    state=progress(store,run)
    if state['pagination_finished']:
        raise ValueError('Cannot append to a finished provider snapshot')
    params=json.loads(run['params'])
    endpoint=ENDPOINTS[run['kind']]
    parsed=normalize(response,endpoint,params,include_rows=True)
    rows=parsed.pop('normalized_rows')
    limit=params.get('per_page')
    if limit and len(rows)>limit:
        raise ValueError('Provider ignored page-size bound')
    cursor=parsed['next_cursor']
    if cursor is not None:
        if not rows or state['next_cursor'] is not None and int(cursor)<=int(state['next_cursor']):
            raise ValueError('Pagination cursor did not advance')
        terminal=False; basis=None
    elif endpoint=='teams':
        terminal=True; basis='nonpaginated_teams_endpoint'
    elif parsed['pagination_metadata_present']:
        terminal=True; basis='explicit_null_next_cursor'
    elif len(rows)<limit:
        terminal=True; basis='short_page_without_next_cursor'
    else:
        raise ValueError('Full response page omitted its continuation cursor')
    # Reject duplicates across pages; do not silently drop/rewrite a player or
    # revised statistic from a moving provider snapshot.
    payload={**parsed,'requested_cursor':state['next_cursor'],'artifacts':artifacts,
             'sample_rows':parsed['sample_rows'],'terminal_basis':basis}
    with store.db:
        store.db.executemany('INSERT INTO stats_import_objects VALUES (?,?,?,?)',
                             [(run['id'],row['id'],identity([row['game']['id'],row['player']['id']]) if run['kind']=='history' else row['id'],
                               json.dumps(row,sort_keys=True,allow_nan=False)) for row in rows])
        store.db.execute('INSERT INTO stats_import_pages VALUES (?,?,?,?,?,?,?)',
                         (run['id'],state['pages']+1,iso(now()),str(cursor) if cursor is not None else None,
                          int(terminal),len(rows),json.dumps(payload,sort_keys=True,allow_nan=False)))
    return progress(store,run)


def import_data(path,scope='all',max_pages=300,refresh=False,notify=None):
    if scope not in ('all',*ENDPOINTS) or not isinstance(max_pages,int) or not 1<=max_pages<=500:
        raise ValueError('Choose a known import scope and a page limit from 1 to 500')
    try:
        client=Client()
    except StatisticsError as error:
        return {'status':'configuration_required','reason':error.kind,'odds_api_requests':0}
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with import_lock(path):
            return _import(path,client,scope,max_pages,refresh,notify)
    except BlockingIOError:
        return {'status':'deferred','reason':'A statistics import is already running for this database','odds_api_requests':0}


def _import(path,client,scope,max_pages,refresh,notify):
    store=Store(path)
    requests=0
    status='pagination_finished'
    try:
        specs=specifications(now())
        for kind,params in specs.items():
            if scope!='all' and scope!=kind:
                continue
            run=choose_run(store,kind,params,refresh)
            state=progress(store,run)
            if notify: notify(f'{kind}: {state["rows"]} rows saved; {state["pages"]} pages; pagination finished: {state["pagination_finished"]}')
            while not state['pagination_finished']:
                if requests>=max_pages:
                    status='paused_page_limit'; break
                recent=store.db.execute('SELECT recorded,payload FROM stats_import_events WHERE run=? ORDER BY seq DESC LIMIT 1',(run['id'],)).fetchone()
                if recent and json.loads(recent['payload']).get('http_status')==429 and now()-dt(recent['recorded'])<timedelta(minutes=1):
                    status='paused_rate_limit'; break
                space=storage_status(path)
                if not space['collection_allowed'] or space['free_bytes']<space['required_free_bytes']+2*MAX_BODY:
                    status='paused_storage'; break
                wait_until=reserve_request(store,ENDPOINTS[kind],now())
                if wait_until is not None:
                    if notify: notify('Statistics request budget reached; waiting before the next saved page.')
                    time.sleep(min(5,max(.1,(wait_until-now()).total_seconds())))
                    continue
                request_params={**params}
                if state['next_cursor'] is not None:
                    request_params['cursor']=state['next_cursor']
                artifacts=[]
                requests+=1
                try:
                    response=client.request(ENDPOINTS[kind],request_params,path.parent/'source-artifacts',artifacts)
                    retain_artifacts(store,artifacts)
                    state=save_page(store,run,response,artifacts)
                except Exception as error:
                    retain_artifacts(store,artifacts)
                    detail={'status':'error','error_type':type(error).__name__,
                            'page':state['pages']+1,
                            'http_status':getattr(error,'http_status',None),
                            'reason':error.kind if isinstance(error,StatisticsError) else 'Page schema, identity or persistence failed; completed pages were preserved'}
                    record_event(store,run,detail)
                    return {'status':'paused_error','error':detail,'imports':latest_runs(store),
                            'statistics_api_requests':requests,'odds_api_requests':0}
                if notify: notify(f'{kind}: saved page {state["pages"]}; {state["rows"]} rows; pagination finished: {state["pagination_finished"]}')
                if not state['pagination_finished']:
                    time.sleep(2.1)
            if status!='pagination_finished':
                break
        from .statistics_identity import identify
        matches=identify(store)
        return {'status':status,'imports':latest_runs(store),'identity_check':matches,
                'statistics_api_requests':requests,'odds_api_requests':0,
                'note':'Provider pages are retained, not proof of complete coverage or source freshness. Historical window is explicit. No model, selection, settlement or live trial has been enabled.'}
    finally:
        store.close()
