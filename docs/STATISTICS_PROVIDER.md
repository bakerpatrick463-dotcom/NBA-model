# BALLDONTLIE NBA connection

The user reports subscribing to NBA ALL-STAR. The supplied provider table lists
teams, players, games, active players, game-player statistics and player injuries
in that tier, with 60 requests/minute. That is an account request limit, not an
upstream refresh interval or latency promise. Live account access, returned field
coverage and timestamps must still be observed on Render.

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
