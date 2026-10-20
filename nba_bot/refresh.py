"""Opt-in faster polling; verifies reported quota before increasing request rate."""
import math
import os
import shutil
from datetime import timedelta

from .core import dt


def policy(store, at, region='us'):
    requested=os.environ.get('ODDS_REFRESH_PROFILE', 'development').strip().lower()
    if requested not in {'development','fast'}:
        raise ValueError('ODDS_REFRESH_PROFILE must be development or fast')
    latest=store.db.execute('SELECT * FROM provider_usage ORDER BY attempt DESC LIMIT 1').fetchone()
    capacity=(latest['remaining']+latest['used'] if latest and latest['remaining'] is not None and latest['used'] is not None else None)
    expected_cost=max(2*len(region.split(',')), latest['last_cost'] or 0) if latest else 2*len(region.split(','))
    # Worst-case 31-day, 60-second polling budget, plus a reserve. Not a pricing quote.
    required_capacity=max(100000, math.ceil(31*86400/60)*expected_cost+1000)
    fast=requested=='fast' and capacity is not None and capacity>=required_capacity
    interval=21600
    if fast:
        _,quotes=store.latest_quotes(False)
        starts=[(dt(q['start'])-at).total_seconds() for q in quotes if dt(q['start'])>at]
        interval=60 if starts else 1800
    return {'requested':requested, 'effective':'fast' if fast else 'development',
            'interval_seconds':interval, 'retry_seconds':60 if fast else 1800,
            'max_attempts_day':1440 if fast else 6, 'reserve':1000 if fast else 100,
            'reported_capacity':capacity, 'required_fast_capacity':required_capacity,
            'needs_quota_confirmation':requested=='fast' and not fast,
            'note':('Fast mode targets 60-second polling when upcoming NBA games are present; otherwise 30 minutes. Provider advertises 60-second pre-match updates; end-to-end delivery latency remains unmeasured.' if fast else
                    'Six-hour development snapshots. Fast mode requires sufficient provider-reported capacity.' if requested=='fast' else
                    'Six-hour development snapshots are too infrequent for live paper selections')}


def storage_status(path):
    """Leave room for another database-sized backup and headroom before collecting more."""
    usage=shutil.disk_usage(path.parent)
    size=sum(candidate.stat().st_size for candidate in (path, path.with_name(path.name+'-wal')) if candidate.exists())
    required=max(64*1024*1024, size+64*1024*1024)
    return {'free_bytes':usage.free, 'database_bytes_including_wal':size,
            'required_free_bytes':required, 'collection_allowed':usage.free>=required,
            'note':'Local backups share the disk; offsite backups and capacity monitoring are still required'}


def timestamp_ages(quotes):
    ages=sorted((dt(q['collected'])-dt(q['quoted'])).total_seconds() for q in quotes)
    if not ages:
        return {'quotes':0,'median_seconds':None,'p95_seconds':None}
    return {'quotes':len(ages),'median_seconds':ages[len(ages)//2],
            'p95_seconds':ages[max(0,math.ceil(len(ages)*.95)-1)],
            'note':'Age of the provider quote timestamp at collection; not a measurement of sportsbook-to-provider delivery latency'}
