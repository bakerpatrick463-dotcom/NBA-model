# Deployment prerequisites

## Render injury-report text extraction

Set the Render **Build Command** to:

```bash
python3 -m pip install --require-hashes -r requirements.txt && python3 -m unittest discover -s tests -v
```

Keep the existing start command and persistent database path. After deployment, run in the Render shell:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite injuries
```

This reads the latest archived official PDF locally, verifies its SHA-256 against the source manifest, and stores immutable extracted page text. It makes no network requests and consumes no Odds API credits. Repeat runs reuse the stored extraction. `--full` prints every page; the dashboard also shows the extracted text. The source file's label, first observed time and unknown publication time stay distinct. The position-v2 reader applies the PDF's page/text transformations before ordering text top to bottom and left to right, including flipped coordinate systems. Monospace column spacing is approximate and wrapped reasons stay on separate visual lines; no player statuses, minutes restrictions or forecasts are inferred from it.

The user-reported Render source check on October 20, 2026 archived an official injury PDF, while scoreboard access returned HTTP 403 and historical statistics timed out. PDF availability does not resolve those separate statistics blockers. The user supplied the archived five-page PDF (SHA-256 `7caffe199749674aa1418c96ee4985d9af415d4b1615ae71620ffd9b53588a01`); its displayed layout was checked against the corrected extraction on all five pages. The original layout-v1 extraction reversed the page's rows. After deploying position-v2, rerun `injuries` once to create a corrected extraction from the same saved PDF. The previous extraction remains intact, and subsequent runs reuse the new version. No source download or provider request is needed for this upgrade.

**The user has deployed the dashboard on Render and reported adding paid compute and a persistent disk. This workspace cannot directly inspect Render billing, disk attachment, or service settings.** The supplied Compose configuration is a mock-mode starting point. Its images built successfully, and a container smoke test verified collection, dashboard responses and persistence across a dashboard restart; it has not undergone production recovery or uptime testing. It does not begin a live trial. A cloud development workspace is not an independent 24/7 production host.

## Local / cloud development

Use the existing checkout; each Codex task is isolated, so no extra Git worktree is needed. Use Python 3.12+ and install the hash-pinned requirements in a virtual environment for PDF extraction. Run tests and one mock collection, then start the worker and dashboard as separate processes. See README commands. Use an absolute persistent database path if running from another directory. The standard-library dashboard is for local development; do not expose it directly to the Internet.

## Proposed single-host mock deployment

1. Select an always-on host, budget, disk volume, backup destination, incident contact and access policy. Install Docker/Compose through supported host instructions with signature/TLS verification enabled.
2. Review `Dockerfile` and `compose.yaml`, then build and run `docker compose up -d --build`. No API secrets are needed for the mock deployment. Both services use the same named persistent volume. Verify write permissions for UID 10001 and test stop/start restoration before relying on it.
3. Inspect logs and request the mock health endpoint. Confirm data freshness and that the live report still has no trial start. Configure external monitoring through an authenticated gateway/private network. Container health checks **do not automatically restart unhealthy processes**; restart policy covers exited processes. The selected platform must alert and restart/replace persistently unhealthy services.
4. Worker makes daily online SQLite backups in `var/backups`. Copy verified backups off-host, encrypt them, restrict access, define retention and test restores in an isolated directory. Local same-disk copies are not disaster recovery. Backups currently accumulate; set a deliberate retention job with restore tests before continuous use.
5. Put the dashboard behind TLS and authentication with a reverse proxy if remote access is needed. It has no login or write API. Do not expose the default development server publicly. Add log rotation, disk/backup capacity monitoring and deployment cost monitoring.

For live collection only, securely inject `ODDS_API_KEY`, approve provider credit consumption and explicitly change the worker command to `worker --live --region REGION`. Update health checks from mock to live. Do not enable this until account-specific terms and coverage have been verified. Forecasting, settlement, trial activation and user book availability are still prerequisites; changing a worker flag does not implement them.

Use one worker per SQLite database. Multiple concurrent readers are supported; separate service startup is transaction-safe. At larger scale, migrate to PostgreSQL with migrations, idempotent jobs and the same audit constraints. Live network credentials and running processes must not be assumed to survive environment snapshots.

## Operational acceptance before a live trial

Demonstrate automatic recovery after process exits, functional health monitoring and outage notifications, quota-aware polling, permanent disk storage and offsite restore. Verify current odds, matched IDs, official source timestamps, reproducible forecasts, immutable pre-tip selection records and audited result settlement. Freeze the evaluation policy and budget. Do not declare 24/7 availability or start the 14-day clock based only on a running container.

## Current Render startup

Use the combined scheduled `web --live` command in README with the database at `/var/data/paper.sqlite`. It supersedes the earlier one-shot collection followed by `serve`. Set the platform health-check path to `/ready`; `/health` is a data-collection diagnostic and can degrade during quota pauses without making the website unavailable. The paid host manages service restarts; external uptime alerts and offsite backup automation still need configuration. Do not claim the deployed six-hour scheduler works until the user deploys this command and its scheduled activity is observed. The free API allowance is for development; timely live paper selections need a different verified data budget and validated forecasts.
