# Gaijin Market Analytics

Minimal engineering scaffold for a compliant Gaijin Market analytics website.
This repository is limited to imported, manual, or explicitly authorized data.
It does not implement marketplace automation, account automation, trading, or
profit promises.

Allowed future data sources are CSV files, JSON files, manual entry, or data
sources with explicit written authorization.

## Stack

- Python 3.12 managed with uv
- FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, psycopg 3
- pytest for API tests
- Node.js 22 LTS with pnpm
- Next.js and TypeScript
- PostgreSQL through Docker Compose

## Analytics Package

`packages/analytics` is an independent uv project for pure Python analytics
contracts, Decimal statistics, fee calculations, horizon selection, strategy
registration, and the first transparent baseline strategy. It does not import
FastAPI, SQLAlchemy ORM models, database sessions, HTTP clients, filesystem
read/write helpers, or environment variables. All inputs are passed explicitly
through immutable contracts and all outputs are returned as typed values.

Install and test it separately from the API:

```sh
cd packages/analytics
uv --cache-dir ../../.uv-cache sync --dev
uv --cache-dir ../../.uv-cache run pytest
uv --cache-dir ../../.uv-cache lock --check
```

Or from the repository root:

```sh
make analytics-install
make analytics-test
make analytics-lock-check
```

The package uses Python `Decimal` for money, ratios, scores, fees, profit, ROI,
and break-even calculations. It does not convert analytics values to JSON
floats. Timestamps must be timezone-aware and are normalized to UTC at the
contract boundary. Contract errors such as naive datetimes, future observations,
and invalid item IDs raise stable domain exceptions.

The supported analysis horizons are 7, 30, 90, and 180 days. Each window is
selected independently as `[as_of - horizon, as_of]`; shorter windows are never
derived from longer-window results.

`RuleBasedV1` is a deterministic baseline used to exercise the analytics
contract and replacement mechanism. Its statistical reference starts from the
median valid bid inside the selected window, then the displayable
`reference_sell_price` is rounded down to the 0.01 GJN market price quantum.
This is an explainable timing reference, not a future price prediction, profit
guarantee, or trading recommendation. The result always includes strategy,
strategy version, feature version, and fee policy version so future backtests
can reproduce which implementation generated an output.

The current Gaijin Market fee policy is fixed in analytics code as
`gaijin_market` version `1.0.0`: nominal fee rate `0.15`, currency quantum
`0.01`, and proceeds rounding `seller_proceeds_round_down`. Seller proceeds are
computed as `sell_price * 0.85` and then rounded down to 0.01 GJN. For example:

```text
listed sell price: 1.99 GJN
raw seller proceeds: 1.6915 GJN
settled seller proceeds: 1.69 GJN
actual fee amount: 0.30 GJN
```

The actual fee amount is `sell_price - sale_proceeds`, so it can be slightly
higher than exactly 15% because of settlement rounding.

The current Gaijin Market rules are also fixed in analytics code as
`gaijin_market` version `1.0.0`: maximum listing price `2000.00` GJN and
currency quantum derived from the fee policy. The maximum seller settlement
proceeds are derived by applying the fixed fee policy to the maximum listing
price, producing `1700.00` GJN. This is not a fixed profit cap for every item;
the theoretical maximum net profit under the current buy price is
`maximum_sale_proceeds - current_ask`.

See `docs/analytics-design.md` for the input/output contracts, fee math,
scoring formulas, data insufficiency behavior, and registry design.

## Install

```sh
make install
```

Or install each side independently:

```sh
make api-install
make web-install
```

## Development

Copy the example environment file before running services:

```sh
cp .env.example .env
```

Start PostgreSQL:

```sh
make db-up
```

Run database migrations:

```sh
make db-migrate
```

Start the API on port 8000:

```sh
make api-dev
```

The API development entry point binds to `127.0.0.1` by default. Keep local
bridge endpoints on loopback; do not expose them to the LAN.

Start the web app on port 3000:

```sh
make web-dev
```

The web app reads the API base URL from `NEXT_PUBLIC_API_BASE_URL`. For local
development, copy `apps/web/.env.local.example` to `apps/web/.env.local` and
keep the default value:

```sh
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Only public browser-safe values belong in `NEXT_PUBLIC_*` variables. Do not put
database URLs, passwords, tokens, cookies, or private datasets in them.

The API exposes `GET /health` as a database-independent liveness check and
`GET /ready` as a PostgreSQL readiness check. FastAPI's OpenAPI schema remains
available at the default `/openapi.json`.

The API enables CORS for configured origins only. Local development defaults to
`CORS_ALLOWED_ORIGINS=http://localhost:3000`; it does not use a wildcard origin
or browser credentials.

The Local Extension Bridge management endpoints also require an allowed local
Web `Origin`. This is a localhost cross-site request protection, not a defense
against a process that already controls the local machine.

CSV import endpoints:

- `POST /api/v1/imports/csv`
- `GET /api/v1/imports/{job_id}`

CSV imports accept only user-uploaded `.csv` files, default to a 10 MB upload
limit, compute a SHA-256 checksum from the original bytes, and reject duplicate
files by checksum without inserting duplicate snapshots. The CSV v1 fields are:
`item_key`, `item_name`, `category`, `observed_at`, `best_ask`, `best_bid`,
`ask_count`, `bid_count`, and `estimated_volume`. Prices and volume are parsed
with Python `Decimal`; JSON responses return import metadata and error reports,
not the uploaded file contents.

The web app exposes the CSV upload flow at `http://localhost:3000/imports`.
Choose a local `.csv` file, review the filename, size, browser-provided MIME
type, and selection state, then upload it with the "上传 CSV" button. The
browser applies the same default 10 MB size limit as a user-experience
precheck, but the API remains the trusted validator.

The web app also exposes the local screen-recognition review flow at
`http://localhost:3000/screen-recognition`. This alpha page accepts a manually
selected current market screenshot, creates an in-memory review through
`POST /api/v1/local-recognition/reviews`, runs local Windows OCR in a background
task, and lets a reviewer confirm item identity, prices, quantities, and
`observed_at` before generating a reviewed candidate JSON object. Reviews are
kept only in the API process memory, expire after two hours, and are cleared on
service restart. The candidate records `imported=false` and
`database_written=false`; it is not a market snapshot, is not automatically
imported, and does not generate CSV. See
`docs/screen-recognition-review-workflow.md`.

Import statuses shown by the web page:

- `completed`: the CSV was accepted and valid rows were imported into the
  local database. The result shows row counts, a shortened checksum, warning
  count, error count, and a link to `/items`.
- `duplicate`: the checksum matches an earlier successful import. The result
  shows both the current duplicate `job_id` and `duplicate_of_job_id`; it does
  not imply that rows were written again.
- `failed`: the API created a failed job and returned a structured error
  report. Errors and warnings are displayed separately, with long lists capped
  on initial render.

Read-only market query endpoints:

- `GET /api/v1/items`
- `GET /api/v1/items/{item_id}`
- `GET /api/v1/items/{item_id}/snapshots`
- `GET /api/v1/items/{item_id}/analysis`

Developer backtesting is available only as a read-only CLI. It does not add a
public HTTP route, web page, result table, or background job:

```sh
cd apps/api
uv run python -m api.backtesting_cli \
  --item-id 123 \
  --lookback-horizon 30 \
  --forward-horizon 30 \
  --start 2026-01-01T00:00:00Z \
  --end 2026-06-01T00:00:00Z \
  --cadence-days 7 \
  --pretty
```

Backtesting separates the analysis lookback window from the future snapshot
evaluation window. Future `best_bid` values are evaluation proxies, not evidence
of executed trades or profit guarantees. See `docs/backtesting.md`.

Developer screen-recognition acceptance is available as a local-only CLI. It is
for Screen Recognition CUT-20 on user-provided PNG/JPEG screenshots and
manually labeled ground truth. It does not access Gaijin Market, automate a
browser, generate import CSVs, call the CSV import API, or write the database.

```sh
cd apps/api
uv run python -m api.screen_recognition_cut init \
  --images-dir <path-outside-repo>/images \
  --output <path-outside-repo>/ground_truth.jsonl

uv run python -m api.screen_recognition_cut run \
  --images-dir <path-outside-repo>/images \
  --ground-truth <path-outside-repo>/ground_truth.jsonl \
  --output-dir <path-outside-repo>/output \
  --layout-profile gaijin-market-desktop-v1 \
  --ocr-backend windows-ocr \
  --strict
```

The `windows-ocr` backend uses local Windows Media OCR through PowerShell. The
`sidecar` backend is parser-only and must not be mixed with end-to-end accuracy
statistics. See `docs/screen-recognition-cut.md`.

Paired screen-recognition acceptance is also available for current/history
image pairs such as `001.png` and `001_1.png`:

```sh
cd apps/api
uv run python -m api.screen_recognition_cut init-paired \
  --images-dir <path-outside-repo>/images \
  --output <path-outside-repo>/paired_ground_truth.jsonl

uv run python -m api.screen_recognition_cut run-paired \
  --images-dir <path-outside-repo>/images \
  --ground-truth <path-outside-repo>/paired_ground_truth.jsonl \
  --output-dir <path-outside-repo>/paired-output \
  --current-layout gaijin-market-desktop-v1 \
  --history-layout gaijin-market-history-v1 \
  --ocr-backend windows-ocr \
  --strict
```

Paired history analysis uses local Pillow-based color masks for red price areas
and blue volume lines. Chart-derived values are estimates (`exact=false`) and
are never imported or persisted automatically.

These endpoints expose only items and snapshots already imported into
PostgreSQL from allowed sources. They do not create, edit, delete, enrich,
backfill, interpolate, or calculate returns from market data.

`GET /api/v1/items` lists items with pagination and optional filters:

```sh
curl "http://localhost:8000/api/v1/items?page=1&page_size=50&search=synthetic&sort=name&order=asc"
```

Supported query parameters are `page`, `page_size`, `search`, `category`,
`rarity`, `is_active`, `sort`, and `order`. Sorting allows `name`,
`created_at`, and `updated_at`; results always add `id asc` as a stable
secondary sort. Each item includes at most one `latest_snapshot` value.

`GET /api/v1/items/{item_id}` returns item metadata, its latest snapshot, and
snapshot summary timestamps/counts. It does not return full history.

`GET /api/v1/items/{item_id}/snapshots` returns bounded historical snapshots:

```sh
curl "http://localhost:8000/api/v1/items/1/snapshots?from=2026-06-27T00:00:00Z&to=2026-06-28T00:00:00Z&limit=500&order=asc"
```

The `from` and `to` filters must be ISO-8601 datetimes with a timezone and are
normalized to UTC. Snapshot history is sorted by `observed_at` and `id`.
Decimal/NUMERIC fields such as `best_ask`, `best_bid`, and
`estimated_volume` are returned as JSON strings or `null`, never floats.
The web UI keeps those values as strings and only formats them for display, so
browser floating-point arithmetic is not used for monetary values.

`GET /api/v1/items/{item_id}/analysis` computes a read-only baseline analysis
from snapshots already stored in PostgreSQL:

```sh
curl "http://localhost:8000/api/v1/items/1/analysis?horizon=7&as_of=2026-06-29T00:00:00Z"
```

Supported query parameters:

- `horizon`: required, one of `7`, `30`, `90`, or `180`.
- `as_of`: optional ISO-8601 datetime with timezone. When omitted, the API uses
  the current UTC time through a testable clock dependency.

The API does not accept a configurable `fee_rate`. If an old client sends that
query parameter, the route returns HTTP 400 with `fee_rate_not_configurable`.

The API queries only the inclusive database window `[as_of - horizon, as_of]`
using `observed_at >= as_of - horizon` and `observed_at <= as_of`, ordered by
`observed_at asc, id asc`. Results are computed immediately and are not
persisted; this repository does not create an `analysis_results` table.

The response includes item metadata, effective inputs, fixed fee policy
metadata, fixed market rules metadata, strategy metadata, and the `RuleBasedV1`
output. `reference_sell_price` is a baseline reference sell price derived from
the valid bid median after excluding bids above the `2000.00` GJN market cap
and rounded down to the 0.01 GJN price quantum, not a guaranteed future price.
`sale_proceeds` is the rounded seller settlement amount, `fee_amount` is
`reference_sell_price - sale_proceeds`, and `confidence_score` is an
explainability score, not a profit probability. Decimal values, including fee
policy values, market rules values, proceeds, profit/ROI fields, spreads,
medians, volatility, and scores, are returned as JSON strings or `null`, never
JSON floats.

HTTP errors are reserved for missing items, invalid query parameters, invalid
contract inputs, unavailable strategies, or invalid analytics configuration.
Normal data limitations such as empty windows, too few snapshots, insufficient
coverage, stale latest snapshots, or no valid bid/ask return HTTP 200 with
analysis `status` and stable `reason_codes`.

The item detail web page exposes the same read-only analysis at
`/items/{item_id}`. Choose one of the 7, 30, 90, or 180 day windows and click
"Run analysis". The browser displays the fixed 15% nominal fee policy, 0.01 GJN
settlement quantum, and seller-proceeds round-down rule; it does not provide a
fee input and never sends `fee_rate`. The optional `as_of` control is for
historical reproduction; when provided, the local `datetime-local` value is
converted to an ISO-8601 timestamp with timezone before submission. When
omitted, the API uses current UTC.

Submitted analysis parameters are kept in the item detail URL, for example
`/items/1?horizon=30`. A valid `horizon` in the URL restores the form after
refresh and may run one analysis request. Invalid URL parameters show a
client-readable error and do not trigger an automatic analysis request.
Snapshot filters such as `from`, `to`, `limit`, and `order` are preserved when
analysis parameters change, and analysis parameters are preserved when snapshot
filters change. Legacy `fee_rate` query parameters are ignored by the frontend
and removed on the next URL update.

Web routes:

- `/`: project overview, compliance notice, and entry to the item browser.
- `/imports`: browser CSV upload flow for authorized CSV v1 data.
- `/items`: searchable and paginated list of imported items.
- `/items/{item_id}`: item metadata, latest snapshot, and historical snapshot
  table with time range filters, plus the read-only RuleBasedV1 analysis panel
  for 7, 30, 90, and 180 day windows.
- `/screen-recognition`: local-only manual review page for uploaded current
  screenshots and reviewed candidate JSON generation.

Example item list response:

```json
{
  "items": [
    {
      "id": 1,
      "external_key": "synthetic-item-alpha",
      "name": "Synthetic Alpha",
      "category": "vehicle",
      "rarity": null,
      "is_active": true,
      "created_at": "2026-06-27T00:00:00Z",
      "updated_at": "2026-06-27T00:00:00Z",
      "latest_snapshot": {
        "observed_at": "2026-06-27T01:00:00Z",
        "best_ask": "12.340000",
        "best_bid": "11.100000",
        "ask_count": 3,
        "bid_count": 2,
        "estimated_volume": "44.500000"
      }
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 1,
  "total_pages": 1
}
```

Migration helpers:

```sh
make db-current
make db-downgrade
make db-migrate
```

Import the labeled synthetic fixture from this repository after starting the API:

```sh
curl -F "file=@tests/fixtures/synthetic/valid_market_data.csv" http://localhost:8000/api/v1/imports/csv
```

You can also open `http://localhost:3000/imports` and select the same fixture
from the file picker. Then open `http://localhost:3000/items` to browse the
imported synthetic items.

## Checks

```sh
make analytics-test
make test-api
pnpm web:test
make web-lint
pnpm --filter @gaijin-market-analytics/web typecheck
make web-build
make compose-config
```

## Current Scope

Implemented: project skeleton, API health and readiness checks, API tests,
Next.js shell, local configuration examples, PostgreSQL Docker Compose service,
SQLAlchemy async database setup, Alembic migration commands, database foundation
tables, compliant CSV market data import, web CSV upload flow, read-only
item/snapshot query APIs, read-only immediate baseline analysis API, item detail
analysis UI, the standalone pure Python analytics foundation, and a read-only
developer CLI for walk-forward backtesting of imported snapshots.

Not implemented: item write APIs, standalone snapshot write APIs, persisted
analysis results, machine-learning dependencies, user accounts, marketplace
scraping, login automation, or automated trading actions.
