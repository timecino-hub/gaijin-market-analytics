# PROJECT_PLAN.md

## Phase 0 — Freeze scope
- Approve product specification, compliance constraints, data contract, and risk register.
- Decide the exact CSV fields and prepare labeled synthetic fixtures.
- Done when no unresolved question blocks the database schema or import flow.

## Phase 1 — Engineering skeleton
- Scaffold `apps/web`, `apps/api`, `packages/analytics`, tests, docs, and Docker Compose.
- Add health checks, environment templates, Makefile/task commands, and CI.
- Done when a new machine can start the stack and all empty-project checks pass.

## Phase 2 — Data layer
- Implement tables, migrations, CSV validation, deduplication, import jobs, and error reports.
- Done when valid sample data imports idempotently and invalid rows are quarantined.

## Phase 3 — Baseline analytics
- Implement fee-aware profit/ROI/break-even, robust z-score, trend, spread, imbalance, volatility, liquidity, eligibility, score, and explanations.
- Done when deterministic unit tests cover edge cases and formulas are documented.

## Phase 4 — API
- Implement item, history, metrics, signal, import, event, setting, and backtest endpoints.
- Done when OpenAPI and integration tests pass.

## Phase 5 — Web experience
- Build dashboard, list, item detail, calculator, events, imports, and settings pages.
- Done when desktop/mobile, loading/empty/error states, and core E2E flow pass.

## Phase 6 — Walk-forward backtest
- Implement point-in-time data access, execution simulation, fees/spread/holding constraints, metrics, dataset hash, and exports.
- Done when a hand-calculated fixture matches exactly and leakage tests pass.

## Phase 7 — Model experiments
- Add quantile price prediction and sale-probability models behind feature flags.
- Compare with baselines using time-based evaluation and model cards.
- Done only if out-of-time improvement is stable and explainable.

## Phase 8 — Production hardening
- Add authentication, authorization, audit logs, backups, monitoring, rate limits, file limits, and recovery documentation.
- Done when security and restore checklists pass.
