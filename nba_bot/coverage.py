"""One explicit account/book/event coverage check, never continuous prop polling."""
import json
from dataclasses import asdict
from datetime import timedelta
from urllib.parse import quote as escape_path

from .core import dt, iso, now
from .providers import OddsAPI
from .refresh import policy, storage_status
from .store import Store

PROP_MARKETS = ('player_points','player_rebounds','player_assists','player_threes')


def reserve_check(store, at):
    """Count an explicit diagnostic toward the same account budget, without delaying bulk polls."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        settings=policy(store,at)
        if not storage_status(store.path)['collection_allowed']:
            return None,'Insufficient disk space'
        attempts=store.db.execute('SELECT * FROM collection_attempts ORDER BY id').fetchall()
        if sum(dt(a['started'])>at-timedelta(days=1) for a in attempts)>=settings['max_attempts_day']:
            return None,'Daily attempt limit reached; wait before checking again'
        latest=store.db.execute("SELECT a.started FROM collection_attempts a JOIN request_types t ON t.attempt=a.id WHERE t.kind='coverage' ORDER BY a.id DESC LIMIT 1").fetchone()
        if latest and dt(latest['started'])>at-timedelta(hours=1):
            return None,'A coverage request was already attempted in the past hour'
        usage=store.db.execute('SELECT * FROM provider_usage ORDER BY attempt DESC LIMIT 1').fetchone()
        if not usage or usage['remaining'] is None or dt(usage['recorded']).strftime('%Y-%m')!=at.strftime('%Y-%m'):
            return None,'Collect current-month bulk odds first so remaining quota can be checked'
        uncertain=sum(a['estimated_cost'] for a in attempts if a['id']>usage['attempt'])
        if usage['remaining']-uncertain-len(PROP_MARKETS)<settings['reserve']:
            return None,'The estimated four-credit check would cross the account reserve'
        attempt=store.db.execute('INSERT INTO collection_attempts(started,region,estimated_cost) VALUES (?,?,?)',
                                 (iso(at),'us',len(PROP_MARKETS))).lastrowid
        store.db.execute('INSERT INTO request_types VALUES (?,?)',(attempt,'coverage'))
        return attempt,None


def check(path, book='fanduel'):
    if book!='fanduel':
        raise ValueError('This diagnostic currently targets the user-listed FanDuel book')
    store=Store(path)
    provider=None
    attempt=None
    payload=None
    try:
        at=now()
        _,quotes=store.latest_quotes(False)
        events={q['event']:q for q in quotes if at<dt(q['start'])<=at+timedelta(days=2)}
        if not events:
            return {'status':'deferred','reason':'No upcoming game within 48 hours in stored live odds; collect a fresh schedule first'}
        selected=min(events.values(),key=lambda q:(q['start'],q['event']))
        provider=OddsAPI('us')
        attempt,reason=reserve_check(store,at)
        if attempt is None:
            return {'status':'deferred','reason':reason}
        payload={'status':'error','recorded':iso(at),'event':selected['event'],
                 'event_name':selected.get('event_name') or selected['event'], 'book':book,
                 'requested_markets':list(PROP_MARKETS),'estimated_credits':4,
                 'note':'One upcoming game only; an empty response does not prove global unavailability. No live selections enabled.'}
        try:
            event=provider.request('/sports/basketball_nba/events/'+escape_path(selected['event'],safe='')+'/odds',
                                   regions='us',bookmakers=book,markets=','.join(PROP_MARKETS),
                                   oddsFormat='decimal',dateFormat='iso')
            if event.get('id')!=selected['event']:
                raise ValueError('Mismatched provider event identity')
            # Normalize only the explicitly requested book and market set; never substitute another book.
            filtered={**event,'bookmakers':[
                {**b,'markets':[m for m in b.get('markets',[]) if m.get('key') in PROP_MARKETS]}
                for b in event.get('bookmakers',[]) if b.get('key')==book]}
            prices=provider.normalize([filtered],now(),'us')
            returned=sorted({m['key'] for b in filtered['bookmakers'] for m in b['markets'] if m.get('outcomes')})
            payload.update(status='prices_returned' if prices else 'no_prices_for_this_game',
                           returned_markets=returned,quote_count=len(prices),sample_quotes=[asdict(q) for q in prices[:12]])
        except Exception as error:
            payload.update(error_type=type(error).__name__,http_status=getattr(error,'status',None))
        payload['provider_usage']=provider.usage
        with store.db:
            store.db.execute('INSERT INTO coverage_checks VALUES (?,?)',(attempt,json.dumps(payload,sort_keys=True)))
        return payload
    finally:
        if provider is not None and attempt is not None and provider.usage is not None:
            with store.db:
                store.db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?)',
                                 (attempt,iso(now()),provider.usage['remaining'],provider.usage['used'],provider.usage['last_cost']))
        store.close()
