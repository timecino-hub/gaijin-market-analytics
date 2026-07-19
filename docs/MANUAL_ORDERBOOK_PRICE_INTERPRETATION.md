# Manual current-order-book GJN price interpretation

## Versioned contract

- Contract ID: `gaijin_market_1067_current_book_gjn_v1`
- Contract version: `1`
- Evidence level: `CONFIRMED_BY_PAIRED_OBSERVATION`
- Evidence package SHA-256: `f3a5a540fbde5fd0892320486b4b5ac85265f207e5c0abf107c84a7a398db0af`
- Host: `trade.gaijin.net`
- Market ID: `1067`
- Capture schema: `round6_manual_order_book_capture_v1`
- Data: current BUY/SELL order book
- Currency: `GJN`
- Raw scale: `10000`
- Display decimal places: `2`
- Maximum exact saved-formatter raw: `9007199254740893` (`2**53 - 99`)

`CONFIRMED_BY_PAIRED_OBSERVATION` means that two different products had capture identity, depth, timing, raw levels, and visible GJN values checked against the saved frontend formatter. It is a limited evidence contract, not a universal API guarantee or permission to change persistence.

## Exact values and observed display

The authoritative economic value is exact:

```text
base_amount_gjn = Decimal((0, digits(price_raw), -4))
```

Here `digits(value)` is the tuple of decimal digits in the positive integer
coefficient. No float conversion or Decimal division occurs. Up to four
decimal places are retained. For example, raw `1050001` is exactly `105.0001`
GJN.

The observed saved-bundle page display is a different value. This display
interpretation is defined only for
`1 <= price_raw <= 9007199254740893`:

```text
display_minor_units = (price_raw + 99) // 100
display_amount_gjn = Decimal((0, digits(display_minor_units), -2))
```

Both Decimal values are created directly from exact coefficient/exponent
tuples. Their values are independent of the caller's Decimal precision and
rounding mode. The module neither reads nor modifies the global Decimal
context; low precision cannot round either contract value during construction.

Within that display-safe range, this advances any non-cent remainder to the
next `0.01` GJN while leaving exact-cent values unchanged. In that deliberately
narrow range it is equivalent to the reviewed saved bundle's
`Math.round(price_raw) + 99` and string-slicing behavior. It is not claimed to
be equivalent over the parser's complete JavaScript-safe integer range, and it
is not Python `round`, banker rounding, or an unspecified `quantize` mode.

The strict parser has the wider raw-integer range
`1 <= price_raw <= 9007199254740991`. A raw value above the display-safe maximum
remains valid, preserved, and authoritative parser data, but this saved-bundle
display contract rejects it with `price_raw_display_range_unsupported`. That
error does not mean that the source data is invalid. Callers must retain the
raw integer and must not fallback to another scale, formatter, float
conversion, or guessed display value.

`canonical_display_text` is ASCII, uses `.` as the decimal point, always has two decimal places, and contains neither `GJN` nor thousands separators. It exists for contract tests and evidence comparison. It is not claimed to reproduce every locale's UI string.

## Applicability

The evidence-level public entry point is
`interpret_current_order_book_document(content, price_raw, ...)`. It accepts
only raw `bytes` or strict UTF-8 `str`, calls `parse_manual_order_book_json()`
internally, and only then evaluates applicability and price interpretation.
Constructed or replaced dataclass instances are not accepted by this public
entry point.

Interpretation is allowed only when every condition holds:

- the supplied document passes the complete I1 strict parser;
- schema is `round6_manual_order_book_capture_v1`;
- page origin is `https://trade.gaijin.net`;
- page path is exactly `/market/1067/<single segment>`;
- request identity is `POST https://market-proxy.gaijin.net/web`;
- order-book type is `COMMODITY`;
- caller explicitly specifies display currency `GJN` as a built-in `str`;
- contract ID is exactly `gaijin_market_1067_current_book_gjn_v1` and is a
  built-in `str`;
- contract version is exactly `1` and is a built-in `int`;
- `price_raw` is a built-in `int` within the display-safe range above.

Contract inputs accept only the exact built-in `int` and `str` types stated
above. Subclasses of `int` or `str` are rejected, including `bool` as an
`int` subclass.

Any mismatch raises a stable error. There is no fallback, guessing, or
alternative scale selection.

Actual request action remains `UNKNOWN`. Applicability neither requires nor guesses `cln_books_brief`, `cln_get_pair_stat`, user identity, pairing identity, locale, region, tax, or fee treatment.

## Authority and exclusions

`price_raw` remains permanently authoritative. The parser does not create Decimal values; the importer does not persist Decimal values; and the importer write path does not call this module. Existing capture payloads, fingerprints, fixtures, models, and migrations are unchanged.

This contract does not apply to history endpoints, other market IDs, currencies, display modes, account locales/regions, future frontend bundles, tax/fee/settlement amounts, or all Gaijin game-market interfaces.

This round does not approve a database migration, backfill, MarketSnapshot or market-history promotion, calibration, or Analytics promotion. Future behavior changes require a new contract version; v1 must never be silently redefined.
