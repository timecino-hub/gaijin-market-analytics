# Independent review and fixes

Reviewed `b4b55fea6300456051e1239c285533c2a6dd41e4` against the required handoff `d9451cad64`. Code/data and CSS reviews were performed independently, followed by browser reproduction of populated order books.

## Findings resolved

1. **P2 — current quotes disappeared on tablets.** The new <=1100px rule hid the homepage catalog's current buy/sell cells at 769–1100px, where the baseline had displayed them. The original live screenshots contained only missing books, so they did not expose this regression. The fix adds a labelled second row for actual quotes at those widths, with the status on its own row. Missing books and the baseline <=768px compact presentation retain their behavior.
2. **P3 — phone calculator action occupied only half a row.** The button width was 100% of a cell, but the parent retained an inherited two-column grid. At 390px the button measured 153px inside a 316px action area. The parent now has one column; the button measures 316px and keeps its 44px height.

No additional logic, value, formula, fee, GET parameter, source/image identity or legacy-loading regression was found. No backend files changed. The review does not constitute a production deployment.

## Revalidation

- Chromium against a local production build with clearly marked local review fixtures. Quotes 12.34 / 56.78 GJN are **synthetic rendering inputs**, not market data; they were never sent to the public API or production database. The normal public catalog and strategy responses were replayed locally for rendering context.
- Widths 1440, 1100, 820 and 769px: current buy/sell visible after the fix; 768, 390 and 320px retain baseline compact behavior. Stale featured-book status remains visible. No horizontal overflow at any of the seven widths.
- `pnpm web:test`: 148/148 passed. `pnpm web:lint`: zero errors, one unchanged image warning. Web typecheck, production build and `git diff --check`: passed. Node 24.19.0 and pnpm 11.7.0.
- Before/after assertions: [before-checks.json](before-checks.json), [after-checks.json](after-checks.json).
- A second CSS review verified the corrected selector precedence and populated/missing/mobile/desktop branches.

The parent directory screenshots document the initial PR; the screenshots below document the reviewed corrections.

[Before: populated tablet](before-populated-tablet.png) · [After: populated tablet](after-populated-tablet.png) · [After: phone calculator](after-calculator-mobile.png)

![Populated quotes restored on tablet](after-populated-tablet.png)
