# AGENTS.md

## Project mission
Build a compliant, reproducible Gaijin Market analytics website for imported or authorized data. The product provides descriptive statistics, backtests, and explainable timing references. It is not an automated trading system and must not promise profit.

## Hard compliance rules
- Never implement automated access to Gaijin Marketplace, including robots, crawlers, bots, scrapers, browser automation, captured internal endpoints, cookie reuse, rate-limit bypass, IP rotation, or login automation.
- Never implement automatic buying, selling, cancellation, account control, or payment actions.
- The MVP may only ingest CSV/JSON/manual data or a data source with explicit written authorization.
- Keep the data source behind a `MarketDataProvider` interface so it can be replaced later.
- Do not generate fake production market data. Synthetic fixtures must be clearly labeled.

## Repository layout
- `apps/web`: Next.js + TypeScript frontend.
- `apps/api`: FastAPI HTTP service and persistence orchestration.
- `packages/analytics`: pure Python metrics, signals, backtests, and models.
- `packages/shared-schemas`: API and data contracts.
- `docs`: product specification, methodology, data contract, ADRs.
- `tests`: shared fixtures, integration tests, and end-to-end tests.
- `infra`: Docker Compose and deployment configuration.

## Engineering rules
- Prefer small vertical slices. Do not rewrite unrelated files.
- Monetary values use Decimal/Numeric, never binary float in persistence or fee calculations.
- Store timestamps in UTC and convert only at presentation boundaries.
- Every import is idempotent and auditable with checksum, source, status, and error report.
- Strategy parameters, fee schedules, datasets, and outputs are versioned.
- Analytics functions should be deterministic and pure whenever practical.
- Do not add production dependencies without explaining why they are needed.
- Never commit `.env`, credentials, cookies, tokens, private datasets, or user secrets.

## Data-science rules
- Never use future information when producing a historical signal.
- Use time-based train/validation/test splits and walk-forward evaluation.
- Always compare complex models with simple baselines.
- Include fees, bid/ask spread, holding constraints, and failed execution in backtests.
- Report uncertainty, data freshness, model/strategy version, and reasons for each signal.
- A model may replace the baseline only when it improves out-of-time metrics and strategy utility.

## Required checks
Before declaring a task complete, run the checks available in the repository, including:
- Python: formatting, lint, type checking, unit tests, integration tests.
- TypeScript: lint, type check, component tests, production build.
- Database: migration upgrade/downgrade or migration verification.
- API: OpenAPI validation and integration tests.
- For UI changes: verify loading, empty, error, desktop, and mobile states.

## Definition of done for each task
- The requested behavior is implemented and documented.
- Tests cover normal paths and important edge cases.
- Relevant checks pass.
- The diff contains no unrelated changes or secrets.
- The final response summarizes files changed, commands run, results, assumptions, and remaining risks.
- For complex work, plan first and wait for plan approval before large implementation.

## Review checklist
Prioritize findings in this order:
1. Compliance violations or automation against Gaijin Marketplace.
2. Future-data leakage or incorrect backtest execution.
3. Money, fee, time-zone, and Decimal correctness.
4. Security, authentication, secret handling, and upload validation.
5. Idempotency, migrations, data integrity, and error handling.
6. Missing tests, documentation, or user-visible states.
