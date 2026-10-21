# Current NBA data: bounded source-access check

Run in Render's Shell after deployment:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite sources
```

No credentials are requested by this command. It never calls The Odds API and consumes zero of that account's credits. It makes at most five public HTTPS requests with 15-second per-request timeouts and bounded response sizes:

1. Today's NBA CDN scoreboard.
2. One live/final game's NBA CDN boxscore, if available. Pregame zeroes are not treated as played statistics.
3. NBA Stats player game logs for the previous completed season, February onward, to check access to a recent historical baseline source.
4. The current season's official NBA injury-report index.
5. The latest recognized official report PDF, if a link is found.

The scoreboard/boxscore URL shapes and league-game-log parameters were checked against the community `swar/nba_api` implementation. That package is not an NBA service contract, a guarantee of access, a source latency SLA or a license for upstream content. The command uses a truthful application user agent and normal TLS verification. It does not bypass source blocks, impersonate a browser, disable certificate validation or fall back to unofficial/invented results.

The workspace network blocks these NBA destinations. The local diagnostic therefore reported source errors, not successful current-data access. Render connectivity must be tested separately. A denial there may require a supported licensed data provider; do not repeatedly hammer a blocked endpoint or assume a subscription is unnecessary based only on public URLs.

## Interpretation

- The overall `completed` status means checks finished. Read each source's own `status` to determine actual access.
- A scoreboard includes its game date and an explicit Eastern-date comparison. A reachable but old board is not declared current just because it was downloaded now.
- A boxscore preserves canonical NBA game/team/player IDs, minutes, points, rebounds, assists, threes and opportunity counts. Missing fields stay null. Wrong game/team identities or duplicate player identities are rejected. Live statistics are not final results or settlement inputs. Final team minute totals are compared against 240 plus 25 per overtime, with a visible rounding check.
- The historical log query is a source-access sample, not a current-season forecast. Raw responses are preserved; source processing/first-observed times do not establish that revised historical data were available at the original game time.
- The `sources` command archives injury PDFs. The separate `injuries` command reads the saved bytes without network requests, extracts positioned text and transcribes source table labels. It reconstructs wrapped reasons and page continuations, while withholding ambiguous tables. Player and event IDs remain unresolved; rows are not current roster validation or role/minutes projections. A filename's timestamp is preserved without inventing an absolute publication time or timezone. No player injury cause is inferred from a DNP flag.
- This command does not enable automatic collection, player/event matching between providers, live models, settlement or the 14-day trial.

## Provenance and operational bounds

Artifacts are stored by SHA-256 under `/var/data/source-artifacts`, with source URL, observed time, first-observed time, HTTP date/last-modified/cache-age headers and content hash recorded in the audit database. HTTP headers are labeled as such rather than silently interpreted as source publication times. Recognized downloaded artifacts are retained even when parsing their schema fails. Error output contains controlled types/statuses, not process environment variables or credential-bearing request URLs.

Checks are reserved atomically, limited to one per five minutes, and require adequate disk headroom. These are manual diagnostics, not a promise that public endpoints support a particular polling rate. The live dashboard has a separate **Current NBA source checks** section; mock and paper-performance records remain separate.

## Next implementation after actual access is established

Verify provider terms and allowed polling rates, check freshness and historical coverage, normalize the historical minutes/stat fields, parse official injury evidence with supporting text, and build tested identity joins. Then evaluate a recent chronological baseline before enabling current forecasts. If these sources are blocked or insufficient, select a supported statistics/injury provider with demonstrated coverage rather than assuming the 2015 archive or incomplete live boxscores solve the requirement.
