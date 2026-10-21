# Recent provider data: reconciliation and baseline evaluation

After deployment, run these commands in **Render → NBA-model → Shell**:

```bash
python3 -m nba_bot --db /var/data/paper.sqlite stats-import --scope history-games
python3 -m nba_bot --db /var/data/paper.sqlite research-check
```

The first command imports the historical game schedule for the **same** season,
date range and non-postseason filter as the existing player-stat history. It uses
the existing paginated importer, rate/disk guards, source archive and checkpoints.
It does not redownload the 17,904 previously imported player-stat rows. Completed
historical schedule snapshots are reused unless explicitly refreshed. These
requests use BALLDONTLIE; they consume no Odds API credits.

`research-check` makes **no API calls**. It reads saved snapshots, reconciles
provider coverage and evaluates fixed baselines if the data pass. Results appear
under **Recent baseline research** on the live dashboard. Mock mode excludes
these real-data results. The earlier study ending in 2015 is retained separately.

## What is reconciled

The latest completed team/active-roster snapshots identify a 30-franchise scope
in the provider's namespace; all referenced teams must be present in the team
catalog. That scope is not an independent official NBA identity/coverage check.
Historical players remain eligible even if they are no longer active now. Changes
to franchise membership are not modeled. Nonfranchise games/rows, including any
All-Star-type teams outside that scope, are counted separately as exclusions.

Within that scope, the check requires final schedule games to have player records,
consistent game/date/opponents/season/score/period metadata, at least five players
with positive reported minutes per team, and known player point totals that sum to
the source final scores. It flags stat games absent from the final schedule,
nonfinal schedule games, duplicate player/game rows, conflicting embedded game
snapshots, missing core fields on played rows and impossible points/threes or
rebound components.

Team minutes are compared with 240 plus 25 per overtime, using the source period.
The tolerance is one reported unit per positive-minute record plus one team minute:
whole-minute labels allow one minute per record; second-format labels allow a
second; decimal-minute labels allow their last reported unit. This acknowledges
precision rather than pretending whole-minute fields contain exact seconds.
It can miss small omissions, particularly non-scoring short appearances. Passing
these checks therefore means **provider reconciliation passed**, not independently
verified complete NBA coverage. Unknown values are not silently converted to zero.

Data-quality failures produce a saved `blocked_data_quality` result with issue
counts and bounded examples. They do not produce evaluation metrics. Incomplete
imports or mismatched query windows return `not_ready`. No result is presented as
current for a newer import version without a recheck.

## Fixed chronological study

For the 2025 season window imported here, February 2026 supplies warm-up history,
March is validation, and April through the end of the requested non-postseason
window is final evaluation. The split and parameters are fixed before running the
user's actual evaluation. Validation metrics are reported without selecting or
tuning a winner on that period. Any future tuning must not be advertised as an
untouched evaluation on the same final data.

Every date's forecasts are frozen before **any** result from that date is added
to model state. Earlier evaluation dates can update later dates under the same
fixed learning rule. Changing a target's minutes or later-day results cannot
change that target's forecast or earlier forecasts. This is a retrospective
sequential study: the provider snapshots were collected after the games, so it
does not reproduce archived information availability, revisions or executable
pre-tip forecasts.

Game baselines reuse the already reviewed mechanics: expanding prior home-win
rate and Elo (1500 start, K=20, 65-point home advantage). Rolling team scoring is
also reported after ten prior team games, compared with prior league scoring.
Winner accuracy measures **game winners**, not spread/total performance, selected
bets or a 65% target. Brier score and log loss assess the game probabilities.
The imported February start means these models have partial-season warm-up, not
the entire season's prior state.

Player baselines score minutes, points, rebounds, assists and made threes:

- Prior league mean per recorded positive-minute appearance.
- Mean of the player's previous ten eligible appearances.
- Projected minutes from the previous five eligible appearances, multiplied by
  per-minute rates from up to twenty earlier eligible appearances. This differs
  from a simple mean because the minutes and rate windows differ.

All models are compared on the same records, requiring ten earlier eligible
appearances and complete target fields. MAE, RMSE and signed forecast-minus-actual
bias are reported, with record counts and warm-up exclusions. Zero/unknown-minute
records are counted separately and excluded; their participation or injury cause
is not inferred. The resulting study conditions on recorded appearances. It does
**not** predict whether someone will play, learn lineup/role changes, guarantee
team-consistent minutes or supply calibrated prop probabilities.

## Audit and limits

Reports are keyed by research version, fixed parameters and immutable snapshot
run IDs. Repeating the same check reuses the stored result. New data/versions
append results instead of replacing earlier research. Per-game and eligible
per-player historical forecasts/actuals are kept in dedicated append-only research
tables, never inserted into the live forecast, selection or betting ledger.
Report/prediction writes are one transaction. No credentials are needed for the
local evaluation.

There are no historical sportsbook lines/prices, market-only benchmark, historical
pregame injury feed, profit/ROI, selected-bet win rate or confidence intervals in
this study. Results cannot establish a betting edge or an operational live model.
No current forecasts, real-money bets, automatic settlement, faster paid polling
or 14-day trial are enabled by either command.
