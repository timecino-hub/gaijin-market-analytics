# Home and general strategy visual refinement

The user requests continued aesthetic improvements to the homepage and general strategy page, consistent with the vehicle potential page. They explicitly selected a new ChatGPT cloud work task so execution can continue while their computer is off. Implement the frontend changes and verify them; this is a scoped continuation of the existing site, not a new design exploration or a new analytics model.

## Starting point

- Repository: https://github.com/timecino-hub/gaijin-market-analytics
- Branch: `feat/general-rule-20261001`
- Current production implementation commit: `7c44489280db072da9b7ac2cdebeed9d0180e7de`.
- Public site: https://bvvd-analysis.duckdns.org
- Visual reference: `/opportunities`; supporting document: `docs/website-aesthetics-2026-10-15.md`.
- September 28 Astra work was rejected. Do not pull it into this branch. The present branch preserves the accepted historical baseline and later user-approved releases.

## Work to implement

Refine `apps/web/app/page.tsx`, `apps/web/app/strategy/page.tsx` and `apps/web/app/strategy/general-rule-panel.tsx`. Use `apps/web/app/visual-refinement.css` and the shared `market-header.tsx` to maintain consistency. Consult `art-direction.css` and `globals.css` for existing cascade behavior. Make focused changes, avoiding an unrelated framework, dependency or navigation rewrite.

For the homepage, improve the hierarchy of its heading, search, featured vehicle, dated historical price, recent directory and useful navigation links. Balance the vehicle image with the accompanying data, and make missing order books look deliberate without fabricating a price. Remove redundant technical prose where appropriate. Keep search, real data dates, source links and empty/error states working.

For general strategy, give the four action steps, calculator inputs and buy/sell outputs a clear shared layout. Group inputs according to the user's task; make the relevant results easy to scan. Preserve all existing GET parameters, query values and server calculations. Keep old strategy evidence separate and only requested when `legacy=1`; the default page must not fetch the expensive old ledger.

The visual language uses ivory `#f0ece3`, paper `#faf8f2`, ink `#182327`, copper `#b96741` and dark copper `#995333`. Use readable Chinese interface type, tabular numbers, restrained 8px card corners, thin dividers, consistent 44px or larger controls and visible focus. Site width is capped at 1320px. The user prefers a single column for potential cards, and does not want added explanatory snippets or introductory filler. Website copy should use plain Chinese and complete action rules.

## Preserve the accepted facts and behavior

The current general strategy is version `2026-10-01.2`, adopted by the user rather than proven statistically optimal. Observe three days before activity close; the observation gate is 75% of the activity reference price, while the actual total buy cost cap is 55%. Prefer net 50% take-profit; after 150 days, use net 40% if still unfilled; at 180 days research a later exit rather than force liquidation. Default fee is 15%. Keep all values, Decimal calculations, fees and timing unchanged. The fee label must not reintroduce the word "假设".

Potential version 1.1.0, source identity bindings, own-history median, percentile bar, sorting and dated historical comparison remain unchanged. The production data has 55 items and 80,583 historical buckets, with latest complete record day 2026-09-21; it is visibly stale historical data. There is no authorization for automatic trading or Marketplace access. Only the site's existing public read-only endpoints may be used for this preview.

All 55 vehicle images are committed local public assets, with exact official Wiki mappings. Verified display names are already implemented, including SPz 12-3 LGS, M8 LAC and M8A1 GMC. Reuse the helper and images; do not replace them with guessed names or generated pictures.

## Cloud validation and delivery

Use Node 24 and pnpm 11.7.0 from the repository configuration. Install with the committed lockfile. Run `pnpm web:test`, `pnpm web:lint`, `pnpm --filter @gaijin-market-analytics/web typecheck` and `pnpm web:build`. The starting implementation passes 148 web tests; lint has one existing image warning in the unrelated screen-recognition workspace. Avoid claiming a complete Python suite for a frontend-only change.

For a live-data preview, set `API_BASE_URL=https://bvvd-analysis.duckdns.org` (the client appends `/api/v1`). If network access is unavailable, use clearly marked local fixtures matching the existing contracts; never commit a private database or fabricate production data. Review desktop, tablet and phone sizes and loading, empty, error and keyboard-focus states. Save screenshots of both improved pages. Avoid unnecessary dependencies and new image downloads; preserve lazy loading and fast navigation.

Return a reviewable code diff or draft pull request, screenshots and checks. Do not claim a production rollout unless an existing authorized cloud deployment mechanism is actually available and verified. The laptop's SSH key, local knowledge database, local caches and ignored fixture files are not transferred. The current production release is already healthy and should not be reverted. No production secrets or broad new access should be requested just to complete frontend implementation.
