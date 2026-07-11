# Historical Trade Evidence Calibration V1

Round 5C adds an optional, pure analytics diagnostic to the existing
`OpportunityScoreV1` walk-forward calibration. It does not replace the
quote-only baseline and does not change `OpportunityScoreV1`, its eligibility
rules, or its weights.

The diagnostic answers a narrower question:

> After a score was calculated at cutoff `T`, did later completed historical
> trade buckets support the target price, and how does that evidence compare
> with the existing repeated-quote proxy?

A supporting bucket is evidence of market activity, not proof that a user's
order filled or held queue priority.

## Versioned contract

```text
engine_name = historical_trade_evidence
engine_version = 1.0.0
policy_name = completed_bucket_vwap_support
policy_version = 1.0.0
```

The base calibration remains:

```text
engine_name = opportunity_calibration
engine_version = 1.0.0
execution_policy_name = repeated_quote_one_unit
execution_policy_version = 1.0.0
```

When no historical trade histories or evidence configuration are supplied,
`run_opportunity_calibration` returns the original quote-only result and
`trade_evidence` is `None`.

## Input semantics

Each `HistoricalTradeBucket` must contain:

- an item identifier;
- `1h` or `1d` granularity;
- an aligned UTC bucket start and matching duration;
- positive Decimal VWAP;
- positive reported trade volume;
- price semantics
  `bucket_volume_weighted_average_trade_price`;
- volume semantics `reported_trade_volume_unknown_unit`.

The volume is additive but its physical unit remains unconfirmed. Thresholds
therefore use the neutral term **reported trade-volume units**.

## Point-in-time boundary

For a cutoff `T` and forward horizon ending at `E`, a bucket can support a
future target only when:

```text
bucket_start_utc >= T
bucket_end_utc <= E
```

A bucket that straddles the cutoff is excluded because it can contain trades
from before `T`. A bucket that has not fully ended by `E` is also excluded.

Market-phase diagnostics use only buckets that fully ended at or before the
cutoff. Future shocks cannot alter the phase assigned to an earlier case.

## Granularity selection

`1h` and `1d` buckets are never counted together for the same UTC day.

- completed `1h` buckets are preferred;
- a completed `1d` bucket is used only when that UTC day has no eligible `1h`
  bucket;
- even partial hourly coverage suppresses the daily bucket for that day,
  favoring non-duplication over higher recall.

This rule is deterministic and conservative.

## Evidence levels

Each calibration case records independent booleans and one strongest label:

1. `quote_touch`
   - the original repeated future-bid confirmation reached the target;
2. `trade_vwap_support`
   - a selected completed trade bucket had VWAP at or above the target;
3. `trade_volume_support`
   - VWAP supported the target and the bucket met the configured minimum
     reported trade-volume threshold;
4. `unresolved`
   - neither quote nor trade evidence supported the target.

`combined_support` is true only when both quote and trade VWAP evidence exist.
The strongest label prefers volume support, then VWAP support, then quote touch.
The separate booleans preserve all overlapping evidence.

A VWAP at or above a target implies that at least some weighted trade activity
occurred at or above that level, but it does not reveal bucket high/low,
transaction order, limit-order queue position, or whether a specific order
filled.

## Counterfactual return handling

For supported diagnostics, the hypothetical exit remains the historical target
price calculated at the cutoff. The engine never substitutes a higher bucket
VWAP and therefore does not credit unrequested upside.

Seller proceeds still use `GAIJIN_MARKET_FEE_POLICY_V1`:

- nominal fee: 15%;
- seller proceeds rounded down to 0.01 GJN;
- Decimal arithmetic throughout.

These returns are support-conditioned counterfactuals, not execution claims.

## Separate result views

Every split/horizon cohort is reported under three views:

- `quote_only`: target supported by repeated quote observations;
- `trade_supported`: target supported by a completed trade VWAP bucket;
- `combined`: both quote and trade support are present.

Each view reports explicit denominators, support rate, positive-return rate,
mean/median supported ROI, mean time to support, eligible supported count, and
score/ROI Spearman correlation when enough samples exist.

Additional segment summaries are produced by:

- the base score bins;
- low/medium/high/unavailable liquidity-score bands;
- point-in-time market phase.

## Point-in-time market phase

The phase label is descriptive and does not alter scoring or execution:

- `unavailable`: no completed trade bucket in the configured lookback;
- `thin`: too few buckets or low median reported volume;
- `supply_shock`: latest price is sufficiently below prior median while volume
  is sufficiently above prior median;
- `recovery`: price has recovered by the configured ratio after a detected
  earlier shock;
- `normal`: none of the above.

All thresholds are versioned in `TradeEvidenceConfig` and included in the
configuration fingerprint. Because the volume unit is unknown, these labels
must be treated as relative diagnostics rather than market facts.

## Reproducibility

The base calibration dataset and configuration hashes remain unchanged.
Historical evidence has separate SHA-256 fingerprints that include:

- all quote histories;
- all historical trade buckets and their semantics;
- `TradeEvidenceConfig`;
- the base calibration configuration hash;
- evidence engine and policy versions.

This separation preserves historical quote-only comparisons while making every
trade-supported result reproducible.

## Explicit limitations

- VWAP is not a bucket high, low, open, close, or exact transaction list.
- Reported trade volume is not yet known to mean item count or match count.
- No queue position, partial fill, account capital, or order priority is modeled.
- Empty trade buckets are omitted by the source and cannot prove inactivity.
- Trade evidence does not change the public opportunity leaderboard.
- A supported result is not a recommendation, probability, or profit guarantee.
