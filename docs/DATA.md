# Data and coverage

## Current coverage, accurately stated

| Source/product | Implemented | Verified live coverage |
| --- | --- | --- |
| `demo_book_a`, `demo_book_b` | Simulated traditional price fixtures | None: fictional books, teams and player |
| The Odds API | Collection-only NBA spreads/totals adapter; additional normalizers | None: no credentials, subscription or authenticated request tested |
| Official NBA injury reports | Research lead only | No ingestion |
| Press conferences/transcripts | Evidence schema and synthetic example | No recording, transcription or extraction |
| Exchanges / pick'em | Rejected for selection | None; require different payout and settlement models |

We do not scan “every sportsbook.” Broad coverage must be demonstrated with an account-specific coverage inventory, book identifiers, geographical restrictions, supported markets/periods, suspension behavior, quote timestamps, price updates, historical availability and settlement rules. Reference books and user-available books must be distinct lists. Mock selection uses an explicit two-book allowlist; no live availability list has been configured.

## Live prerequisites

1. Choose a provider and review its license, automated-use rights, permitted retention/display, current pricing and limits before paid collection. No budget is approved and no subscription has been purchased.
2. Confirm jurisdiction and which traditional books the user can actually access. Do not infer availability from a provider's region list.
3. Supply `ODDS_API_KEY` securely through environment settings for the collection-only adapter. The application reads that name; never paste a key into chat, commit it, or print request URLs. The key was absent during initial validation. The provider uses a query parameter, so avoid URL logging in infrastructure as well.
4. Permit `api.the-odds-api.com` for requests, `the-odds-api.com` for documentation, and `official.nba.com` for official source research. Further official report PDF hosts must be identified and reviewed when implementing ingestion.
5. Test `python3 -m nba_bot once --live --region us` only after approving collection costs. This command collects spreads/totals, stores provider quotes and does **not** generate live selections. A live worker continuously consumes quota; do not start it until limits are confirmed. The per-event player-prop endpoint is not called by this prototype.
6. Resolve player/event IDs against trusted roster/schedule data, verify per-book settlement rules, add licensed historical observations/results, and evaluate a time-correct forecasting baseline before enabling live paper selections.

## Sources requiring verification

- https://the-odds-api.com/liveapi/guides/v4/
- https://the-odds-api.com/sports-odds-data/bookmaker-apis.html
- https://the-odds-api.com/sports-odds-data/update-intervals.html
- https://official.nba.com/

During initial setup on 2026-10-20 these public documentation requests failed at the network proxy with `403 Forbidden` on the tunnel. Current terms, pricing, sportsbook counts, 60-second prop update claims, market access and credit charging therefore remain **unverified**. Do not use older planning allowances as quotes or coverage promises. The adapter is fixture-tested, not authenticated against current live responses.

## Evidence requirements for the next iteration

The evidence store supports source URL, original supporting text, speaker, event date, published, first-observed and processed timestamps. Future collectors must preserve source provenance and deduplicate reused stories/clips independently of processing time. Extract availability, restricted minutes, lineups, closing role, offensive responsibility, workload and defensive scheme with supporting text; unsupported or ambiguous claims remain unknown. Language models may extract evidence but must not invent probabilities.

Historical patterns must distinguish rest/injury/foul trouble/blowouts/coaching and regime changes. Use shrinkage for small samples, player-specific baselines, and explicit normal/restricted/absent scenarios. No such live signal is currently claimed.
