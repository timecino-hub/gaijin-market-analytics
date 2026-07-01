import assert from "node:assert/strict";
import test from "node:test";
import type { LocalRecognitionReview } from "../../lib/types.ts";
import {
  formFromReview,
  mergeReviewsByPolling,
  payloadFromForm,
  validateReviewForm,
  validateScreenshotFile
} from "./review-validation.ts";

test("validateScreenshotFile accepts PNG and JPEG but rejects other extensions", () => {
  assert.equal(validateScreenshotFile({ name: "current.png", size: 10 }).ok, true);
  assert.equal(validateScreenshotFile({ name: "current.jpeg", size: 10 }).ok, true);

  const gif = validateScreenshotFile({ name: "current.gif", size: 10 });
  assert.equal(gif.ok, false);
  if (!gif.ok) {
    assert.equal(gif.code, "unsupported_image_format");
  }
});

test("formFromReview keeps missing OCR quantities blank instead of zero", () => {
  const review = reviewFixture({
    total_bid_quantity: null,
    total_ask_quantity: null
  });

  const form = formFromReview(review);

  assert.equal(form.finalTotalBidQuantity, "");
  assert.equal(form.finalTotalAskQuantity, "");
});

test("payloadFromForm preserves explicit manual zero quantity", () => {
  const form = formFromReview(reviewFixture({ total_bid_quantity: null, total_ask_quantity: null }));
  form.itemKey = "manual-key";
  form.finalTotalAskQuantity = "0";

  const payload = payloadFromForm(form);

  assert.equal(payload.final_total_bid_quantity, null);
  assert.equal(payload.final_total_ask_quantity, 0);
});

test("validateReviewForm requires item identity and legal prices", () => {
  const form = formFromReview(reviewFixture({}));
  form.itemKey = "";
  assert.equal(validateReviewForm(form).ok, false);

  form.itemKey = "manual-key";
  form.finalBestBid = "0";
  const price = validateReviewForm(form);
  assert.equal(price.ok, false);
  if (!price.ok) {
    assert.equal(price.code, "price_out_of_market_range");
  }
});

test("mergeReviewsByPolling does not overwrite dirty form state and reports terminal conflicts", () => {
  const current = reviewFixture({ status: "pending_review", best_bid: "12.34" });
  const next = reviewFixture({ status: "confirmed_with_edits", best_bid: "99.99" });

  const merged = mergeReviewsByPolling(current, next, true);

  assert.equal(merged.conflict, true);
  assert.equal(merged.review?.status, "confirmed_with_edits");
  assert.equal(merged.review?.ocr_candidate.best_bid, "12.34");
});

function reviewFixture(overrides: Partial<LocalRecognitionReview["ocr_candidate"]> & { status?: LocalRecognitionReview["status"] }): LocalRecognitionReview {
  const bid = "best_bid" in overrides ? overrides.best_bid ?? null : "12.34";
  const ask = "best_ask" in overrides ? overrides.best_ask ?? null : "13.00";
  const bidQuantity = "total_bid_quantity" in overrides ? overrides.total_bid_quantity ?? null : 5;
  const askQuantity = "total_ask_quantity" in overrides ? overrides.total_ask_quantity ?? null : 7;
  return {
    review_id: "review_test",
    created_at: "2026-07-01T00:00:00Z",
    expires_at: "2026-07-01T02:00:00Z",
    status: overrides.status ?? "pending_review",
    suggested_observed_at: "2026-07-01T00:00:00Z",
    image: {
      original_filename: "sample.png",
      width: 1200,
      height: 800,
      format: "png"
    },
    recognition: {
      ocr_backend: "windows-ocr",
      ocr_backend_version: "fake",
      layout_name: "gaijin-market-desktop-v1",
      layout_version: "1.2.0",
      config_sha256: "a".repeat(64),
      parser_version: "parser",
      runner_version: "runner",
      processing_duration_ms: 1
    },
    ocr_candidate: {
      item_name_raw: "Synthetic Alpha",
      item_name_normalized: "Synthetic Alpha",
      best_bid: bid,
      best_ask: ask,
      total_bid_quantity: bidQuantity,
      total_ask_quantity: askQuantity
    },
    draft: {
      selected_item_id: null,
      item_key: null,
      final_item_name: "Synthetic Alpha",
      final_best_bid: bid,
      final_best_ask: ask,
      final_total_bid_quantity: bidQuantity,
      final_total_ask_quantity: askQuantity,
      observed_at: "2026-07-01T00:00:00Z",
      observed_at_source: "review_created_default",
      reviewer_note: null
    },
    ocr_evidence_summary: {
      fields: {},
      confidence_source: "unavailable",
      confidence_available: false
    },
    warnings: [],
    errors: [],
    candidate: null,
    confirmed_at: null,
    rejected_at: null
  };
}
