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
            elif url.path in {"/api/dashboard", "/health", "/ready"}:
                store = Store(path)
                try:
                    demo = parse_qs(url.query).get("mode", ["live"])[0] == "mock"
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
