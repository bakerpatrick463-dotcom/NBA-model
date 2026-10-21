# NBA Betting Research Bot

A working **paper-only, simulated-data prototype**. It does not place real bets. No 14-day live trial has started, no edge has been established, and 65% wins is an evaluation target rather than a promised result.

## Start here

Use Python 3.12 or newer. The mock demo and core tests use the standard library. Official injury PDF text extraction additionally requires the pinned reader in `requirements.txt`; no subscription is required for that reader.

```bash
cd /workspace/NBA-model
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --require-hashes -r requirements.txt
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
- Searchable collected-price table with game names, sportsbook names, market/book filters, decimal prices, source timestamps and stale-snapshot labels. Only the latest stored snapshot is shown; refreshing the website does not request provider data. Existing records without display names fall back to their original identifiers.
- A reproducible historical study compares Elo and expanding home-win baselines plus league/team scoring baselines on a reviewed FiveThirtyEight archive, with separate chronological evaluation and explicit archive-age limitations.
- Read-only dashboard/API: wins/losses/pushes/voids/pending, net units, ROI, odds, daily/weekly/book/market/model breakdowns, coarse calibration bins and drawdown. Live reports exclude all simulated selections.
- A hosted `web` command runs the dashboard and a single background collector together. Live collection defaults to six-hour development snapshots with a persistent retry/credit guard; mock polling is every 15 minutes. Daily local online backups retain seven managed snapshots. Operational records, schedule status and health checks are visible.
- A collection-only Odds API adapter for spreads/totals, with normalization support for team totals and basic player props. Live forecasts/selections are deliberately blocked until a real baseline and validated identities/rules exist.

## What remains

No current historical training feed, calibrated live forecasts, official injury ingestion, transcript collection, language-model extraction, player-specific workload learning, real roster/lineup matching, live result feed, automated settlement, closing-price snapshots, push notifications, or offsite backups are implemented. Role scenarios and opposing-team/player consistency are future work. Operating costs are not yet ingested. A separate real-data historical baseline study is now available, using an archive ending in 2015. It does not enable current forecasts or paper bets; see [research methods and limitations](docs/RESEARCH.md).

The worker logs failures instead of substituting mock data for missing live inputs. Mock and live modes are explicit; the live adapter never falls back to demo output.

See [provider coverage and data prerequisites](docs/DATA.md), [deployment](docs/DEPLOYMENT.md), and [ledger/evaluation protocol](docs/PROTOCOL.md). Test evidence is in [validation](docs/VALIDATION.md).

## Project layout

- `nba_bot/core.py`: quote validation, odds/EV/profit and distributions.
- `nba_bot/providers.py`: fictional fixtures and collection-only provider adapter.
- `nba_bot/store.py`: SQLite audit ledger, reporting and online backup.
- `nba_bot/__main__.py`: CLI, worker and dashboard HTTP server.
- `tests/test_system.py`: failure cases and end-to-end storage tests.

The prototype is committed and uploaded to GitHub. A commit saves a version locally; a push uploads that version to GitHub. Do not add `var/`, `.env`, credentials or database files to a commit.

## Render: scheduled development collection

After attaching a persistent disk at `/var/data`, use this Start Command on a single paid Python web-service instance:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite web --live --region us --host 0.0.0.0 --port $PORT
```

Keep `ODDS_API_KEY` in Render's secure Environment settings. Use `/ready` as the platform health-check path. The background collector and HTTP server run in one process and share the mounted SQLite database. SIGTERM stops the collector and server gracefully. A file lock blocks a second worker on the same database; persistent atomic request reservations stop rapid restarts from making extra paid requests. Do not scale this SQLite deployment to multiple instances.

The initial 500-credit development plan uses a **six-hour** live interval, at most **six attempts per rolling 24 hours**, and a **100-credit reserve** based on provider response headers. Failed unconfirmed requests count toward the limits and retry no sooner than 30 minutes. At two credits per response, four collections/day consume roughly 248 credits in a 31-day month before retries or other uses of the account. This is an estimate, not a provider pricing guarantee. Missing remaining-credit headers pause further requests until the next UTC calendar month; insufficient known quota also pauses collection. Monitor the provider account for calls outside this app.

The dashboard shows the next eligible request and provider credits at the last response. A restart may defer its first request because an earlier reservation is still active. `/ready` checks that the HTTP server and background thread are alive; `/health` reports collection operation health separately from `quotes_fresh`. An old quote stays labeled stale even while the six-hour scheduler is functioning normally. A quota pause can leave the site available while collection health is degraded.

Backups are under `/var/data/backups/auto-YYYY-MM-DD.sqlite`; the latest seven managed daily copies are retained, with manual backups untouched. They are on the same disk and **must still be copied off-host** for disaster recovery. This schedule is for development, not timely betting decisions. No live paper-trial clock starts and live forecasts/selections remain disabled.

## Personal books versus market references

Set these non-secret variables in your hosting environment to label your own books:

```text
AVAILABLE_BOOKS=fanduel
USER_JURISDICTION=US-VA
```

`AVAILABLE_BOOKS` accepts comma-separated provider bookmaker keys. Without it, live books are displayed as market references. `USER_JURISDICTION` is an optional user-declared label, not a verified eligibility determination. These labels are visible on the dashboard. Configured live views default to **My listed books**; choose **All collected books** for reference prices. The API reports missing listed books rather than substituting another sportsbook. Simulated fixtures never become personally available live books. This view configuration does not enable live selections, establish that a particular market is available in an account, or narrow the existing provider request; it consumes no additional API calls.

## Faster odds collection

An opt-in fast profile is prepared; it is **not enabled by default**. It targets one-minute polling when upcoming NBA games are available, with provider-quota verification and disk-space guards. The browser checks for newly stored data every five seconds without spending provider credits. Read [freshness, budget and storage requirements](docs/FRESHNESS.md) before changing frequency or purchasing a data plan. Provider-source latency and current player-prop availability still need verification. The initial 1 GB disk needs capacity planning before sustained fast collection.

## One-game FanDuel player-prop access check

Before choosing a paid plan, an operator can run this explicit diagnostic in the deployed service's shell, using its existing secure API-key variable and mounted database:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite coverage --book fanduel
```

It chooses the earliest stored upcoming game within 48 hours and makes at most one event-odds request, for points/rebounds/assists/threes and FanDuel only. Estimated cost is four credits; the returned provider header reports actual cost when available. Current-month known quota, daily account limits and the reserve are required, and repeat diagnostics are deferred for one hour. Errors show a sanitized HTTP status/type without printing credential-bearing URLs. Empty props for one game do not prove absence across all games or plans. No alternate bookmaker is substituted.

The result appears under **FanDuel prop coverage test** on the live dashboard. Diagnostic samples are stored separately and do not replace the bulk spreads/totals snapshot or create selections. This command does not enable continuous prop collection, validate settlement rules or start the trial. `prices_returned` means this account/book/game returned data; broader coverage needs further observations. A failed request or missing source metadata remains a reported limitation.

## Current statistics and official injury-source access

For the subscribed BALLDONTLIE NBA account, first save `BALLDONTLIE_API_KEY` in
Render's secret environment settings, deploy, then run:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite stats-check
```

This bounded check reads at most five response pages for teams, active players,
games, historical player statistics and injuries. Results appear under **NBA
statistics connection**. It does not use Odds API credits, enable automatic stats
polling or create live predictions. See [connection instructions and sample
limitations](docs/STATISTICS_PROVIDER.md).

After account access is confirmed, run `python3 -m nba_bot --db /var/data/paper.sqlite stats-import` in Render Shell. It saves versioned roster,
schedule and recent historical-stat pages, with checkpoints that resume when the
same command is rerun. It may take several minutes. Saved imports and exact
injury-to-provider identity checks appear on the live dashboard. Pagination
completion does not establish complete statistical coverage, a validated model or
current availability; predictions remain disabled. See [resumable import
behavior](docs/STATISTICS_PROVIDER.md#resumable-roster-schedule-and-history-imports).

While paid odds upgrades are deferred, development can check NBA statistics and official report sources without spending Odds API credits:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite sources
```

Run this in Render's Shell after the latest deployment. The resulting **Current NBA source checks** section reports each endpoint separately. Reachability is not yet confirmed on Render; this workspace's network blocked those destinations. Injury PDFs are archived rather than automatically interpreted, and the historical player-stat query checks the previous completed season rather than pretending to forecast current games. See [source-check behavior and limitations](docs/CURRENT_DATA.md).

After a PDF is archived, `python3 -m nba_bot --db /var/data/paper.sqlite injuries` extracts its text and structured source rows locally using the pinned PDF reader. The dashboard shows the reported player name, team, matchup, status and complete wrapped reason with a source page reference. Team reports marked `NOT YET SUBMITTED` remain separate notices. These are archived source labels; canonical player/game matching, current roster validation and live predictions remain unavailable. See [Render extraction setup](docs/DEPLOYMENT.md#render-injury-report-text-extraction).
