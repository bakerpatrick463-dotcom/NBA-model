# NBA Betting Research Bot

A working **paper-only, simulated-data prototype**. It does not place real bets. No 14-day live trial has started, no edge has been established, and 65% wins is an evaluation target rather than a promised result.

## Start here

Use Python 3.12 or newer. The application and tests use only the standard library; no subscriptions or package installs are needed for the demo.

```bash
cd /workspace/NBA-model
python3 -m unittest discover -s tests -v
python3 -m nba_bot once
python3 -m nba_bot serve
```

In a second terminal, use `python3 -m nba_bot worker` for adaptive polling. The dashboard listens on port 8000. Its default view is **live with no selections**: choose **Simulated demo** to see fictional examples. Running this inside Codex is development, not 24/7 hosting. Processes can stop when this task ends.

Other useful commands:

```bash
python3 -m nba_bot report --mock
python3 -m nba_bot report
python3 -m nba_bot backup var/backups/manual-001.sqlite
```

Use `python3 -m nba_bot --db /absolute/path/paper.sqlite …` to override storage. Keep a single worker per database. SQLite WAL transactions, deduplication, unique event/policy constraints, and append-only triggers protect the ledger from accidental rewrites. The database owner can bypass these protections; this is not tamper-proof external auditing.

## What works

- Fictional odds for player points, team totals, spreads and totals, with two fictional sportsbooks. Every quote has event/subject/period/side/line/price/region/time/rules metadata.
- Poisson count forecasts derived from a synthetic minutes × opportunity-rate baseline, and discrete Gaussian score/margin baselines. A constrained allocator conserves 240 team minutes with a 48-minute player cap. These are reproducible demonstrations, not fitted NBA predictions.
- Stale/future quotes, unsupported products, missing identities, and unavailable evidence are rejected. Push probability, commission, expected value, and minimum break-even price are explicit. Each line is evaluated separately.
- All qualifying alerts are retained; selections are limited to the available-book list and one per game per frozen policy. This reduces same-game exposure but does not model portfolio covariance. Exactly one unit per selection; no real-money execution exists.
- Immutable original forecasts, synthetic supporting evidence, selections and append-only settlement corrections. Same-day restarts cannot insert a second selection for the same game/policy.
- Read-only dashboard/API: wins/losses/pushes/voids/pending, net units, ROI, odds, daily/weekly/book/market/model breakdowns, coarse calibration bins and drawdown. Live reports exclude all simulated selections.
- Adaptive polling (60 seconds near tipoff, 5 minutes within six hours, otherwise 15 minutes), bounded retry backoff, operational failure records, daily SQLite online backups and freshness-aware health checks.
- A collection-only Odds API adapter for spreads/totals, with normalization support for team totals and basic player props. Live forecasts/selections are deliberately blocked until a real baseline and validated identities/rules exist.

## What remains

No historical training dataset, calibrated live forecasts, official injury ingestion, transcript collection, language-model extraction, player-specific workload learning, real roster/lineup matching, live result feed, automated settlement, closing-price snapshots, push notifications, or offsite backups are implemented. Role scenarios and opposing-team/player consistency are future work. Operating costs are not yet ingested. Model comparison and chronological evaluation have not been run because there is no historical dataset.

The worker logs failures instead of substituting mock data for missing live inputs. Mock and live modes are explicit; the live adapter never falls back to demo output.

See [provider coverage and data prerequisites](docs/DATA.md), [deployment](docs/DEPLOYMENT.md), and [ledger/evaluation protocol](docs/PROTOCOL.md). Test evidence is in [validation](docs/VALIDATION.md).

## Project layout

- `nba_bot/core.py`: quote validation, odds/EV/profit and distributions.
- `nba_bot/providers.py`: fictional fixtures and collection-only provider adapter.
- `nba_bot/store.py`: SQLite audit ledger, reporting and online backup.
- `nba_bot/__main__.py`: CLI, worker and dashboard HTTP server.
- `tests/test_system.py`: failure cases and end-to-end storage tests.

Nothing has been committed or pushed to GitHub by this implementation. Your project files are in this checkout. A commit saves a version locally; a push uploads that version to GitHub. Do not add `var/`, `.env`, credentials or database files to a commit.
