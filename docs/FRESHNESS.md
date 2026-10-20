# Fast data collection: prepared, not enabled

The six-hour development schedule does not meet timely betting-research needs. There are three different delays: sportsbook-to-provider delivery, provider-to-collector polling, and collector-to-browser refresh. Improving one does not remove the others. No globally fastest provider or end-to-end latency guarantee has been established.

## What is implemented

- `ODDS_REFRESH_PROFILE=development` (default): existing six-hour collector and free-plan guards.
- `ODDS_REFRESH_PROFILE=fast` (opt-in): 60-second polling when upcoming NBA games are present in the latest snapshot; otherwise 30 minutes. This covers only the **spreads and game totals** already implemented. It does not add player-prop, injury, live box-score or transcript collection.
- Fast mode activates only when the provider's `x-requests-used` plus `x-requests-remaining` reports sufficient capacity: at least 100,000 credits for the present one-region/two-market workload. The requirement grows for multiple regions or a higher observed request cost. Otherwise the app remains on the development schedule and displays that fast mode is waiting for quota confirmation. Enabling an environment variable does not purchase a plan.
- Fast requests have a 1,000-credit reserve, at most 1,440 attempts per rolling day, persistent reservations across restarts, and bounded retry spacing. Development retains its existing 100-credit reserve/six-attempt cap. Missing remaining-credit headers pause collection instead of assuming unlimited access.
- Upgrading an account does not retroactively update an old saved quota response. Its next development request (within six hours) can discover the new capacity, then activate the requested profile. Check the dashboard's effective profile and last quota observation; do not assume a purchase alone activated fast polling.
- Database snapshots use an index so the dashboard reads the latest prices without scanning every historical quote. An inexpensive stored-data change check runs every five seconds while the browser tab is visible; the full table is fetched when data changes. These browser checks do not call the paid odds provider. Quote status ages locally between full responses.
- The dashboard reports median/p95 age of the provider quote timestamp at collection. That is **not** a measurement of true sportsbook-to-provider delivery latency; an unchanged price's timestamp may be old for legitimate reasons.
- Collection pauses if disk space cannot accommodate another database-sized backup plus 64 MiB of headroom. This is a protective stop, not storage sizing or offsite backup automation.

## Credit arithmetic, not a price quote

Assuming two credits per request for one region and the existing spreads/totals markets:

| Poll interval, if sustained 24/7 | 31-day credits |
| --- | ---: |
| 30 seconds | 178,560 |
| 60 seconds | 89,280 |
| 120 seconds | 44,640 |
| 300 seconds | 17,856 |

No-upcoming-game polling may consume less; retries, other uses of the same account and additional markets consume more. One bulk request contains many books/games, so 704 returned quotes are not 704 billed requests. Use provider-reported request costs and remaining quota rather than assuming these example costs forever.

The user reported pricing of 20,000 credits for $30/month and 100,000 credits for $59/month on 2026-10-20. Those are user-observed listings, not independently verified plan terms. A 20,000-credit plan cannot sustain one-minute two-market requests around the clock. A 100,000-credit plan supports the example two-market workload but is not automatically sufficient for player props across every game. Per-event prop requests and their current charges/access still need verification. No paid odds subscription purchase has been confirmed in this workspace.

## Prerequisites before enabling fast mode

1. Verify automated-use/display terms, FanDuel/US-VA market coverage and the paid account's effective quota. The user supplied the official update-interval screenshot described below. A faster subscription does not necessarily mean a faster underlying feed.
2. For sub-minute source delivery, evaluate licensed streaming/push-feed options with a trial and measured delivery times. SportsDataIO and Sportradar documentation are research leads; no access, implementation or fastest-vendor claim is established.
3. Size storage for the chosen observation cadence and retention. The initial 1 GB disk is **not sized for sustained minute-by-minute quote history plus seven growing database copies**. Raw snapshot history grows continuously. Arrange larger or external historical storage/offsite backups and monitor capacity before enabling faster collection. A low-space pause prevents collection instead of deleting audit history.
4. Only then set `ODDS_REFRESH_PROFILE=fast` in Render and save/deploy. Keep the existing `web --live` command and persistent database path. Verify the effective profile, confirmed quota, source timestamps, request intervals and disk usage. This document is preparation, not confirmation that fast polling is deployed.

Current official documentation URLs:

- https://the-odds-api.com/sports-odds-data/update-intervals.html
- https://the-odds-api.com/liveapi/guides/v4/
- https://the-odds-api.com/
- https://sportsdata.io/live-sports-data-api
- https://developer.sportradar.com/basketball/docs/nba-ig-push

All five vendor-site checks were blocked from the workspace by proxy tunnel 403 on 2026-10-20. The user subsequently provided a screenshot of the official Odds API update-interval page: featured markets advertise 60-second pre-match / 40-second in-play updates; additional markets including player props advertise 60 seconds for both. Its footnote says update intervals begin decreasing six hours before start toward the in-play interval. These are provider-advertised source refresh intervals, not measured end-to-end latency or an SLA. Exchanging a free plan for more credits does not itself establish faster source delivery. The Odds API's official Python sample README was accessible and confirmed its basic v4/used-and-remaining workflow, but did not establish current latency, pricing or prop terms. Do not invent precise source update rates or quote a vendor as the fastest from this evidence.

## Current-data coverage gaps

The existing historical study ends in 2015 and is not used for current forecasts. Current player/team statistics, official injury reports, role/news evidence, provider-to-book identity/settlement validation and automated results remain separate requirements. Fast odds polling alone does not implement them, validate a betting edge or start the 14-day trial.
