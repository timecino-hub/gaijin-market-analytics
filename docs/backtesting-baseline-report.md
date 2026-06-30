# Backtesting Baseline Report

## 1. Investigation Snapshot

| Field | Value |
| --- | --- |
| Investigation date | 2026-06-30T06:40:42Z |
| Branch | chore/baseline-backtest-study |
| Git commit | f4cba30 |
| Scope | Baseline RuleBasedV1 data-readiness and backtest preflight |
| Data source | Existing local PostgreSQL rows only |
| Marketplace automation | None |
| Database writes during investigation | None after existing migration check |
| Production code changes | None |

This report was produced from an isolated worktree on `chore/baseline-backtest-study`. The previous screen-recognition worktree was not edited, reverted, copied from, committed, or pushed.

## 2. PostgreSQL Recovery

Initial database access failed because the new worktree's compose project tried to create a fixed-name container, `gaijin-market-analytics-postgres`, while an older container with that name already existed from the original compose project. That existing container was paused and unhealthy, while still occupying the configured database service identity and port mapping.

Recovery action:

- No volume was deleted.
- No database was rebuilt.
- No data was cleared.
- The existing container was unpaused.

Final health checks:

| Check | Result |
| --- | --- |
| `Test-NetConnection localhost -Port 5432` | `TcpTestSucceeded: True` |
| `pg_isready -U gaijin_market -d gaijin_market_analytics` | Accepting connections |
| Compose service status | `Up` and `healthy` |
| Alembic current | `20260627_0001 (head)` |

PostgreSQL was restored.

## 3. Database Inventory

| Count | Before investigation | After investigation | Match |
| --- | ---: | ---: | --- |
| items | 8 | 8 | true |
| market_snapshots | 24 | 24 | true |
| import_jobs | 12 | 12 | true |
| migration | 20260627_0001 | 20260627_0001 | true |

Import job summary:

| source_type | status | jobs | row_count | valid_row_count | invalid_row_count |
| --- | --- | ---: | ---: | ---: | ---: |
| csv_upload | completed | 5 | 24 | 24 | 0 |
| csv_upload | duplicate | 5 | 9 | 0 | 0 |
| csv_upload | failed | 2 | 0 | 0 | 0 |

## 4. Exclusions

Exclusion matching was case-insensitive and limited to explicit test markers in `category`, `external_key`, or `name`: `synthetic`, `smoke`, `test`, `cap-reachable`, `cap-unreachable`, and `backtest-smoke`.

All 8 database items were excluded. No item remained for formal backtesting.

| Item id | Category | Snapshots | Min observed_at | Max observed_at | Exclusion reasons |
| ---: | --- | ---: | --- | --- | --- |
| 1 | vehicle | 1 | 2026-06-27T00:00:00Z | 2026-06-27T00:00:00Z | external_key synthetic; name synthetic |
| 2 | skin | 1 | 2026-06-27T00:30:00Z | 2026-06-27T00:30:00Z | external_key synthetic; name synthetic |
| 3 | vehicle | 1 | 2026-06-29T00:00:00Z | 2026-06-29T00:00:00Z | external_key synthetic; name synthetic; external_key smoke; name smoke |
| 4 | vehicle | 1 | 2026-06-29T06:22:59.590693Z | 2026-06-29T06:22:59.590693Z | external_key smoke; name smoke |
| 5 | skin | 1 | 2026-06-29T06:23:00.606316Z | 2026-06-29T06:23:00.606316Z | external_key smoke; name smoke |
| 9 | synthetic | 3 | 2026-06-22T14:08:38.135608Z | 2026-06-29T13:08:38.135608Z | category synthetic; name smoke; external_key cap-reachable |
| 12 | synthetic | 4 | 2026-06-22T14:08:38.135608Z | 2026-06-29T14:08:38.135608Z | category synthetic; name smoke; external_key cap-unreachable |
| 19 | synthetic | 12 | 2026-06-03T00:00:00Z | 2026-06-24T00:00:00Z | category synthetic; external_key smoke; name smoke; external_key backtest-smoke |

Reason counts:

| Reason | Count |
| --- | ---: |
| category synthetic | 3 |
| external_key synthetic | 3 |
| name synthetic | 3 |
| external_key smoke | 4 |
| name smoke | 6 |
| external_key cap-reachable | 1 |
| external_key cap-unreachable | 1 |
| external_key backtest-smoke | 1 |

Included investigation items: 0.

## 5. Data Coverage

| Coverage field | Value |
| --- | --- |
| Non-test earliest observed_at | null |
| Non-test latest observed_at | null |
| Non-test snapshot count | 0 |
| Excluded test earliest observed_at | 2026-06-03T00:00:00Z |
| Excluded test latest observed_at | 2026-06-29T14:08:38.135608Z |

No non-test data remained after exclusion, so snapshot interval statistics, duplicate timestamp checks, missing bid coverage, and future-window completeness could not be evaluated for real or authorized market data.

## 6. Preflight Scale Estimate

Theoretical eligibility requires enough non-test coverage for `min_observed_at + lookback <= max_observed_at - forward`.

| Combination | Lookback | Forward | Cadence | Theoretical eligible items | Planned CLI runs | Estimated cases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 7 | 7 | 7 | 0 | 0 | 0 |
| B | 30 | 7 | 7 | 0 | 0 | 0 |
| C | 30 | 30 | 7 | 0 | 0 | 0 |
| D | 90 | 30 | 14 | 0 | 0 | 0 |
| E | 180 | 90 | 30 | 0 | 0 | 0 |

Total planned CLI runs: 0.

Estimated case count: 0.

Estimated runtime: 0 seconds.

The run-count threshold of 200, total runtime threshold of 30 minutes, and single-run threshold of 60 seconds were not exceeded. No sampling was performed.

## 7. CLI Execution

No `api.backtesting_cli` batch runs were executed because no non-test item qualified for any A-E combination.

| Metric | Value |
| --- | ---: |
| Planned CLI runs | 0 |
| Actual completed CLI runs | 0 |
| CLI success count | 0 |
| CLI failure count | 0 |
| Consecutive CLI failures | 0 |
| Failure rate | not applicable |
| Stop threshold triggered | false |

The existing CLI sets `require_complete_forward_window=True`; if eligible data exists in a later run, incomplete future windows will not enter formal metric denominators.

## 8. A-E Results

No formal backtest metrics were produced. The values below are unevaluated, not zero-performance results.

| Combination | Theoretical eligible items | Actual successes | Total cases | Evaluated cases | Micro denominator | Macro valid items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 0 | 0 | 0 | 0 | 0 | 0 |
| B | 0 | 0 | 0 | 0 | 0 | 0 |
| C | 0 | 0 | 0 | 0 | 0 | 0 |
| D | 0 | 0 | 0 | 0 | 0 | 0 |
| E | 0 | 0 | 0 | 0 | 0 | 0 |

## 9. Micro Summary

Micro metrics are case-weighted and require evaluated cases. All denominators are zero.

| Combination | Reference reach numerator | Reference reach denominator | Reference reach rate | Break-even reach numerator | Break-even reach denominator | Break-even reach rate | Positive terminal numerator | Positive terminal denominator | Positive terminal rate |
| --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: | --- |
| A | 0 | 0 | null | 0 | 0 | null | 0 | 0 | null |
| B | 0 | 0 | null | 0 | 0 | null | 0 | 0 | null |
| C | 0 | 0 | null | 0 | 0 | null | 0 | 0 | null |
| D | 0 | 0 | null | 0 | 0 | null | 0 | 0 | null |
| E | 0 | 0 | null | 0 | 0 | null | 0 | 0 | null |

## 10. Macro Summary

Macro metrics are item-weighted medians and require at least one valid item-level denominator. No item qualified.

| Combination | Macro valid items | Median item reference reach | Median item break-even reach | Median item positive terminal | Median item terminal ROI | Median evaluated cases per item |
| --- | ---: | --- | --- | --- | --- | --- |
| A | 0 | null | null | null | null | null |
| B | 0 | null | null | null | null | null |
| C | 0 | null | null | null | null | null |
| D | 0 | null | null | null | null | null |
| E | 0 | null | null | null | null | null |

## 11. RuleBasedV1 Readiness

There is not enough non-test, source-audited data to evaluate RuleBasedV1. The database currently contains only rows that match synthetic, smoke, or test markers. No reference reach, break-even reach, terminal return, ROI, or timing conclusion should be drawn from this run.

## 12. Recommended Next Stage

Recommended priority: import or accumulate explicitly authorized, non-test historical snapshots.

Minimum next-step requirements:

- Keep ingestion behind the existing `MarketDataProvider` boundary.
- Preserve import auditability with checksum, source, status, and error report.
- Accumulate enough non-test time coverage to support at least combination A before interpreting RuleBasedV1.
- Re-run this investigation only after preflight shows non-zero eligible items.

Do not proceed to RuleBasedV2, factor tuning, scoring, or machine-learning experiments from the current data.

## 13. Cleanup And Status

| Check | Result |
| --- | --- |
| Raw CLI JSON files in repo | None |
| CLI stderr files in repo | None |
| Temporary scripts in repo | None |
| Database dumps in repo | None |
| `.env` committed or modified | No |
| Analytics package modified | No |
| API modified | No |
| Web modified | No |
| Tests modified | No |
| Migrations modified | No |
| Investigation-time commits | None |
| Investigation-time pushes | None |

Final observed worktree status:

```text
?? docs/backtesting-baseline-report.md
```

This report is suitable to commit as a baseline investigation record, with the caveat that it is a data-readiness result rather than a strategy-performance result.
