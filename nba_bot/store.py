from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .core import MODEL, POLICY, Quote, dt, identity, iso, now, profit, value

SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT NOT NULL,
 region TEXT NOT NULL, estimated_cost INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS request_types(attempt INTEGER PRIMARY KEY REFERENCES collection_attempts(id), kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coverage_checks(attempt INTEGER PRIMARY KEY REFERENCES collection_attempts(id), payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_usage(attempt INTEGER PRIMARY KEY REFERENCES collection_attempts(id),
 recorded TEXT NOT NULL, remaining INTEGER, used INTEGER, last_cost INTEGER);

CREATE TABLE IF NOT EXISTS collection_batches(seq INTEGER PRIMARY KEY AUTOINCREMENT,
 recorded TEXT NOT NULL, simulated INTEGER NOT NULL, quote_count INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS collection_batches_mode_seq ON collection_batches(simulated,seq);
CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS quotes_mode_collected ON quotes
 (json_extract(payload, '$.simulated'), json_extract(payload, '$.collected'));
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forecasts(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bets(id TEXT PRIMARY KEY, event TEXT NOT NULL, simulated INTEGER NOT NULL,
 policy TEXT NOT NULL, selected TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(event,simulated,policy));
CREATE TABLE IF NOT EXISTS settlements(seq INTEGER PRIMARY KEY AUTOINCREMENT, bet TEXT NOT NULL REFERENCES bets(id),
 status TEXT NOT NULL CHECK(status IN ('pending','win','loss','push','void')), recorded TEXT NOT NULL,
 reason TEXT NOT NULL, source TEXT NOT NULL, net REAL NOT NULL);
CREATE TABLE IF NOT EXISTS operations(seq INTEGER PRIMARY KEY AUTOINCREMENT, recorded TEXT NOT NULL,
 mode TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        for table in ("quotes", "evidence", "forecasts", "alerts", "bets", "settlements", "operations", "collection_attempts", "provider_usage", "collection_batches", "request_types", "coverage_checks"):
            for action in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only audit table'); END")
        self.db.commit()

    def close(self):
        self.db.close()

    def put(self, table, payload):
        if table not in {"quotes", "evidence", "forecasts", "alerts"}:
            raise ValueError("Invalid table")
        key = identity(payload)
        self.db.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,?)", (key, json.dumps(payload, sort_keys=True)))
        return key

    def collect(self, quotes, simulated=None):
        quotes = list(quotes)
        modes = {q.simulated for q in quotes}
        if simulated is not None:
            if modes - {simulated}:
                raise ValueError("Mixed collection modes")
            modes.add(simulated)
        with self.db:
            for q in quotes:
                # Keep even rejected quotes for audit; never silently repair identity.
                self.put("quotes", asdict(q))
            for mode in modes:
                times = [q.collected for q in quotes if q.simulated == mode]
                self.db.execute("INSERT INTO collection_batches(recorded,simulated,quote_count) VALUES (?,?,?)",
                                (max(times) if times else iso(now()), mode, len(times)))

    def operation(self, mode, ok, detail):
        with self.db:
            self.db.execute("INSERT INTO operations(recorded,mode,ok,detail) VALUES (?,?,?,?)", (iso(now()), mode, ok, detail))

    def research(self, candidates, at, available_books):
        """Persist all eligible forecasts/alerts, then choose best EV once per game."""
        ranked = []
        with self.db:
            for q, p, assumptions, evidence in candidates:
                q.validate(at)
                if not q.simulated:
                    raise ValueError("Live forecasting is not validated; only mock candidates allowed")
                if len(p) != 3 or any(not math.isfinite(v) or v < 0 or v > 1 for v in p) or abs(sum(p) - 1) > 1e-8:
                    raise ValueError("Invalid outcome distribution")
                evidence_ids = []
                for item in evidence:
                    if not dt(item["event_date"]) <= dt(item["published"]) <= dt(item["first_observed"]) <= dt(item["processed"]) <= dt(at):
                        raise ValueError("Evidence was unavailable at forecast time")
                    evidence_ids.append(self.put("evidence", item))
                qid = self.put("quotes", asdict(q))
                f = {"quote": qid, "created": iso(at), "model": MODEL, "win": p[0], "loss": p[1], "push": p[2],
                     "assumptions": assumptions, "evidence": evidence_ids, "simulated": True,
                     "uncertainty": "Uncalibrated synthetic demonstration; no validated confidence interval"}
                fid = self.put("forecasts", f)
                ev, minimum = value(q, p)
                if ev < 0.05:
                    continue
                payload = {"quote": asdict(q), "quote_id": qid, "forecast": fid, "model": MODEL, "policy": POLICY,
                           "win_probability": p[0], "push_probability": p[2], "ev": ev, "minimum_price": minimum,
                           "selected": iso(at), "risk": 1.0, "evidence": evidence_ids, "assumptions": assumptions,
                           "uncertainty": f["uncertainty"]}
                alert_id = self.put("alerts", payload)
                if q.book in available_books:
                    ranked.append((ev, alert_id, payload))
            for _, aid, payload in sorted(ranked, key=lambda r: (-r[0], r[1])):
                q = payload["quote"]
                self.db.execute("INSERT OR IGNORE INTO bets VALUES (?,?,?,?,?,?)", (aid, q["event"], True, POLICY, iso(at), json.dumps(payload, sort_keys=True)))
        return len(ranked)

    def settle(self, bet_id, status, reason, source, recorded=None):
        if not reason.strip() or not source.strip():
            raise ValueError("Settlement requires reason and source")
        row = self.db.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown bet")
        payload = json.loads(row["payload"])
        timestamp = iso(recorded or now())
        if dt(timestamp) < dt(payload["quote"]["start"]):
            raise ValueError("Cannot settle before event start")
        effective = 1 + (payload["quote"]["odds"] - 1) * (1 - payload["quote"]["commission"])
        net = 0.0 if status == "pending" else profit(status, effective)
        with self.db:
            self.db.execute("INSERT INTO settlements(bet,status,recorded,reason,source,net) VALUES (?,?,?,?,?,?)", (bet_id, status, timestamp, reason, source, net))

    def report(self, simulated=False):
        from .collector import collection_status
        from .history import report as historical_report
        from .settings import book_preferences
        from .refresh import timestamp_ages
        preferences = book_preferences()
        view_version = self.change_version(simulated)
        rows = self.db.execute("""SELECT b.*, s.status,s.net FROM bets b LEFT JOIN settlements s
          ON s.seq=(SELECT MAX(x.seq) FROM settlements x WHERE x.bet=b.id)
          WHERE b.simulated=? ORDER BY b.selected,b.id""", (simulated,)).fetchall()
        bets = []
        for row in rows:
            p = json.loads(row["payload"])
            p["supporting_evidence"] = [json.loads(self.db.execute("SELECT payload FROM evidence WHERE id=?", (eid,)).fetchone()[0]) for eid in p["evidence"]]
            bets.append({"id": row["id"], **p, "status": row["status"] or "pending", "net": row["net"] or 0.0})
        def metrics(items):
            wins = sum(b["status"] == "win" for b in items)
            losses = sum(b["status"] == "loss" for b in items)
            settled = [b for b in items if b["status"] in {"win", "loss", "push"}]
            net = sum(b["net"] for b in settled)
            return {"selections": len(items), "wins": wins, "losses": losses,
                    "pushes": sum(b["status"] == "push" for b in items),
                    "voids": sum(b["status"] == "void" for b in items),
                    "pending": sum(b["status"] == "pending" for b in items),
                    "win_rate": wins / (wins + losses) if wins + losses else None,
                    "net_units": net, "roi": net / len(settled) if settled else None,
                    "average_decimal_odds": sum(b["quote"]["odds"] for b in items) / len(items) if items else None}
        breakdowns = {}
        for field in ("market", "book", "model", "day", "week"):
            groups = {}
            for b in bets:
                key = (b["quote"][field] if field in {"market", "book"} else b["model"] if field == "model" else
                       b["selected"][:10] if field == "day" else dt(b["selected"]).strftime("%G-W%V"))
                groups.setdefault(key, []).append(b)
            breakdowns[field] = {k: metrics(v) for k, v in groups.items()}
        calibration = []
        for lower in (0, .2, .4, .6, .8):
            group = [b for b in bets if b["status"] in {"win", "loss"} and min(4, int(5 * b["win_probability"] / (1-b["push_probability"]))) == round(lower * 5)]
            calibration.append({"lower": lower, "n": len(group), "observed_win_rate": metrics(group)["win_rate"]})
        equity = peak = drawdown = 0.0
        # Settlement-time equity using the latest audited result, not selection order.
        settled_rows = self.db.execute("""SELECT s.net FROM settlements s JOIN bets b ON b.id=s.bet
          WHERE b.simulated=? AND s.seq=(SELECT MAX(x.seq) FROM settlements x WHERE x.bet=b.id)
          ORDER BY s.recorded,s.seq""", (simulated,)).fetchall()
        for row in settled_rows:
            equity += row["net"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        ops = [dict(r) for r in self.db.execute("SELECT * FROM operations ORDER BY seq DESC LIMIT 10")]
        latest, quotes = self.latest_quotes(simulated)
        for q in quotes:
            q["event_name"] = q.get("event_name") or q["event"]
            q["book_name"] = q.get("book_name") or q["book"]
            q["user_listed_book"] = not simulated and q["book"] in preferences["user_listed_books"]
            q["book_role"] = "Simulated fixture" if simulated else "User-listed sportsbook" if q["user_listed_book"] else "Market reference"
            age = (now()-dt(q["quoted"])).total_seconds()
            if dt(q["start"]) <= now():
                q["display_status"] = "Started / past event"
            elif age < 0 or dt(q["quoted"]) > dt(q["collected"]):
                q["display_status"] = "Invalid timestamp"
            elif q["status"] != "active":
                q["display_status"] = q["status"]
            elif age > 120 or (now()-dt(q["collected"])).total_seconds() > 120:
                q["display_status"] = "Stale snapshot"
            else:
                q["display_status"] = "Recent snapshot"
        quotes.sort(key=lambda q: (q["start"], q["event_name"], q["market"], q["subject"], q["side"], q["line"], -q["odds"], q["book"]))
        coverage_check = self.db.execute("SELECT payload FROM coverage_checks ORDER BY attempt DESC LIMIT 1").fetchone() if not simulated else None
        return {"latest_prop_coverage_check": json.loads(coverage_check[0]) if coverage_check else None, "mode": "SIMULATED — NOT LIVE PERFORMANCE" if simulated else "LIVE — NO TRIAL STARTED",
                "trial_started": None, "server_time": iso(now()), "view_version": view_version, "quote_timestamp_age_at_collection": timestamp_ages(quotes), "book_configuration": preferences,
                "book_coverage": {"user_listed_quotes": sum(q["user_listed_book"] for q in quotes),
                                  "reference_quotes": sum(not q["user_listed_book"] for q in quotes) if not simulated else 0,
                                  "listed_books_missing_from_snapshot": sorted(set(preferences["user_listed_books"]) - {q["book"] for q in quotes})},
                "historical_research": historical_report(), "collector": collection_status(self, simulated), "quotes": quotes,
                "coverage": {"quotes": len(quotes), "games": len({q["event"] for q in quotes}),
                             "books": len({q["book"] for q in quotes})},
                "summary": metrics(bets), "breakdowns": breakdowns, "bets": bets,
                "calibration": calibration, "max_drawdown_units": drawdown,
                "last_collection": latest, "freshness_seconds": (now()-dt(latest)).total_seconds() if latest else None,
                "recent_operations": ops, "closing_line_movement": None,
                "limitations": ["No verified live model or automated settlement", "Closing snapshots unavailable", "Operating costs not collected; excluded from betting P&L", "No validated edge or confidence intervals"]}

    def change_version(self, simulated=False):
        from .settings import book_preferences
        import os
        # Small indexed/count queries let browsers discover changes without redownloading every price.
        stamp = self.db.execute("SELECT MAX(json_extract(payload, '$.collected')) FROM quotes WHERE json_extract(payload, '$.simulated')=?", (simulated,)).fetchone()[0]
        batch = self.db.execute("SELECT MAX(seq) FROM collection_batches WHERE simulated=?", (simulated,)).fetchone()[0]
        mode = "mock" if simulated else "live"
        operation = self.db.execute("SELECT MAX(seq) FROM operations WHERE mode=?", (mode,)).fetchone()[0]
        bet = self.db.execute("SELECT MAX(rowid) FROM bets WHERE simulated=?", (simulated,)).fetchone()[0]
        settlement = self.db.execute("SELECT MAX(s.seq) FROM settlements s JOIN bets b ON b.id=s.bet WHERE b.simulated=?", (simulated,)).fetchone()[0]
        usage = self.db.execute("SELECT MAX(attempt) FROM provider_usage").fetchone()[0] if not simulated else None
        coverage = self.db.execute("SELECT MAX(attempt) FROM coverage_checks").fetchone()[0] if not simulated else None
        return identity([stamp,batch,operation,bet,settlement,usage,coverage,book_preferences(),os.environ.get("ODDS_REFRESH_PROFILE", "development")])

    def latest_quotes(self, simulated=False):
        # Indexed lookup keeps HTTP refreshes bounded to the current snapshot as history grows.
        quote_stamp = self.db.execute("SELECT MAX(json_extract(payload, '$.collected')) FROM quotes WHERE json_extract(payload, '$.simulated')=?", (simulated,)).fetchone()[0]
        batch = self.db.execute("SELECT recorded FROM collection_batches WHERE simulated=? ORDER BY seq DESC LIMIT 1", (simulated,)).fetchone()
        stamps = [stamp for stamp in (quote_stamp, batch["recorded"] if batch else None) if stamp]
        latest = max(stamps, default=None)
        quotes = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM quotes WHERE json_extract(payload, '$.simulated')=? AND json_extract(payload, '$.collected')=?", (simulated, latest))] if latest else []
        return latest, quotes

    def backup(self, destination):
        target = Path(destination)
        if target.resolve() == self.path.resolve() or target.exists():
            raise ValueError("Backup must use a new separate path")
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as copy:
            self.db.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
