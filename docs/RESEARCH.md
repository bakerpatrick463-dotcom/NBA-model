# Historical baseline study v1

This is a mechanics study on real historical results, **not current NBA forecasting and not paper-betting performance**. It makes no provider API calls and produces no alerts or selections.

## Source and rights

Source: FiveThirtyEight, Historical NBA Elo dataset, game information originally from Basketball-Reference.com.

- Dataset documentation: https://github.com/fivethirtyeight/data/tree/master/nba-elo
- Reviewed CSV: https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-elo/nbaallelo.csv
- Repository license: https://github.com/fivethirtyeight/data/blob/master/LICENSE
- License: Creative Commons Attribution 4.0 International, https://creativecommons.org/licenses/by/4.0/
- Reviewed content SHA-256: `d46ed3540ee8d9eca31b3e94cc8c777e0be5156173d814ebf65b8195e8d616bc`.

The source and license were fetched with normal TLS verification. The hash records the reviewed snapshot; it is not a separately published upstream authenticity signature. The raw CSV is not committed. The bundled JSON contains derived aggregate metrics, attribution and transformations. The source archive ends in 2015; do not use it to forecast 2026 games.

## Reproduce

No package installs or credentials are needed. From the repository root:

```bash
python3 -m nba_bot.history fetch var/research/nbaallelo.csv
python3 -m nba_bot.history evaluate var/research/nbaallelo.csv --output var/research/evaluation-v1.json
```

Commands refuse to overwrite an existing archive/report. A changed archive hash requires investigation instead of accepting changed content silently. The checked summary is packaged as `nba_bot/research_summary.json`; the dashboard reads it without downloading or reevaluating the archive on each visit.

## Predeclared methods

Filter to NBA regular-season home/away games, season 2000 onward. Use `_iscopy=0` exactly once per game; exclude playoffs and neutral venues. Reject duplicate canonical IDs. Team IDs retain the source's historical names and are not mapped to current rosters.

- **Expanding home-win rate:** historical home wins divided by prior games, with a Beta(1,1) starting prior.
- **Elo:** initial 1500, K=20, home advantage 65 Elo, each new season retaining 75% of deviation from 1500. Elo is recomputed from past results. The source's precomputed `forecast`, `elo_i` and postgame `elo_n` columns are not model inputs.
- **League scoring baseline:** prior games in the same season establish points per team, plus/minus 1.5 for home/away.
- **Rolling team scoring:** combine each team's points scored and its opponent's points allowed over the last 20 same-season games, plus/minus 1.5 for home/away. Require ten earlier games per team. Compare scoring baselines on the same eligible game subset.

Parameters were fixed before viewing evaluation results. Seasons 2000–2013 form the warmup/training history, 2014 is a chronological validation period, and 2015 is the final sequential evaluation. Model state may update after completed evaluation games, but parameters do not retune. All forecasts for a day are frozen before **any** result from that day is incorporated. A team starting a new season needs ten completed games before scoring forecasts resume; skipped games are reported.

## Interpretation and limitations

The 2015 sample has 1,228 home/away regular-season games; neutral-site games are excluded. Scoring baselines are evaluated on 1,069 games; 159 lacked sufficient same-season team history. Elo game-winner accuracy was approximately 67.8%, compared with 57.5% for the expanding home-rate baseline. **These are game-winner predictions without sportsbook prices; they are not a betting win rate, spread performance, profitability, or evidence that the user's 65% betting target has been met.**

Lower Brier score and log loss are better. The study's scoring errors are useful baseline benchmarks, not calibrated live probability distributions. It has no market-only comparison because archived as-observed odds are absent. It has no player props, minutes restrictions, injuries, current rosters or press-conference features. The archive contains revised final results rather than historical first-observed timestamps, so this is not a fully executable as-of backtest. Shared-game duplicate rows are not treated as independent predictions.

Current-season statistics, time-stamped injury/role evidence, identity matching, calibrated outcome distributions, market comparison, and automated settlement remain prerequisites for live paper selections. The 14-day trial remains inactive. Other sources were investigated (hoopR's ESPN/NBA-source data pipelines); their current-season coverage and underlying reuse terms were not established, so they are not silently substituted into production.
