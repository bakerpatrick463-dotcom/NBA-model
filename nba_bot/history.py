"""Historical mechanics study, never a source of current-game selections."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict, deque
from datetime import datetime
from itertools import groupby
from pathlib import Path
from urllib.request import urlopen

from .core import iso, now

SOURCE = "https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-elo/nbaallelo.csv"
SOURCE_SHA256 = "d46ed3540ee8d9eca31b3e94cc8c777e0be5156173d814ebf65b8195e8d616bc"
ATTRIBUTION = "Derived from FiveThirtyEight Historical NBA Elo data; game information originally from Basketball-Reference.com."
LICENSE = "https://creativecommons.org/licenses/by/4.0/"
PARAMETERS = {"initial_elo": 1500, "elo_k": 20, "home_advantage_elo": 65,
              "season_carryover": .75, "score_window": 20, "minimum_team_games": 10,
              "home_score_adjustment": 1.5, "training_start_season": 2000,
              "validation_season": 2014, "final_evaluation_season": 2015}


def download(destination):
    target = Path(destination)
    if target.exists():
        raise ValueError("Choose a new destination; existing files are never overwritten")
    with urlopen(SOURCE, timeout=45) as response:
        content = response.read(32*1024*1024+1)
    if hashlib.sha256(content).hexdigest() != SOURCE_SHA256:
        raise ValueError("Archive differs from the reviewed snapshot; investigate upstream before importing")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def load_games(content):
    """Canonical one-row-per-game results, excluding playoffs/neutral sites and other leagues."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    required = {"game_id", "lg_id", "_iscopy", "year_id", "date_game", "is_playoffs",
                "team_id", "opp_id", "pts", "opp_pts", "game_location"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("Historical CSV schema is incomplete")
    games, seen = [], set()
    for row in reader:
        if row["lg_id"] != "NBA" or row["_iscopy"] != "0" or row["is_playoffs"] != "0" or row["game_location"] not in {"H", "A"}:
            continue
        season = int(row["year_id"])
        if season < PARAMETERS["training_start_season"]:
            continue
        key = row["game_id"]
        if not key or key in seen:
            raise ValueError("Duplicate or missing canonical game identity")
        seen.add(key)
        home = row["game_location"] == "H"
        own, opponent = int(row["pts"]), int(row["opp_pts"])
        if not row["team_id"] or not row["opp_id"] or row["team_id"] == row["opp_id"] or min(own, opponent) <= 0 or own == opponent:
            raise ValueError("Invalid final score or team identity")
        games.append({"id": key, "day": datetime.strptime(row["date_game"], "%m/%d/%Y").date().isoformat(),
                      "season": season, "home": row["team_id"] if home else row["opp_id"],
                      "away": row["opp_id"] if home else row["team_id"],
                      "home_score": own if home else opponent, "away_score": opponent if home else own})
    return sorted(games, key=lambda g: (g["day"], g["id"]))


def replay(games):
    ratings = defaultdict(lambda: PARAMETERS["initial_elo"])
    scores = defaultdict(lambda: deque(maxlen=PARAMETERS["score_window"]))
    wins = total = 0
    season = None
    predictions = []
    league_points = league_games = 0
    for day, batch in groupby(sorted(games, key=lambda g: (g["day"], g["id"])), key=lambda g: g["day"]):
        batch = list(batch)
        seasons = {g["season"] for g in batch}
        if len(seasons) != 1:
            raise ValueError("Ambiguous season for a single day")
        this_season = next(iter(seasons))
        if season != this_season:
            for team in ratings:
                ratings[team] = 1500 + PARAMETERS["season_carryover"]*(ratings[team]-1500)
            scores.clear()
            league_points = league_games = 0
            season = this_season
        # All same-day forecasts are frozen before incorporating ANY same-day result.
        today = []
        for game in batch:
            home, away = game["home"], game["away"]
            p = 1/(1+10**((ratings[away]-ratings[home]-PARAMETERS["home_advantage_elo"])/400))
            row = {**game, "home_win_probability": p, "historical_home_probability": (wins+1)/(total+2),
                   "projected_home_score": None, "projected_away_score": None, "league_score_baseline": None}
            if min(len(scores[home]), len(scores[away])) >= PARAMETERS["minimum_team_games"]:
                mean = lambda team, index: sum(s[index] for s in scores[team])/len(scores[team])
                row["projected_home_score"] = (mean(home, 0)+mean(away, 1))/2 + PARAMETERS["home_score_adjustment"]
                row["projected_away_score"] = (mean(away, 0)+mean(home, 1))/2 - PARAMETERS["home_score_adjustment"]
                row["league_score_baseline"] = league_points/(2*league_games)
            today.append(row)
        predictions.extend(today)
        for row in today:
            home, away = row["home"], row["away"]
            outcome = int(row["home_score"] > row["away_score"])
            delta = PARAMETERS["elo_k"]*(outcome-row["home_win_probability"])
            ratings[home] += delta
            ratings[away] -= delta
            scores[home].append((row["home_score"], row["away_score"]))
            scores[away].append((row["away_score"], row["home_score"]))
            wins += outcome
            total += 1
            league_points += row["home_score"]+row["away_score"]
            league_games += 1
    return predictions


def metrics(rows):
    def probability_metrics(field):
        if not rows:
            return {"games": 0, "brier": None, "log_loss": None, "winner_accuracy": None}
        probs = [min(1-1e-12, max(1e-12, row[field])) for row in rows]
        outcomes = [int(row["home_score"] > row["away_score"]) for row in rows]
        return {"games": len(rows), "brier": sum((p-y)**2 for p,y in zip(probs,outcomes))/len(rows),
                "log_loss": -sum(y*math.log(p)+(1-y)*math.log1p(-p) for p,y in zip(probs,outcomes))/len(rows),
                "winner_accuracy": sum((p>=.5)==bool(y) for p,y in zip(probs,outcomes))/len(rows)}
    eligible = [row for row in rows if row["projected_home_score"] is not None]
    def score_metrics(use_team):
        errors, margin_errors, total_errors = [], [], []
        for row in eligible:
            home, away = ((row["projected_home_score"], row["projected_away_score"]) if use_team else
                          (row["league_score_baseline"]+1.5, row["league_score_baseline"]-1.5))
            errors.extend([abs(home-row["home_score"]), abs(away-row["away_score"])])
            margin_errors.append(abs(home-away-(row["home_score"]-row["away_score"])))
            total_errors.append(abs(home+away-(row["home_score"]+row["away_score"])))
        average = lambda values: sum(values)/len(values) if values else None
        return {"games": len(eligible), "team_score_mae": average(errors),
                "margin_mae": average(margin_errors), "total_mae": average(total_errors)}
    return {"winner_baselines": {"expanding_home_rate": probability_metrics("historical_home_probability"),
                                 "elo": probability_metrics("home_win_probability")},
            "scoring_baselines": {"prior_league_mean": score_metrics(False), "rolling_team_scoring": score_metrics(True)},
            "score_games_skipped_insufficient_history": len(rows)-len(eligible)}


def evaluate(source):
    content = Path(source).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("Only the reviewed historical snapshot may produce a published research report")
    games = load_games(content)
    predictions = replay(games)
    validation = [r for r in predictions if r["season"] == PARAMETERS["validation_season"]]
    final = [r for r in predictions if r["season"] == PARAMETERS["final_evaluation_season"]]
    if not validation or not final:
        raise ValueError("Expected chronological evaluation seasons missing")
    return {"status": "HISTORICAL RESEARCH ONLY — NOT CURRENT NBA FORECASTS", "created": iso(now()),
            "model_version": "historical-mechanics-v1", "parameters": PARAMETERS,
            "source": SOURCE, "source_sha256": digest, "attribution": ATTRIBUTION, "license": LICENSE,
            "first_game": games[0]["day"], "last_game": games[-1]["day"], "unique_games": len(games),
            "validation": metrics(validation), "final_evaluation": metrics(final),
            "limitations": ["Archive ends in 2015; unsuitable for current-game forecasting",
                            "Retrospective results lack archived first-observed/revision timestamps; not an executable as-of backtest",
                            "Forecasts use only earlier days; final season is evaluated sequentially without parameter tuning",
                            "No odds, player props, injury evidence, market-only comparison, ROI or selected-bet win rate",
                            "Winner accuracy measures game winners, not performance against sportsbook spreads",
                            "Team ID changes and season roster/coaching changes are not modeled"]}


def report():
    path = Path(__file__).with_name("research_summary.json")
    return json.loads(path.read_text()) if path.exists() else None


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("destination")
    run = sub.add_parser("evaluate")
    run.add_argument("source")
    run.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        print(download(args.destination))
    else:
        destination = Path(args.output)
        if destination.exists():
            raise ValueError("Choose a new report path to preserve the previous research result")
        result = evaluate(args.source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
        print(json.dumps(result["final_evaluation"], indent=2))


if __name__ == "__main__":
    main()
