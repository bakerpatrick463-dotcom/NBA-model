"""Explicit synthetic fixtures and a collection-only licensed-feed adapter."""
import json
import os
from datetime import timedelta
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

from .core import Quote, allocate_minutes, iso, now, probabilities


def mock_candidates(at):
    start = at.replace(hour=23, minute=59, second=0, microsecond=0) + timedelta(days=1)
    event = f"demo-{at.date()}-harbor-v-metro"
    # Scenarios are synthetic assumptions, never extracted real NBA evidence.
    minutes = allocate_minutes({"Demo Guard": 34, **{f"Teammate {i}": 29 for i in range(7)}})
    assumption = {"playing_probability": 1.0, "minutes": minutes,
                  "role": "normal (synthetic)", "points_per_minute": .78,
                  "warning": "All rates, news, teams, books, and prices are fictional"}
    evidence = [{"source_url": "mock://rotation/001", "text": "Synthetic coach: normal rotation expected.",
                 "speaker": "Fictional coach", "event_date": iso(at-timedelta(hours=2)),
                 "published": iso(at-timedelta(hours=1)), "first_observed": iso(at-timedelta(minutes=2)),
                 "processed": iso(at), "simulated": True, "signal": "normal_role"}]
    result = []
    specs = [("Demo Guard", "points", 20.5, minutes["Demo Guard"]*.78, None),
             ("Harbor", "team_total", 109.0, 114.0, 12.0),
             ("Harbor vs Metro", "total", 222.5, 229.0, 17.0),
             ("Harbor", "spread", -2.0, 6.0, 12.0)]
    for subject, market, line, mean, sd in specs:
        for book, odds in (("demo_book_a", 1.91), ("demo_book_b", 2.02)):
            q = Quote(event, iso(start), subject, market, "home" if market == "spread" else "over", float(line), odds,
                      book, "demo", iso(at), iso(at), True,
                      event_name="Harbor vs Metro (fictional)", book_name=book)
            result.append((q, probabilities(q, mean, sd), assumption, evidence))
    return result


class ProviderError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__("Odds API request failed; check access, quota, region and configuration (URL redacted)")


class OddsAPI:
    """Collection only. Require explicit book availability and rules before selection.

    Never forwards a key to a different host; errors must not include request URLs.
    Market names follow v4; actual access/coverage depends on account and region.
    """
    BASE = "https://api.the-odds-api.com/v4"
    MARKETS = {"spreads": "spread", "totals": "total", "team_totals": "team_total",
               "player_points": "points", "player_rebounds": "rebounds",
               "player_assists": "assists", "player_threes": "threes"}

    def __init__(self, region="us"):
        self.key = os.environ.get("ODDS_API_KEY")
        if not self.key:
            raise ValueError("ODDS_API_KEY is required for live collection")
        self.region = region
        self.usage = None

    def request(self, path, **params):
        params.update(apiKey=self.key)
        try:
            with urlopen(self.BASE + path + "?" + urlencode(params), timeout=20) as response:
                def count(name):
                    raw = response.headers.get(name)
                    return int(raw) if raw is not None and raw.isdigit() else None
                self.usage = {"remaining": count("x-requests-remaining"),
                              "used": count("x-requests-used"), "last_cost": count("x-requests-last")}
                return json.load(response)
        except HTTPError as error:
            raise ProviderError(error.code) from None
        except Exception:
            raise ProviderError() from None

    def collect(self, at):
        # Team markets use bulk endpoint; props require per-event requests and additional credits.
        events = self.request("/sports/basketball_nba/odds", regions=self.region,
                              markets="spreads,totals", oddsFormat="decimal", dateFormat="iso")
        return self.normalize(events, now(), self.region)

    @classmethod
    def normalize(cls, events, at, region):
        result = []
        for event in events:
            for book in event.get("bookmakers", []):
                for market in book.get("markets", []):
                    kind = cls.MARKETS.get(market["key"])
                    if kind is None:
                        continue
                    for outcome in market.get("outcomes", []):
                        name = outcome["name"]
                        if kind == "spread":
                            if name not in {event["home_team"], event["away_team"]}:
                                raise ValueError("Unmatched team; quote rejected")
                            side = "home" if name == event["home_team"] else "away"
                            subject = name
                        else:
                            if name not in {"Over", "Under"}:
                                raise ValueError("Unmatched outcome; quote rejected")
                            side = name.lower()
                            subject = outcome.get("description") if kind not in {"total"} else event["id"]
                            if not subject:
                                raise ValueError("Missing subject; cannot safely match quote")
                        stamp = market.get("last_update") or book.get("last_update")
                        if not stamp:
                            raise ValueError("Provider quote timestamp missing")
                        result.append(Quote(event["id"], iso(event["commence_time"]), subject, kind, side,
                                            float(outcome["point"]), float(outcome["price"]), book["key"], region,
                                            iso(stamp), iso(at), False,
                                            rules="UNVERIFIED provider/book-specific settlement rules",
                                            event_name=f"{event['away_team']} at {event['home_team']}",
                                            book_name=book.get("title", book["key"])))
        return result
