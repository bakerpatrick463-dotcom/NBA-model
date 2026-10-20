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
