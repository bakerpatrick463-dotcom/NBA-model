# BALLDONTLIE NBA connection

The user reports subscribing to NBA ALL-STAR. The supplied provider table lists
teams, players, games, active players, game-player statistics and player injuries
in that tier, with 60 requests/minute. That is an account request limit, not an
upstream refresh interval or latency promise. Live account access was subsequently observed as documented below; full coverage
and provider update latency remain unverified.

The user's October 21 Render diagnostic subsequently confirmed successful access
to all five endpoints: 89 team records (not 89 current NBA franchises), 100 active
players with a continuation cursor, 13 games, five usable final historical stat
rows and 80 injury records. Those response samples establish access, not complete
coverage or source latency.

## Resumable roster, schedule and history imports

After the new deployment says **Live**, run in Render's **Shell**:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite stats-import
```

The command saves teams, active-player pages, a five-day schedule window centered
on today's Eastern date, and paginated player-game history from February–June of
the previous completed season. It does not download a full multi-season archive.
Each completed page is committed before the next request. Progress is printed
with flushing so the shell shows activity. Expect several minutes; keep the shell
open until the final JSON result. The dashboard also shows saved page/row counts.

By default one invocation stops after at most 300 requests. If it stops at the
page limit or is interrupted, run the same command again to resume. A second
concurrent importer for that database is rejected. Imports and `stats-check`
share a persistent ceiling of 30 requests in a rolling minute, below the reported
ALL-STAR limit; import pages are also spaced by 2.1 seconds. HTTP/schema/storage
errors stop the import without retrying that page. Successful earlier pages and
original raw artifacts remain intact. A 429 pauses resumption for at least a
minute. Other applications can still consume the account's own allowance.

`--scope roster`, `--scope games`, `--scope teams` or `--scope history` imports only
that dataset. `--max-pages N` sets a 1–500 request bound. `--refresh` creates new
snapshots without deleting/replacing old data. Completed historical snapshots
are reused by default; roster/team snapshots are refreshed after 24 hours, and
the schedule is scoped to its frozen requested dates. Reuse is snapshot-specific,
not a guarantee that a live provider's data have stopped changing.

Every page has an explicit terminal basis: nonpaginated teams, an explicit null
cursor, or a short page with no continuation cursor. A full page missing its
cursor, a nonadvancing cursor, duplicate provider IDs, or repeated game/player
statistics across pages stops processing. Reaching the provider's pagination end
is labeled separately from **coverage reconciliation**, which is still pending.
It does not prove every player/game is present or validate a predictive baseline.
Disk headroom is checked before every request. Raw files are published atomically
from flushed temporary files, so an interrupted write cannot become a partial
canonical artifact. Normalized objects, pages, import events and prior versions
are append-only. No Odds API credits are used; its polling schedule is unchanged.

After imports, a local identity check uses the newest finished team/roster/game
snapshots and structured official injury report. Snapshot/report observations
must be at most 24 hours old. It requires exact normalized first and last name,
current team, game calendar date and ordered opponents. It supports only one
explicit team-label alias (`LA Clippers` / `Los Angeles Clippers`); it never
removes player suffixes or guesses fuzzy/ambiguous matches. Missing/ambiguous
records remain unresolved, and `NOT YET SUBMITTED` stays a team-report notice.

Matches are stored separately with their source PDF hash and snapshot run IDs.
They identify **BALLDONTLIE** records, not canonical NBA IDs or Odds API events.
Original injury rows/IDs/statuses are never rewritten. A matched name does not
verify fresh availability, return dates, participation or workload. Identity
results, import progress and unresolved cases appear in a separate dashboard
section; simulated mode excludes them. Statistical coverage checks, a recent
chronological baseline, cross-provider event mapping and evaluation remain
prerequisites for predictions. No trial, settlement or live selection is enabled.

## Connect on Render

1. Open **NBA-model → Environment → Add Environment Variable**.
2. Name it `BALLDONTLIE_API_KEY`. Paste the account's key into the secret value
   field. Never paste the key in chat, GitHub, repository files or shell commands.
3. Save and deploy. Keep the existing build command, start command and disk.
4. After the deployment says **Live**, open **Shell** and run:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite stats-check
```

The live dashboard's **NBA statistics connection** section displays the latest
result. `configuration_required` means the environment variable is absent or
invalid. HTTP 401 suggests an authentication problem; HTTP 403 on a paid endpoint
may require checking the account's NBA subscription. HTTP 429 stops the remaining
requests without retrying. `completed` only means the diagnostic finished: inspect
each endpoint's status for access, schema errors or empty results.

## What the check reads

One explicit invocation makes at most five sequential requests, with no retries:

- `/nba/v1/teams`: provider team identities.
- `/nba/v1/players/active`: up to 100 active-player records from one response page.
- `/nba/v1/games`: one page for today and tomorrow's dates in America/New_York.
- `/nba/v1/stats`: up to five game-player records from February–June of the
  previous completed season, excluding postseason, to check historical access.
- `/nba/v1/player_injuries`: up to 100 records from one response page.

Checks are reserved atomically and deferred for five minutes after an attempt,
including across process restarts. Authentication and rate-limit errors stop
subsequent requests. This diagnostic consumes **zero Odds API credits** and never
changes that collector's schedule, reserve or ledger. It is not scheduled by the
worker or triggered by dashboard refreshes. Other clients using the same account
can still consume its provider request allowance.

Pagination state is explicit. Missing pagination metadata means unknown
completeness; a page is not presented as an entire current roster or a complete
historical training feed. Full pagination, coverage reconciliation, verified
cross-provider identity joins and a recent chronological baseline are subsequent
implementation work. No current forecasts, selections, automatic settlement or
14-day trial are enabled by this check.

## Integrity and interpretation

Authorization uses the HTTPS request header, never URL parameters. Redirects are
not followed. There is no alternate host or TLS bypass. Authentication errors are
sanitized; error response bodies and keys are not logged or archived. Successful
response bytes are archived by SHA-256 in the existing `source-artifacts` directory,
including responses whose data schema fails normalization. Requests have 15-second
timeouts and a 2 MiB response cap, with disk headroom checked before reservation.
The database stores append-only results and artifact first-observed timestamps.

All IDs retain the **balldontlie** namespace; they are not NBA or Odds API IDs.
Historical row teams must match that row's game; a player's current team can differ
after a trade. Missing statistics stay null and zero stays zero. Recognized DNP
labels produce unknown minutes, not an inferred injury. Only final rows with
minutes/points/rebounds/assists/threes present count toward the sample's required
baseline-field check. They are still a sample, not a trained or validated model.

Source calendar dates are not assumed to be UTC tipoff times. HTTP dates and
observation timestamps do not establish source freshness, historical availability
at tipoff or bookmaker-to-provider latency. Injury labels and estimated return
dates do not establish verified participation, workload or minutes restrictions.
The provider's terms and any reuse restrictions still apply.

## Interface evidence

Paths, query parameters, raw `Authorization` header format and expected response
fields were checked against the published BALLDONTLIE Python SDK 0.1.6 on PyPI
(`balldontlie/nba/api.py`, `models.py`, `client.py`). The inspected wheel SHA-256 is
`3baa021bd6346fe129f674a5fedd9e5e2bc0a1dcfd1b3a3291934068959e27a2`.
The application uses the Python standard library directly; it does not install
that SDK. Local tests use synthetic responses to verify transport safeguards,
normalization, pagination, provenance and persistence. Those tests do not prove
the user's live account entitlements, historical completeness or update latency.
