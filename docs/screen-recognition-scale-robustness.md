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
