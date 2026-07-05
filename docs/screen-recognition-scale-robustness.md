# Screen Recognition Scale Robustness

This document records the local-only scale-robustness work for current-market
screenshots. It does not change the Local Extension Bridge upload contract, does
not write market data, and does not train or load machine-learning models.

## Current Scale Sensitivity

The current recognition path is:

```text
browser extension or manual upload
-> POST /api/v1/local-recognition/extension-reviews or /reviews
-> validate PNG/JPEG bytes and decoded dimensions
-> create in-memory review
-> process_review_image background task
-> api.screen_recognition Windows OCR backend
-> layout profile ROIs
-> OCR field evidence
-> parse_ocr_contract
-> pending_review
-> manual confirm/reject/unreadable
```

The public upload contract remains multipart `file` plus the existing extension
metadata fields. The extension is not required to send browser zoom.

The current profile already uses normalized coordinates, not a single
screenshot's absolute pixel crop. Scale sensitivity mainly came from the OCR
stage: normalized ROIs were converted to pixels, cropped, and then enlarged by a
fixed factor before Windows OCR. A browser zoom or layout reflow can change text
pixel height, row spacing, and table density inside the same normalized ROI, so
one fixed crop scale and one preprocessing path is brittle.

## Fixed Coordinates Audit

Current crop-like code:

- `apps/api/src/api/screen_recognition/layouts.py:roi_to_pixels`: normalized
  coordinates converted to image-relative pixels.
- `apps/api/src/api/screen_recognition/roi.py:resolve_roi_pixels`: normalized
  coordinates, clamped and validated before crop use.
- `apps/api/src/api/screen_recognition/windows_ocr.ps1`: normalized ROI values
  from the layout profile are converted to source-image pixels, then rendered
  into bounded OCR candidate images.
- `apps/api/src/api/screen_recognition/history_analysis.py`: chart ROIs use
  normalized coordinates, then Pillow crops or pixel scans within those
  image-relative regions.

No current-market bid/ask ROI is intentionally defined as absolute pixels. The
profile remains a tested normalized fallback. Visual-anchor detection is not yet
used for the current-market order book; if future real Edge/Chrome fixtures show
responsive reflow that normalized ROIs cannot handle, stable header or border
anchors should be evaluated before any model-based detector.

## ROI Strategy

All layout ROIs are centralized in `api.screen_recognition.layouts`. Pixel
resolution now goes through explicit validation:

- clamp to image bounds;
- reject empty regions;
- reject too-small regions;
- reject abnormal ROI aspect ratios;
- return stable error codes such as `roi_out_of_bounds`, `roi_too_small`, and
  `roi_aspect_ratio_invalid`.

A clamped ROI produces a warning and remains review-only evidence. The system
must not rely on Pillow or System.Drawing implicit crop behavior to hide bad
coordinates.

## OCR Preprocessing

`normalize_ocr_roi` defines bounded preprocessing for OCR-sized crops. The
Windows OCR helper uses the same metadata and tries a fixed, explainable set of
candidate pipelines:

- `gray_3x`
- `gray_autocontrast_4x`
- `binary_4x`
- `inverted_binary_4x`

The helper records the selected pipeline in field warnings. Windows OCR does
not expose confidence, so confidence remains unavailable; the selection rule is
field format first, then non-empty text. It does not fabricate confidence.

## Production OCR Backend

The production `windows-ocr` alias now uses the button-anchored price-cell v4
profile (`windows-media-ocr-price-cells-v4`). This profile anchors compact
bid/ask price cells from the large order buttons and accepts both inactive gray
and active green sell-button states.

The production alias still runs eight OCR attempts per screenshot. If the button
anchor cannot be detected, recognition falls back to the normalized ROI path and
requires review instead of treating the result as confident. Item-name or
quantity recognition issues can still make the overall review require manual
confirmation even when bid and ask prices are exact.

Older backends remain explicitly selectable for regression and diagnostics,
including `candidate-price-cells-v2`, `candidate-price-cells-v3`, and
`candidate-price-cells-v4`.

## Price Rules

Prices remain `Decimal` values. Valid recognized prices must be non-empty,
numeric, finite, no more than two decimal places, and within:

```text
0.01 <= price <= 2000.00
```

Potential missing-decimal outputs such as `1810` can create a structured
suggestion like `18.10`, but the parsed value remains `None` and review is
required. The parser does not silently rewrite OCR output.

Order-book validation keeps page order intact. Bid levels are expected high to
low; ask levels are expected low to high. Sorting checks only create errors,
warnings, or confidence evidence. They never reorder OCR rows.

## Private Fixtures

Private browser screenshots belong only under an ignored local directory:

```text
artifacts/private/screen-recognition-evaluation/
  edge/
    080/
    090/
    100/
    110/
    125/
  chrome/
    080/
    090/
    100/
    110/
    125/
```

Use filenames such as `sample-a.png`; do not include account names, tokens,
pairing codes, URLs, or other private identifiers. `artifacts/private/` is in
`.gitignore`.

Run local evaluation from `apps/api`:

```sh
uv run python -m api.screen_recognition.evaluate \
  --input ../../artifacts/private/screen-recognition-evaluation \
  --output ../../artifacts/private/screen-recognition-evaluation/report.json \
  --pretty
```

If no private fixtures exist, the tool writes a report with:

```text
no private evaluation fixtures found
```

It does not pretend that real Edge or Chrome zoom was tested.

### Private Ground Truth

Generate the local CSV template before judging accuracy:

```sh
uv run python -m api.screen_recognition.evaluate \
  --input ../../artifacts/private/screen-recognition-evaluation \
  --create-ground-truth-template
```

The template is written to:

```text
artifacts/private/screen-recognition-evaluation/ground-truth.csv
```

This file is ignored private data. It must not be committed. Fixture discovery
only accepts paired files where `sample.png` has a same-name `sample.json`
metadata file. Report files, private report files, diagnostics files, ROI crops,
or historical debug JSON are not metadata and are not fixtures.

Each row uses an anonymous `fixture_id`; the template does not store full local
paths, URLs, slugs, item names, account data, tokens, screenshot content, or OCR
output. Existing rows are preserved as-is. Re-running the command only appends
new fixture rows and leaves already reviewed human values untouched.

The user fills at least:

```text
expected_best_bid
expected_best_ask
reviewed
```

Prices must be Decimal strings exactly as displayed, for example `18.20`,
`18.10`, `0.01`, or `2000.00`. Valid prices are finite, use at most two decimal
places, and stay within `0.01 <= price <= 2000.00`. If a side is truly not
visible in the screenshot, use `not_visible` or `not_applicable`; do not use `0`
for missing values.

Optional fields can record visible row counts and top bid/ask values in display
order:

```text
expected_bid_count
expected_ask_count
expected_top_bid_values
expected_top_ask_values
```

Use semicolon-separated Decimal values for top value lists. Rows with
`reviewed=false` are validated but are excluded from accuracy statistics.

### Accuracy Reports

After ground truth is reviewed, run:

```sh
uv run python -u -m api.screen_recognition.evaluate \
  --input ../../artifacts/private/screen-recognition-evaluation \
  --output ../../artifacts/private/screen-recognition-evaluation/report.json \
  --ground-truth ../../artifacts/private/screen-recognition-evaluation/ground-truth.csv \
  --pretty
```

The safe `report.json` contains anonymous fixture IDs, browser, zoom, status,
error code, stage reason, selected preprocessing pipeline, timings, and
aggregate accuracy. It intentionally omits expected prices, recognized prices,
raw OCR text, screenshots, full paths, URLs, item names, slugs, tokens, and
base64 content.

The private `report.private.json` and `diagnostics.html` are written in the
ignored private directory. They may include expected/recognized price
comparisons, local screenshot previews, ROI crop previews, and raw OCR debug
details for local diagnosis only.

Accuracy is grouped by:

```text
overall
browser
zoom
sample_label
layout_profile
preprocessing_pipeline
```

Reported metrics include exact bid/ask matches, both-side exact match, missing
outputs, wrong values, and false-confident errors. A false-confident error means
the OCR produced a wrong value while `requires_review=false`; this is the highest
risk local evaluation metric.

### Profiling

Use `--profile` for stage timing and OCR invocation counts:

```sh
uv run python -u -m api.screen_recognition.evaluate \
  --input ../../artifacts/private/screen-recognition-evaluation \
  --output ../../artifacts/private/screen-recognition-evaluation/report.json \
  --only-browser chrome \
  --limit 1 \
  --verbose \
  --profile \
  --ocr-timeout-seconds 90
```

Profile output includes PowerShell process count, OCR invocation count,
preprocessing pipelines attempted and completed, whether an early exit was used,
per-pipeline duration, OCR engine initialization time, OCR execution time, total
OCR duration, and total fixture duration. Verbose terminal output remains
anonymous and must not print screenshot content or raw OCR text.

Specific bid/ask missing reasons are recorded separately from the public
compatible error codes. Examples include `bid_roi_empty`, `bid_ocr_empty`,
`bid_price_parse_failed`, `bid_candidates_rejected`, `bid_selection_failed`,
`ask_roi_empty`, `ask_ocr_empty`, `ask_price_parse_failed`,
`ask_candidates_rejected`, and `ask_selection_failed`.

## Synthetic Scaling

Synthetic scaling is useful for checking ROI normalization and preprocessing
stability at `0.80`, `0.90`, `1.00`, `1.10`, and `1.25`.

synthetic scaling is not equivalent to browser zoom or responsive layout reflow

Synthetic results must not be reported as real Edge/Chrome zoom results.

## Future Capture Metadata

If normalized ROIs plus bounded preprocessing still fail on real multi-zoom
fixtures, the bridge contract could later add optional capture metadata:

- `browser_zoom`
- `device_pixel_ratio`
- `viewport_width_css`
- `viewport_height_css`

This round only recommends those fields for future evaluation. It does not
change the API or extension contract.

## Why Not Machine Learning

This round keeps recognition deterministic, local, and review-bound. It avoids
PyTorch, TensorFlow, Ultralytics, YOLO, ONNX Runtime, model weights, training
scripts, and annotation tooling. A lightweight region detector should be
considered only after real Edge/Chrome multi-zoom fixtures show that tested
traditional ROI and anchor strategies cannot locate the order-book region
reliably.
