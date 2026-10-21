import argparse
import json
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .collector import acquire_lock, collection_status, cycle, run_worker
from .store import Store

LOG = logging.getLogger("nba_bot")


def worker(path, live, region):
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with acquire_lock(path):
        run_worker(path, live, region, stop)


def serve(path, host, port, worker_thread=None, stop=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                body = Path(__file__).with_name("dashboard.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif url.path in {"/api/dashboard", "/api/changes", "/health", "/ready"}:
                store = Store(path)
                try:
                    demo = parse_qs(url.query).get("mode", ["live"])[0] == "mock"
                    if url.path == "/api/changes":
                        report = {"version": store.change_version(demo)}
                        healthy = True
                    else:
                        report = store.report(demo)
                    if url.path == "/ready":
                        healthy = worker_thread is None or worker_thread.is_alive()
                        report = {"ready": healthy, "background_collector": worker_thread is not None}
                    elif url.path == "/health":
                        state = report["collector"]
                        healthy = state["collector_on_schedule"]
                        report = {"healthy": healthy, "mode": state["mode"], "collector": state,
                                  "last_collection": report["last_collection"],
                                  "quotes_fresh": report["freshness_seconds"] is not None and report["freshness_seconds"] < 120,
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
    server = ThreadingHTTPServer((host, port), Handler)
    if stop is not None:
        def shutdown(*_):
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if stop is not None:
            stop.set()
        if worker_thread is not None:
            worker_thread.join(timeout=25)


def web(path, host, port, live, region):
    # Single-instance SQLite deployment: web and worker share the mounted database.
    with acquire_lock(path):
        stop = threading.Event()
        collector = threading.Thread(target=run_worker, args=(path, live, region, stop), daemon=True)
        collector.start()
        serve(path, host, port, collector, stop)


def main():
    parser = argparse.ArgumentParser(description="Paper-only NBA prototype; mock data is never live performance")
    parser.add_argument("--db", default="var/paper.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("once", "worker", "web"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--live", action="store_true", help="Collection only; requires ODDS_API_KEY")
        cmd.add_argument("--region", default="us")
        if name == "web":
            cmd.add_argument("--host", default="127.0.0.1")
            cmd.add_argument("--port", type=int, default=8000)
    dashboard = sub.add_parser("serve")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8000)
    report = sub.add_parser("report")
    report.add_argument("--mock", action="store_true")
    injury = sub.add_parser("injuries", help="Extract text from the latest verified archived official injury PDF; no network requests")
    injury.add_argument("--full", action="store_true", help="Print all extracted pages instead of a short preview")
    sub.add_parser("sources", help="Check public NBA stats/injury sources without Odds API credits")
    sub.add_parser("stats-check", help="Check BALLDONTLIE NBA account access with at most five requests; no automatic polling")
    sub.add_parser("identity-check", help="Recheck official source identities against saved snapshots; no API requests")
    stats_import = sub.add_parser("stats-import", help="Resume versioned BALLDONTLIE roster/games/history imports; does not enable predictions")
    stats_import.add_argument("--scope", choices=["all","teams","roster","games","history"], default="all")
    stats_import.add_argument("--max-pages", type=int, choices=range(1,501), default=300, metavar="1..500")
    stats_import.add_argument("--refresh", action="store_true", help="Start new snapshots, preserving previous data")
    coverage = sub.add_parser("coverage", help="One upcoming-game FanDuel prop check; estimated four credits")
    coverage.add_argument("--book", choices=["fanduel"], default="fanduel")
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
    elif args.command == "web":
        web(args.db, args.host, args.port, args.live, args.region)
    elif args.command == "injuries":
        from .injuries import extract, summary
        result=extract(args.db)
        print(json.dumps(result if args.full else summary(result),indent=2))
        if result["status"] in {"dependency_missing","error"}:
            raise SystemExit(1)
    elif args.command == "identity-check":
        from .statistics_identity import identify
        store=Store(args.db)
        try:
            result=identify(store)
            print(json.dumps({key:value for key,value in result.items() if key!='rows'},indent=2))
        finally:
            store.close()
    elif args.command == "stats-import":
        from .statistics_import import import_data
        result=import_data(args.db,args.scope,args.max_pages,args.refresh,
                           notify=lambda message: print(message,flush=True))
        # Keep shell output reviewable; complete immutable match rows are in the database/dashboard.
        if result.get('identity_check',{}).get('rows') is not None:
            result['identity_check']={key:value for key,value in result['identity_check'].items() if key!='rows'}
        print(json.dumps(result,indent=2))
        if result['status'] in {'configuration_required','paused_error'}:
            raise SystemExit(1)
    elif args.command == "stats-check":
        from .statistics_provider import check
        result=check(args.db)
        print(json.dumps(result,indent=2))
        if result['status']=='configuration_required':
            raise SystemExit(1)
    elif args.command == "sources":
        from .current_data import check
        print(json.dumps(check(args.db),indent=2))
    elif args.command == "coverage":
        from .coverage import check
        result=check(args.db,args.book)
        print(json.dumps(result,indent=2))
        if result["status"]=="error":
            raise SystemExit(1)
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
