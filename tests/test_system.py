import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from nba_bot.core import allocate_minutes, american_to_decimal, iso, now, probabilities, profit, value
from nba_bot.providers import OddsAPI, mock_candidates
from nba_bot.store import Store


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"paper.sqlite"
        self.store = Store(self.path)
        self.at = now()
        self.candidates = mock_candidates(self.at)
        self.q = self.candidates[0][0]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def research(self):
        self.store.research(self.candidates, self.at, {"demo_book_a", "demo_book_b"})
        return self.store.report(True)["bets"][0]

    def test_odds_and_profit(self):
        self.assertAlmostEqual(american_to_decimal(-110), 1.909090909)
        self.assertEqual(american_to_decimal(150), 2.5)
        self.assertEqual(profit("win", 2.5), 1.5)
        self.assertEqual(profit("loss", 2.5), -1)
        for status in ("push", "void"):
            self.assertEqual(profit(status, 2.5), 0)
        with self.assertRaises(ValueError):
            profit("win", 2, 2)
        with self.assertRaises(ValueError):
            american_to_decimal(0)

    def test_push_distribution_and_commission(self):
        q = replace(self.q, line=20.0)
        p = probabilities(q, 20)
        self.assertAlmostEqual(sum(p), 1)
        self.assertGreater(p[2], 0)
        self.assertEqual(probabilities(replace(q, line=20.5), 20)[2], 0)
        ev, minimum = value(replace(q, commission=.05), (.5, .4, .1))
        self.assertAlmostEqual(ev, .5*(q.odds-1)*.95-.4)
        self.assertAlmostEqual(minimum, 1+.4/(.5*.95))

    def test_minutes_conserved_and_caps(self):
        allocation = allocate_minutes({"star": 100, **{str(i): 1 for i in range(7)}, "absent": 0})
        self.assertAlmostEqual(sum(allocation.values()), 240)
        self.assertTrue(all(0 <= m <= 48 for m in allocation.values()))
        self.assertEqual(allocation["absent"], 0)

    def test_bad_quotes_and_timestamp_eligibility(self):
        bad = [replace(self.q, quoted=iso(self.at-timedelta(minutes=3))),
               replace(self.q, quoted=iso(self.at+timedelta(seconds=1))),
               replace(self.q, start=iso(self.at)), replace(self.q, collected=iso(self.at+timedelta(seconds=1))),
               replace(self.q, status="suspended"), replace(self.q, status="withdrawn"),
               replace(self.q, subject=""), replace(self.q, side="unknown"),
               replace(self.q, product="pickem"), replace(self.q, odds=float("nan")),
               replace(self.q, rules="")]
        for q in bad:
            with self.subTest(q=q), self.assertRaises(ValueError):
                q.validate(self.at)

    def test_duplicate_restart_and_correlation_limit(self):
        bet = self.research()
        self.assertEqual(bet["quote"]["book"], "demo_book_b")
        alerts = self.store.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        self.assertGreater(alerts, 1)
        self.store.close()
        self.store = Store(self.path)
        self.research()
        self.assertEqual(len(self.store.report(True)["bets"]), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], alerts)
        self.assertEqual(self.store.report(False)["summary"]["selections"], 0)
        self.assertIsNone(self.store.report(False)["trial_started"])

    def test_reference_book_never_selected(self):
        self.store.research(self.candidates, self.at, set())
        self.assertEqual(self.store.report(True)["summary"]["selections"], 0)
        self.assertGreater(self.store.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 0)

    def test_future_evidence_rolls_back(self):
        q, p, assumptions, evidence = self.candidates[0]
        evidence = [{**evidence[0], "processed": iso(self.at+timedelta(seconds=1))}]
        with self.assertRaises(ValueError):
            self.store.research([(q, p, assumptions, evidence)], self.at, {q.book})
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)

    def test_live_forecasts_blocked(self):
        q, p, a, e = self.candidates[0]
        with self.assertRaises(ValueError):
            self.store.research([(replace(q, simulated=False), p, a, e)], self.at, {q.book})

    def test_settlement_corrections_and_postponement(self):
        bet = self.research()
        with self.assertRaises(ValueError):
            self.store.settle(bet["id"], "win", "too early", "fixture", self.at)
        after = self.at+timedelta(days=3)
        self.store.settle(bet["id"], "win", "fixture final", "mock://result", after)
        self.assertEqual(self.store.report(True)["summary"]["wins"], 1)
        self.store.settle(bet["id"], "push", "corrected fixture", "mock://correction", after)
        summary = self.store.report(True)["summary"]
        self.assertEqual(summary["pushes"], 1)
        self.assertEqual(summary["roi"], 0)
        self.assertIsNone(summary["win_rate"])
        self.store.settle(bet["id"], "void", "canceled", "mock://cancel", after)
        self.assertIsNone(self.store.report(True)["summary"]["roi"])
        self.store.settle(bet["id"], "pending", "postponed; awaiting result", "mock://postponed", after)
        self.assertEqual(self.store.report(True)["summary"]["pending"], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM settlements").fetchone()[0], 4)

    def test_ledger_immutability_and_backup(self):
        self.research()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("DELETE FROM bets")
        self.store.db.rollback()
        self.store.backup(Path(self.temp.name)/"backup.sqlite")
        with sqlite3.connect(Path(self.temp.name)/"backup.sqlite") as copy:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)

    def test_provider_normalization_rejects_wrong_team(self):
        events = [{"id": "nba-1", "commence_time": self.q.start, "home_team": "A", "away_team": "B", "bookmakers": [
            {"key": "book", "last_update": iso(self.at), "markets": [
                {"key": "spreads", "outcomes": [{"name": "A", "price": 1.91, "point": -2.5}]}]}]}]
        quotes = OddsAPI.normalize(events, self.at, "us")
        self.assertEqual(quotes[0].side, "home")
        self.assertFalse(quotes[0].simulated)
        self.assertIn("UNVERIFIED", quotes[0].rules)
        events[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["name"] = "Wrong team"
        with self.assertRaises(ValueError):
            OddsAPI.normalize(events, self.at, "us")


if __name__ == "__main__":
    unittest.main()
