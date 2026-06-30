# Screen Recognition CUT-20 And Paired CUT-20

Screen Recognition CUT-20 is a short-term component acceptance test for local
screen-recognition work. It is not a long-term accuracy proof, market model
validation, trading system, or profit/backtest claim.

The tool only works with local PNG/JPEG screenshots, manually prepared ground
truth, and local OCR output. It does not access Gaijin Market, automate a
browser, log in, reuse cookies, call internal endpoints, trade, write the
database, or call the CSV import API.

## Current OCR Backend

The first end-to-end backend is `windows-ocr`. It uses Windows Media OCR through
PowerShell and .NET image cropping on the user's machine.

- Runs locally on Windows.
- Does not upload screenshots.
- Does not access the network at CUT runtime.
- Does not download OCR models at CUT runtime.
- Requires Windows OCR language support to be available for the current user
  profile.
- Uses CPU only for deterministic crop and scale preprocessing.
- Does not expose confidence scores through the Windows API, so CUT records
  `confidence: null` and `confidence_source: unavailable`.

Windows OCR evidence includes raw text, field bounding rectangles, OCR line
order, word order, and line/word bounding rectangles when the Windows OCR API
returns them. The tool must not fabricate confidence scores.

The auxiliary `sidecar` backend reads authorized local `.ocr.txt` files and is
for parser-only diagnosis. Parser-only runs must not be mixed with end-to-end
accuracy statistics.

Pillow is an API/tooling dependency used only for local image reading, ROI
handling, RGB color masks, chart pixel sampling, and optional debug artifacts.
It is not added to `packages/analytics`, does not download models, and does not
perform network access at runtime.

## Layout Profile

The initial profile is `gaijin-market-desktop-v1`. It uses normalized
coordinates relative to image width and height, not one screenshot's absolute
pixels.

The profile defines ROIs for:

- `item_name`
- `best_bid`
- `best_ask`
- `total_bid_quantity`
- `total_ask_quantity`
- `bid_levels`
- `ask_levels`

The profile also checks minimum resolution and aspect ratio. Non-matching
screenshots return `unsupported_layout`. The profile does not contain item
names, prices, or answers for a particular private batch.

Paired CUT-20 also defines `gaijin-market-history-v1` for `_1` history chart
screenshots. It defines normalized ROIs for item name, top order-book
distribution, bottom historical chart, left price axis labels, right volume
axis labels, time axis labels, red price plot, blue volume plot, and legend.

## Workflow

1. Prepare or extract screenshots outside the repository. Keep private
   screenshots out of Git.
2. Generate a ground truth template:

   ```sh
   cd apps/api
   uv run python -m api.screen_recognition_cut init \
     --images-dir <path-outside-repo>/images \
     --output <path-outside-repo>/ground_truth.jsonl
   ```

3. Have an administrator manually fill the template fields:
   `item_key`, `item_name`, `best_bid`, `best_ask`,
   `total_bid_quantity`, `total_ask_quantity`, optional visible levels, and
   `expected_status`.
4. Choose the layout profile, currently `gaijin-market-desktop-v1`.
5. Run end-to-end CUT:

   ```sh
   cd apps/api
   uv run python -m api.screen_recognition_cut run \
     --images-dir <path-outside-repo>/images \
     --ground-truth <path-outside-repo>/ground_truth.jsonl \
     --output-dir <path-outside-repo>/output \
     --layout-profile gaijin-market-desktop-v1 \
     --ocr-backend windows-ocr \
     --strict
   ```

6. Review `summary.json` and `report.md`.
7. Inspect structured failures under `failures/`. The tool does not copy source
   screenshots into failure records.
8. Fix OCR, parsing, layout, or ground truth issues and rerun the same manually
   labeled ground truth.

Use parser-only diagnosis only when authorized OCR sidecars exist:

```sh
uv run python -m api.screen_recognition_cut run \
  --images-dir <path-outside-repo>/images \
  --ground-truth <path-outside-repo>/ground_truth.jsonl \
  --output-dir <path-outside-repo>/parser-output \
  --layout-profile gaijin-market-desktop-v1 \
  --ocr-backend sidecar
```

## Paired CUT-20 Workflow

`Screen Recognition Paired CUT-20` treats each sample as a pair:

- `001.png`: current item page with current price and order-book information.
- `001_1.png`: history/chart page for the same item.

Generate a paired template:

```sh
cd apps/api
uv run python -m api.screen_recognition_cut init-paired \
  --images-dir <path-outside-repo>/images \
  --output <path-outside-repo>/paired_ground_truth.jsonl
```

The template enumerates PNG/JPG/JPEG files, pairs `001` with `001_1`, records
missing or duplicate images, and leaves administrator fields as `null`. It does
not run OCR and does not fill answers.

Run paired CUT after manual ground truth is complete:

```sh
uv run python -m api.screen_recognition_cut run-paired \
  --images-dir <path-outside-repo>/images \
  --ground-truth <path-outside-repo>/paired_ground_truth.jsonl \
  --output-dir <path-outside-repo>/paired-output \
  --current-layout gaijin-market-desktop-v1 \
  --history-layout gaijin-market-history-v1 \
  --ocr-backend windows-ocr \
  --strict
```

The default split is explicit in the template and config: `001` through `004`
are calibration, and `005` through `020` are evaluation. Calibration may tune
ROI and color parameters. Evaluation must use the frozen configuration recorded
by SHA-256 in `run_metadata.json`.

## History Chart Recognition

History recognition is reported in three levels:

- Level 1 structure detection: top order-book distribution, bottom chart
  region, axes, red series, and blue series.
- Level 2 axis semantics: red price series bound to the left axis, blue volume
  series bound to the right axis, one-month time range evidence, and no axis
  swap.
- Level 3 numeric estimation: validated pixel-to-value mappings and
  chart-derived estimates.

Level 1 or Level 2 success does not imply Level 3 success. If the tool cannot
build a reliable numeric mapping, it reports
`chart_numeric_mapping_unavailable` and leaves estimates as `null`.

Red series extraction uses only the lower historical chart ROI, segments
approximate red pixels, and records the upper envelope as price position:
`extraction_method = red_area_upper_envelope`. It does not use the red area's
geometric center.

Blue series extraction uses only the lower historical chart ROI, segments
approximate blue pixels, and records a stable median y for detected line pixels:
`extraction_method = blue_line_median_y`. Missing or broken positions remain
`null`; no interpolation is fabricated.

Every chart-derived point records `source = chart_estimate` and `exact = false`.
These estimates must not become market snapshots, import CSV rows, or backtest
inputs automatically.

An axis mapping is built only when at least two different OCR tick values are
present, each tick has a y position, tick values are monotonic by vertical
position, duplicates are absent, and linear residual is within the configured
threshold. Left-axis prices use Decimal values. Right-axis volumes must be
non-negative.

## Ground Truth JSONL

Each line is one JSON object. Prices must be JSON strings so they are parsed as
`Decimal`; JSON floats are rejected.

```json
{
  "sample_id": "cut_001",
  "filename": "cut_001.png",
  "expected_status": "passed",
  "item_key": "admin-provided-key",
  "item_name": "Example Item",
  "best_bid": "12.34",
  "best_ask": "13.00",
  "total_bid_quantity": 5,
  "total_ask_quantity": 7,
  "bid_levels": [
    {"exact_price": "12.34", "quantity": 2, "raw_display_price": "12.34"},
    {"price_lower_bound": "89.00", "lower_bound_inclusive": true, "aggregation_type": "greater_than_or_equal", "quantity": 3, "raw_display_price": "89.00+"}
  ],
  "ask_levels": []
}
```

`item_key` is administrator-provided manifest data. OCR only recognizes and
checks `item_name`; it does not generate a formal item key, and filenames are
never used as recognition answers.

Missing optional fields are not scored. They are not treated as zero.

## Outputs

The output directory contains:

- `run_metadata.json`
- `results.jsonl`
- `results.csv`
- `summary.json`
- `report.md`
- `failures/<sample_id>.json`

Each result includes image dimensions, layout match state, raw OCR text per
field, OCR confidence field, parsed contract, expected contract, comparisons,
warnings, errors, duration, and status. Failures are structured enough to tell
whether the problem came from image reading, layout matching, OCR, parsing,
domain validation, or ground truth comparison.

`--debug-artifacts` may be used to write local ROI crops for manual debugging.
It is off by default and the generated artifacts should not be committed.

Paired runs write `run_metadata.json`, `paired_results.jsonl`,
`current_summary.json`, `history_summary.json`, `pair_summary.json`, and
`report.md`. Reports separate current screenshot OCR, history chart structure,
chart numeric estimates, pair consistency, calibration, and evaluation metrics.

## Error Codes

The stable error vocabulary includes:

- `image_unreadable`
- `image_recognizer_not_configured`
- `unsupported_layout`
- `item_name_missing`
- `best_bid_missing`
- `best_ask_missing`
- `best_bid_mismatch`
- `best_ask_mismatch`
- `bid_ask_swapped`
- `total_bid_quantity_mismatch`
- `total_ask_quantity_mismatch`
- `aggregate_price_misclassified`
- `bid_levels_not_descending`
- `ask_levels_not_ascending`
- `first_bid_not_equal_best_bid`
- `first_ask_not_equal_best_ask`
- `displayed_quantity_sum_mismatch`
- `price_above_market_cap`
- `non_positive_price`
- `low_confidence`
- `ground_truth_invalid`
- `unexpected_exception`
- `pair_current_image_missing`
- `pair_history_image_missing`
- `pair_duplicate_current_image`
- `pair_duplicate_history_image`
- `pair_invalid_filename`
- `pair_item_identity_mismatch`
- `pair_name_mismatch`
- `unexpected_extra_image`
- `price_series_not_detected`
- `volume_series_not_detected`
- `price_volume_series_swapped`
- `price_series_wrong_axis`
- `volume_series_wrong_axis`
- `left_axis_unreadable`
- `right_axis_unreadable`
- `time_axis_unreadable`
- `chart_region_not_detected`
- `order_book_distribution_not_detected`
- `chart_numeric_mapping_unavailable`
- `chart_value_out_of_axis_range`

Exception stack traces are not written to user reports.

## Acceptance Thresholds

Formal CUT-20 acceptance must use `test_scope = end_to_end`.

The default alpha thresholds are:

- 20/20 files processed.
- 20/20 structured results produced.
- `unexpected_error` count is 0.
- At least 19/20 best bid matches.
- At least 19/20 best ask matches.
- At least 18/20 item names match exactly or by safe normalization.
- At least 18/20 total quantity fields match.
- Aggregate price classification accuracy is 100%.
- Bid/ask swapped count is 0.
- Silent acceptance of over-cap prices is 0.
- Unreadable images producing normal forged prices is 0.
- Hard error count is 0.
- At least 18 samples are fully passed.

When a field is absent from ground truth, the denominator is the actual
evaluable count, not 20.

## Current CSV Boundary

CUT-20 does not generate import CSVs. Screenshot `total_bid_quantity` and
`total_ask_quantity` mean displayed item quantities. Existing CSV fields
`bid_count` and `ask_count` cannot yet be assumed to have identical semantics.

Candidate CSV export can only be considered later if an administrator confirms
the mapping or the CSV contract adds explicit quantity fields.

Historical chart estimates also do not generate CSVs and do not write the
database.

## Private Screenshot ZIPs

Private screenshot ZIPs should be handled outside the repository. The ZIP
safety helper accepts only root-level PNG/JPG/JPEG files, rejects path
traversal, rejects symbolic links, and rejects executable/script files. The
current task does not automatically run the user's private 20-image batch; that
requires a separate approval after the framework is verified.
