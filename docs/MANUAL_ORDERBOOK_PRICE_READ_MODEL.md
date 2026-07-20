# Manual order-book price read model

## Boundary

This is a database-free, read-only price model and offline CLI. It is not an
importer. It does not create or update Item, ImportJob, manual import,
MarketSnapshot, history, calibration, or Analytics records. It reads no database,
network service, or environment-selected contract.

The sole public evidence-output function,
`render_manual_order_book_price_read_model`, accepts the original confirmed
capture only as exact
built-in `bytes` or exact built-in strict UTF-8 `str`, plus explicit contract
ID, integer contract version, display currency, and an optional level selector.
Subclasses, `bytearray`, `memoryview`, and other objects are rejected before the
strict parser. It performs exactly this flow:

```text
strict parser
-> P3-A applicability validation
-> level selection
-> bulk price interpretation
-> versioned read-only model
```

The document is parsed once and applicability is checked once. The function
directly returns the sole canonical JSON bytes. Constructed capture dataclasses
are not public input. There is no evidence-level public shortcut that accepts
only `price_raw`.

The read-model dataclass, builder, and serializer are private implementation
details. Frozen dataclasses are not a provenance security boundary:
`dataclasses.replace()` can alter their fields. Therefore no official public or
package-exported serializer accepts a constructed or modified model. Such a
model cannot produce formal schema JSON through the supported API.

## Versions and evidence

- Read-model schema: `round6_manual_order_book_price_read_model_v1`
- Implementation version: `round6d_p3c_v1`
- Price contract: `gaijin_market_1067_current_book_gjn_v1`
- Contract version: `1`
- Currency: `GJN`
- Raw scale: `10000`
- Display decimal places: `2`
- Display-safe maximum raw: `9007199254740893`
- Evidence level: `CONFIRMED_BY_PAIRED_OBSERVATION`
- Price evidence package SHA-256:
  `f3a5a540fbde5fd0892320486b4b5ac85265f207e5c0abf107c84a7a398db0af`
- Integration audit SHA-256:
  `302a9194d07c11575a0be8f844c4cb8d4ce4d0cd5396e738ecc3e586f6ea78d5`

`price_raw` remains permanently authoritative. Base and display amounts are
exact Decimal-derived JSON strings under the approved contract; they never
replace raw data and never pass through float.

## Source-file identity

For exact built-in `bytes`, `source_file_sha256` hashes the exact supplied bytes
and the output representation label is `supplied_bytes`.

For exact built-in `str`, the function strictly encodes the supplied text as
UTF-8 exactly once and hashes that representation. The label is
`supplied_utf8`. Unencodable text is rejected as `invalid_utf8`. The same
normalized `source_bytes` object is passed to the strict parser and SHA-256;
parsing and hashing cannot observe different subclass conversion behavior. This
does not claim to recover original file bytes, because newline and other textual
representation choices may already have changed before the function received
the string.

The CLI reads the explicit local file as bounded bytes, so its source-file hash
binds the actual input file bytes. The raw-response SHA-256 is emitted only as
`raw_response_sha256_claim`, with
`raw_response_hash_verifiable=false`, because raw response bytes are absent.

## Selectors

With no selector, all levels are returned in source order: every BUY level,
then every SELL level. Levels are not sorted, deduplicated, or merged. The
side-local zero-based `level_index`, quantity, and raw price are preserved.

A single-level selector requires both:

- exact built-in `str` side `BUY` or `SELL`;
- exact built-in non-`bool` `int` level index within that side.

Providing only one component, using a subclass, negative index, wrong side, or
out-of-range index raises a stable selector error. There is no cross-side or
last-level fallback.

## All-or-nothing display behavior

The strict parser permits a wider JavaScript-safe raw range than the saved
formatter evidence. If any selected level exceeds `9007199254740893`, the
operation raises `price_raw_display_range_unsupported` and produces no read
model. Raw input may still be valid. The implementation does not skip the level,
emit partial success, guess, or fall back to another scale.

All-level mode checks every level. Single-level mode interprets only the selected
level, while strict document parsing and complete applicability validation still
apply to the capture.

## Output

Top-level fields are `schema_version`, `validation_status`, `contract`,
`evidence`, `capture`, `selection`, and `levels`.

Capture metadata contains source schema/capture/time/page/request/review
provenance, request action `UNKNOWN`, fingerprint, source-file hash, and the
unverifiable raw-response hash claim. It contains no database Item or ImportJob
ID, account/user identity, Cookie, Authorization header, request payload, source
body, or absolute file path.

Each level contains side, side-local index, quantity, integer `price_raw`, exact
base/display amount strings, and two-place canonical display text. Canonical
text contains no currency suffix or thousands separator and is not a locale UI
claim.

## Deterministic JSON

The sole serializer uses:

- `ensure_ascii=False`;
- `sort_keys=True`;
- compact separators `,` and `:`;
- `allow_nan=False`;
- UTF-8 without BOM;
- exactly one final LF.

Output does not depend on locale, Decimal context, incidental dictionary order,
platform newlines, current time, UUID generation, database IDs, or file path.
The same input representation, contract inputs, and selector produce identical
bytes.

## Offline CLI

Example all-level invocation:

```text
python -m api.manual_order_book_price_cli capture.json \
  --contract-id gaijin_market_1067_current_book_gjn_v1 \
  --contract-version 1 \
  --currency GJN
```

Add `--side BUY --level-index 0` for one level. Contract parameters are always
explicit; the CLI never selects a latest contract or reads environment variables
to choose one.

The CLI calls the same one-shot public render function and writes only the
returned bytes. It does not expose or compose the private builder and serializer.

The CLI reads at most 2 MiB plus one detection byte using the existing strict
file boundary. Success JSON goes only to stdout. Expected errors produce narrow
JSON only on stderr, with no traceback, source body, all-level dump, or full
path. Exit `2` covers invalid documents/contracts/selectors/display range and
oversized input. Exit `4` covers file-open/read operational failures.
Integer arguments that exceed Python's conversion limit are mapped to their
stable contract/selector type error; the CLI neither exposes interpreter error
text nor reads or modifies the process-wide integer digit limit.

## Exclusions

The contract is limited to current BUY/SELL order books for market 1067 in GJN
under the fixed evidence package. Request action remains `UNKNOWN`. It does not
apply to history endpoints, other markets/currencies, account behavior, fees,
settlement, or future frontend bundles.

The one-shot boundary changes neither output schema nor sample bytes. This
implementation does not approve a migration, persistence/backfill,
MarketSnapshot integration, market-history export, calibration, or Analytics
promotion. Future read-model schema or price-contract behavior changes require
a new version; v1 is not silently redefined.
