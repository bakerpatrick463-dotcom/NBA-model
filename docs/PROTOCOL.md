# Paper ledger and evaluation

Policy `paper-v1-ev05-one-per-game` is frozen: eligible full-game traditional sportsbook quotes with age <=120 seconds; EV >=0.05 per unit; available books only; select highest EV once per game; ties use stable alert IDs. All qualifying alerts remain separate from selected bets. The prototype permits only simulated forecasts. Different policy/model versions must be explicit code/config releases and evaluated separately.

Every bet risks exactly one unit. Original quotes, forecasts, assumptions, evidence, alerts and selections are append-only. A selection must satisfy quote time <= collection time <= selection time < tipoff; evidence must have been processed by selection. No retrospective picks. There is no automatic re-selection after a void. Zero-selection days are valid. Local DB backups preserve pending records across restarts.

Settlement is currently manual and audited:

```bash
python3 -m nba_bot settle BET_ID win --reason 'Verified final result' --source 'https://trusted-results-source/example'
```

Supply actual evidence, not the example URL. Settlement before recorded game start is blocked. The operator must verify final status and book rules (DNP, overtime, canceled/postponed events, stat corrections) before entering a result. A postponement can remain `pending`; a cancellation or rules-based refund may be `void`. A correction appends another settlement with a reason and source; it never edits the old entry. A new `pending` entry can reopen an incorrectly settled bet. Timestamp/history are preserved; event rescheduling requires future schedule-version support before live use. Manual settlement accuracy is not automatically verified by this prototype.

Profit: win = decimal odds minus one, adjusted for commission; loss = -1; push/void = 0. Win rate = wins/(wins+losses). ROI = net units/(wins+losses+pushes), because each is one unit risked. Void and pending stakes are excluded. Average odds covers all selected bets. Calibration bins show conditional non-push win frequency and sample size; no statistical confidence is implied. Drawdown uses latest corrected results ordered by settlement timestamp and is restated after corrections, not an as-originally-published equity history. Weekly groups use ISO UTC weeks; daily groups use UTC dates. Operating costs are excluded and currently unmeasured.

Closing-line movement is **unavailable**, not zero. Future implementation should use the final eligible pre-tip quote with matching book/event/market/subject/period/settlement rules: odds movement at the same line is expressed as closing no-vig probability minus selection no-vig probability; line changes should be reported separately and require a probability model to compare. Do not substitute the latest arbitrary quote as a closing price.

## Fourteen-day live protocol (not started)

The clock may start only when live collection, a frozen validated forecast/selection policy, immutable logging and verified settlement work end to end. This code has no command to assert trial activation and always reports no trial start. A future activation record must be auditable and exclude mock fixtures. Require 14 consecutive operational calendar days; outages and missing data must be visible, with an explicit restart/extension decision. Include zero-bet days. Afterward review results and operations; never automatically switch to real-money execution.

A 65% win rate is only a target. Report sample sizes, odds, ROI, drawdown, calibration, missingness and correlated uncertainty. Do not boost hit rate by choosing favorites unrelated to value. Two weeks alone is unlikely to establish a reliable edge.

## Future-data validation prerequisites

Use archived as-observed data and realistic polling/processing delays. Split chronologically, tune only on earlier intervals, and lock a final untouched evaluation period. Compare market-only and historical baselines against role-enhanced models. Estimate uncertainty with game-level clusters; duplicate quotes across books are not independent predictions. Validate player/teammate/team consistency before relying on joint simulations. Current synthetic examples and unit tests do not constitute a backtest or evidence of predictive performance.
