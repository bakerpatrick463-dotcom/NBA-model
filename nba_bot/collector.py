"""Persistent scheduling for the initial 500-credit development allowance."""
import fcntl
import logging
from datetime import timedelta
from pathlib import Path

from .core import dt, iso, now
from .providers import OddsAPI, mock_candidates
from .store import Store
from .refresh import policy, storage_status

LOG = logging.getLogger("nba_bot")
LIVE_INTERVAL = 6 * 3600
RETRY_INTERVAL = 1800
MAX_ATTEMPTS_DAY = 6
CREDIT_RESERVE = 100


def next_month(at):
    return at.replace(year=at.year + (at.month == 12), month=at.month % 12 + 1,
                      day=1, hour=0, minute=0, second=0, microsecond=0)


def schedule(store, at, region="us"):
    """Return next eligible request time and why; must run inside reservation transaction."""
    refresh=policy(store,at,region)
    storage=storage_status(store.path)
    if not storage["collection_allowed"]:
        return at+timedelta(hours=1), "Paused: insufficient space for collection and a backup"
    attempts = store.db.execute("SELECT * FROM collection_attempts ORDER BY id").fetchall()
    rolling = [r for r in attempts if dt(r["started"]) > at-timedelta(days=1)]
    if len(rolling) >= refresh["max_attempts_day"]:
        return dt(rolling[0]["started"])+timedelta(days=1), "Daily request-attempt limit"
    usage = store.db.execute("SELECT * FROM provider_usage ORDER BY attempt DESC LIMIT 1").fetchone()
    if usage and dt(usage["recorded"]).strftime("%Y-%m") == at.strftime("%Y-%m"):
        if usage["remaining"] is None:
            return next_month(at), "Paused: provider did not report remaining credits"
        unconfirmed_cost = sum(r["estimated_cost"] for r in attempts if r["id"] > usage["attempt"])
        cost = max(2*len(region.split(",")), usage["last_cost"] or 0)
        if usage["remaining"] - unconfirmed_cost - cost < refresh["reserve"]:
            return next_month(at), f"Paused: protecting {refresh['reserve']}-credit reserve"
    if attempts:
        latest = attempts[-1]
        # Confirmed responses (including empty schedules) use the six-hour interval.
        complete = store.db.execute("SELECT 1 FROM provider_usage WHERE attempt=?", (latest["id"],)).fetchone()
        interval = refresh["interval_seconds"] if complete else refresh["retry_seconds"]
        due = dt(latest["started"])+timedelta(seconds=interval)
        if due > at:
            return due, ("Scheduled six-hour refresh" if refresh["effective"]=="development" else f"Adaptive refresh ({interval}s)") if complete else "Retry backoff"
    return at, "Ready to collect"


def reserve(store, at, region):
    regions = region.split(",")
    if any(r not in {"us", "us2", "uk", "eu", "au"} for r in regions) or len(set(regions)) != len(regions):
        raise ValueError("Use supported nonduplicate provider regions")
    # Reserving before the request makes retries and restarts count toward the limit.
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        due, reason = schedule(store, at, region)
        if due > at:
            return None, due, reason
        row = store.db.execute("INSERT INTO collection_attempts(started,region,estimated_cost) VALUES (?,?,?)",
                               (iso(at), region, 2*len(regions)))
        return row.lastrowid, due, reason


def cycle(path, live=False, region="us"):
    store = Store(path)
    provider = None
    attempt = None
    try:
        at = now()
        if live:
            # Missing configuration must not consume a reservation or be logged as a success.
            provider = OddsAPI(region)
            attempt, due, reason = reserve(store, at, region)
            if attempt is None:
                LOG.info("Collection deferred: %s; next eligible %s", reason, iso(due))
                return False
            quotes = provider.collect(at)
            store.collect(quotes, simulated=False)
            store.operation("live", True, f"Collected {len(quotes)} quotes; live forecasting disabled")
        else:
            candidates = mock_candidates(at)
            store.research(candidates, at, {"demo_book_a", "demo_book_b"})
            store.operation("mock", True, f"Processed {len(candidates)} SIMULATED quotes")
        LOG.info("Collection completed in %s mode", "live" if live else "SIMULATED")
        return True
    except Exception as error:
        store.operation("live" if live else "mock", False, f"Cycle failed ({type(error).__name__}); no replacement data used")
        raise
    finally:
        if provider is not None and attempt is not None and provider.usage is not None:
            with store.db:
                store.db.execute("INSERT INTO provider_usage VALUES (?,?,?,?,?)",
                                 (attempt, iso(now()), provider.usage["remaining"], provider.usage["used"], provider.usage["last_cost"]))
        store.close()


def collection_status(store, simulated=False, at=None):
    at = at or now()
    mode = "mock" if simulated else "live"
    op = store.db.execute("SELECT * FROM operations WHERE mode=? ORDER BY seq DESC LIMIT 1", (mode,)).fetchone()
    if simulated:
        due, reason = (dt(op["recorded"])+timedelta(minutes=15), "Mock refresh") if op else (at, "No collection yet")
    else:
        due, reason = schedule(store, at)
    usage = store.db.execute("SELECT remaining,used,last_cost,recorded FROM provider_usage ORDER BY attempt DESC LIMIT 1").fetchone()
    refresh=policy(store,at)
    return {"mode": mode, "refresh_profile":refresh, "storage":storage_status(store.path), "next_eligible_collection": iso(due), "schedule_status": reason,
            "interval_hours": None if simulated else refresh["interval_seconds"]/3600,
            "provider_credits": dict(usage) if usage and not simulated else None,
            "last_operation": dict(op) if op else None,
            "collector_on_schedule": bool(op and op["ok"] and not reason.startswith("Paused:")
                                          and at-dt(op["recorded"]) <= timedelta(seconds=refresh["interval_seconds"] if not simulated else 900)+timedelta(minutes=5)),
            "note": refresh["note"] if not simulated else "Simulated fixtures only"}


def daily_backup(path):
    directory = Path(path).parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"auto-{now().date()}.sqlite"
    if not destination.exists():
        # A crash cannot leave a partially written file marked as the daily backup.
        temporary = directory / f".auto-{now().date()}.partial"
        if temporary.exists():
            temporary.unlink()
        store = Store(path)
        try:
            store.backup(temporary)
            temporary.replace(destination)
        finally:
            store.close()
    # Retain only this feature's daily backups; never remove manual backups.
    managed = sorted(p for p in directory.glob("auto-????-??-??.sqlite") if len(p.name) == 22)
    for old in managed[:-7]:
        old.unlink()


def acquire_lock(path):
    target = Path(str(path)+".collector.lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("Another collector already owns this database") from None
    return handle


def run_worker(path, live, region, stop):
    LOG.info("Scheduled collector started: %s", "live, quota-aware refresh profile" if live else "SIMULATED")
    while not stop.is_set():
        failed = False
        try:
            cycle(path, live, region)
        except Exception as error:
            failed = True
            LOG.error("Collector cycle failed (%s); retry is scheduled", type(error).__name__)
        try:
            daily_backup(path)
        except Exception as error:
            store = Store(path)
            try:
                store.operation("backup", False, f"Backup failed ({type(error).__name__})")
            finally:
                store.close()
            LOG.error("Backup failed (%s)", type(error).__name__)
        if live:
            store = Store(path)
            try:
                due, _ = schedule(store, now(), region)
                refresh=policy(store,now(),region)
            finally:
                store.close()
            delay = max(refresh["retry_seconds"] if failed else 1, min(900, (due-now()).total_seconds()))
        else:
            delay = 900
        stop.wait(delay)
