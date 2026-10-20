import argparse
import json
import logging
import signal
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .core import iso, now
from .providers import OddsAPI, mock_candidates
from .store import Store

LOG = logging.getLogger("nba_bot")


def cycle(path, live=False, region="us"):
    store = Store(path)
    try:
        at = now()
        if live:
            quotes = OddsAPI(region).collect(at)
            store.collect(quotes)
            store.operation("live", True, f"Collected {len(quotes)} quotes; live forecasting disabled")
        else:
            candidates = mock_candidates(at)
            store.research(candidates, at, {"demo_book_a", "demo_book_b"})
            store.operation("mock", True, f"Processed {len(candidates)} SIMULATED quotes")
        LOG.info("Collection completed in %s mode", "live" if live else "SIMULATED")
    except Exception as error:
        # Never log upstream exception text that may include an API-key URL.
        store.operation("live" if live else "mock", False, f"Cycle failed ({type(error).__name__}); no replacement data used")
        raise
    finally:
        store.close()


def polling_seconds(path, live=False):
    store = Store(path)
    try:
        starts = []
        from .core import dt
        for row in store.db.execute("SELECT payload FROM quotes"):
            q = json.loads(row[0])
            if q["simulated"] != (not live):
                continue
            delta = (dt(q["start"]) - now()).total_seconds()
            if delta > 0:
                starts.append(delta)
        # Conservative cost control; no promise of provider-frequency replication.
        return 60 if starts and min(starts) <= 3600 else 300 if starts and min(starts) <= 21600 else 900
    finally:
        store.close()


def worker(path, live, region):
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    failures = 0
    last_backup = None
    while not stop.is_set():
        try:
            cycle(path, live, region)
            failures = 0
            if last_backup is None or (now()-last_backup).total_seconds() >= 86400:
                store = Store(path)
                try:
                    store.backup(Path(path).parent / "backups" / (now().strftime("%Y%m%dT%H%M%S%f") + ".sqlite"))
                    last_backup = now()
                finally:
                    store.close()
        except Exception as error:
            failures += 1
            LOG.error("Worker cycle failed (%s); will retry", type(error).__name__)
        delay = min(900, 30 * 2**min(failures, 5)) if failures else polling_seconds(path, live)
        stop.wait(delay)


def serve(path, host, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                body = Path(__file__).with_name("dashboard.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif url.path in {"/api/dashboard", "/health"}:
                store = Store(path)
                try:
                    demo = parse_qs(url.query).get("mode", ["live"])[0] == "mock"
                    report = store.report(demo)
                    if url.path == "/health":
                        mode = "mock" if demo else "live"
                        latest = next((op for op in report["recent_operations"] if op["mode"] == mode), None)
                        # Three times the maximum normal polling interval is the hard health deadline.
                        healthy = bool(latest and latest["ok"] and report["freshness_seconds"] is not None and report["freshness_seconds"] < 2700)
                        report = {"healthy": healthy, "mode": mode, "last_collection": report["last_collection"],
                                  "freshness_seconds": report["freshness_seconds"], "trial_started": None}
                    else:
                        healthy = True
                    body = json.dumps(report, allow_nan=False).encode()
                finally:
                    store.close()
                self.send_response(200 if healthy else 503)
                self.send_header("Content-Type", "application/json")
            else:
                self.send_error(404)
                return
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass
    LOG.info("Dashboard started on port %s", port)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Paper-only NBA prototype; mock data is never live performance")
    parser.add_argument("--db", default="var/paper.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("once", "worker"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--live", action="store_true", help="Collection only; requires ODDS_API_KEY")
        cmd.add_argument("--region", default="us")
    dashboard = sub.add_parser("serve")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8000)
    report = sub.add_parser("report")
    report.add_argument("--mock", action="store_true")
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    settle = sub.add_parser("settle")
    settle.add_argument("bet")
    settle.add_argument("status", choices=["pending", "win", "loss", "push", "void"])
    settle.add_argument("--reason", required=True)
    settle.add_argument("--source", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "once":
        cycle(args.db, args.live, args.region)
    elif args.command == "worker":
        worker(args.db, args.live, args.region)
    elif args.command == "serve":
        serve(args.db, args.host, args.port)
    else:
        store = Store(args.db)
        try:
            if args.command == "report":
                print(json.dumps(store.report(args.mock), indent=2))
            elif args.command == "backup":
                store.backup(args.destination)
            elif args.command == "settle":
                store.settle(args.bet, args.status, args.reason, args.source)
        finally:
            store.close()


if __name__ == "__main__":
    main()
