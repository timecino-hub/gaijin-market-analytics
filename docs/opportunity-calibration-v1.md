# Opportunity Calibration Backtest V1

Round 5 adds a pure, deterministic walk-forward calibration engine for
`OpportunityScoreV1`. It complements the existing single-item reference-price
backtest; it does not replace that engine or change the public leaderboard.

The calibration engine answers a narrower question:

> When the V1 score was calculated using only information available at a
> historical cutoff, did higher scores correspond to better after-fee outcomes
> during later holding windows?

It is not a trading simulator, execution guarantee, or recommendation system.
All fills are explicitly labeled quote-based proxies.

## Versions and reproducibility

```text
engine_name = opportunity_calibration
engine_version = 1.0.0
execution_policy_name = repeated_quote_one_unit
execution_policy_version = 1.0.0
```

Every result also records the analysis strategy, opportunity strategy and
feature versions, fee-policy version, market-rules version, a deterministic
SHA-256 of the input histories, and a SHA-256 of the calibration configuration.
No result reads the clock or environment implicitly.

## Point-in-time boundary

For cutoff `T` and lookback horizon `L`, analysis and scoring receive only:

```text
[T - L, T]
```

Future evaluation receives only:

```text
(T, T + H]
```

where `H` is each configured holding horizon. The same point-in-time score can
be evaluated against several forward horizons. Observations after `T` are never
passed to `RuleBasedV1` or `OpportunityScoreV1`.

Each item determines forward-window completeness from its own latest timestamp.
Incomplete windows are explicit. They are excluded by default, or may be kept
as partial diagnostics when `require_complete_forward_window` is disabled.

## Temporal splits

`TemporalSplitConfig` assigns each cutoff to exactly one time-ordered split:

- train: cutoff at or before `train_end_at`;
- validation: cutoff after train and at or before `validation_end_at`;
- test: cutoff after validation.

The engine does not fit or optimize weights. The split labels exist so any
later calibration decision can be made on train/validation data and judged on
an untouched out-of-time test period.

## Entry proxy

A one-unit counterfactual entry uses the current valid ask at the cutoff. Entry
is available only when the latest consecutive valid ask observations confirm
that price or a better price at least the configured number of times.

A missing, invalid, or higher ask breaks the confirmation streak. Future asks
cannot confirm a historical entry. Aggregate screenshot quantities are not
interpreted as executable depth.

The engine evaluates counterfactual entry outcomes across the full score range.
`opportunity_eligible` remains separate, and portfolio metrics use only trades
that passed the V1 eligibility gate.

## Exit proxy

The preferred exit is the historical reference sell price calculated at the
cutoff. It is treated as reached only after the configured number of
**consecutive future snapshots with valid bids** are at or above the target. A
single spike, a missing bid, an invalid bid, or a lower bid breaks the streak.

When the target is not confirmed, V1 may perform a terminal liquidation at the
last valid bid in the holding window. The quote must be no older than the
configured terminal-snapshot age relative to the window end. Otherwise the
exit remains unresolved. Terminal liquidation can be disabled.

When the target is confirmed, the simulated listing price is the target itself,
not the potentially higher observed bid. This avoids crediting extra upside
that the historical signal did not request.

All seller proceeds use `GAIJIN_MARKET_FEE_POLICY_V1`: 15% nominal fee and
seller proceeds rounded down to 0.01 GJN. Net profit and ROI remain Decimal.

## Metrics

For each holding horizon and every split/horizon cohort, the engine reports
explicit denominators and includes:

- scored, eligible, entered, realized, target-exit, terminal-exit, and unresolved counts;
- entry, realization, and positive-return rates;
- total profit, mean/median ROI, and mean/median holding time;
- eligible-only realized counts, win rate, and profit;
- peak concurrent one-unit entry cost;
- ending equity from eligible realized trades;
- maximum drawdown and drawdown rate using the historical peak at the time of
  each drawdown.

The portfolio view is unconstrained and opens one unit for every eligible
historical signal. It does not model order priority, limited account capital,
partial fills, or reinvestment.

## Score calibration

Score bins default to:

```text
[0,20), [20,40), [40,60), [60,80), [80,100]
```

Each bin reports case counts, eligible counts, realized outcomes, positive
return rate, and mean/median ROI. The engine also reports Spearman rank
correlation between future realized ROI and:

- total opportunity score;
- profitability component;
- liquidity component;
- stability component;
- evidence-confidence component;
- freshness component;
- risk penalty.

The profitability component is the explicit simple baseline. The result reports
whether the composite score's rank correlation exceeds that baseline and the
fraction of adjacent populated score bins whose mean ROI is non-decreasing.

These statistics diagnose the current `35/25/15/15/10` weights; they do not
automatically change them. A new score or feature version is required for any
weight or eligibility change.

## Optional historical-trade evidence

Round 5C can attach `TradeEvidenceCalibrationResult` diagnostics to
the unchanged quote-only result. Completed `1h` buckets are preferred and
completed `1d` buckets are used only as a non-overlapping fallback. The
diagnostic reports quote-only, trade-supported, and combined evidence views; it
never treats bucket VWAP as proof of a user's fill. See
`docs/historical-trade-calibration-v1.md`.

## Interpretation limits

- Quote persistence is not proof of execution.
- Screenshot quantity is not executable depth or traded volume.
- Sparse snapshots can miss both favorable and adverse intraperiod prices.
- Overlapping cutoffs produce overlapping hypothetical trades.
- Multiple forward horizons are alternative evaluations of the same signal and
  must not be combined as if they were independent positions.
- Correlation and monotonicity require adequate samples, especially in the test
  split.
- No metric is a profit probability or guarantee.
