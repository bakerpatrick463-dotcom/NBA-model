# Validation evidence

Initial validation: 2026-10-20, Python 3.12.14.

- `python3 -m unittest discover -s tests -v`: 11 tests passed. Covers odds/profit, push probability/commission, 240-minute allocation, stale/future/suspended/withdrawn/malformed quotes, restart deduplication, one-game exposure cap, reference-only books, future evidence rejection/transaction rollback, live forecast block, settlement corrections/postponement, append-only enforcement, verified SQLite backup, and team matching.
- `python3 -m nba_bot once` run repeatedly: one simulated selection retained, multiple model alerts preserved, no duplicate selected bet.
- CLI reports: simulated report populated; live report has zero selections and no trial start.
- SQLite online backup completed and integrity checked. A temporary backup was opened in tests and its bet count verified.
- Dashboard HTML and functional JSON requested via local HTTP: simulated dashboard and health returned 200; live dashboard returned zero selections; live health correctly returned 503 without a live collector.
- Mock worker started, collected, logged and produced a backup. Dashboard started in the development workspace. These processes are not evidence of independent 24/7 hosting.
- `docker compose config --quiet`: passed. Docker images built successfully. An isolated Compose project ran mock collection twice against the same named volume; the container dashboard returned the simulated selection and supporting evidence, mock health was healthy, and a dashboard restart preserved exactly one selected bet with an intact ledger. Temporary smoke-test containers and volume were removed afterward. Docker client state used `/tmp/nba-docker-config` because the default home directory is read-only.
- Provider normalization fixture passed; no authenticated live provider request, official source ingestion, real forecast, automated settlement, chronological backtest or live trial was executed.
- Provider/official documentation network requests failed with proxy tunnel 403. Pricing, terms and current coverage remain unverified.

The test results establish prototype mechanics, not a betting edge or a deployment uptime guarantee. No test data enters live performance reports. Generated databases and local backups are ignored by Git.


Scheduled-host update: 24 automated tests passed (the original 14 plus ten scheduling/quota/backup tests). The new cases verify persistent six-hour timing across restarts, 30-minute failure backoff, six attempts per rolling day, the 100-credit reserve, missing-header pause, UTC month rollover, single collector locking, quota-header parsing, no mock fallback, empty snapshot clearing, and seven-copy backup retention without deleting manual backups. A local subprocess check ran combined `web` mode twice on a temporary database: a background worker produced data and a daily backup, `/ready` and mock `/health` passed, SIGTERM exited cleanly, and the restart preserved one selected mock bet. No external API calls were made by these automated checks. The JavaScript syntax check passed. Deployment of scheduled mode on Render still requires the user to update its start command.


Historical research update: 29 tests passed, including five new historical cases for future-result independence, same-day result isolation, scoring warmup accounting, duplicate-copy/neutral-game handling and rejection of unreviewed snapshot content. The reviewed 18,173,265-byte real CSV was downloaded via TLS and its SHA-256 recorded. The fixed baseline evaluation actually executed on its contents, producing the bundled derived research summary. The final 2015 home/away regular-season sample contains 1,228 games, with scoring forecasts on 1,069 after warmup. These results are isolated from both mock and live paper ledgers. There is no live model activation or betting trial start.

Book-preference update: all 33 tests passed. Four new cases check normalized user-declared book/jurisdiction configuration, malformed keys, FanDuel/reference separation including absent coverage, and isolation of simulated fixtures. No live selections were enabled by these display preferences.
