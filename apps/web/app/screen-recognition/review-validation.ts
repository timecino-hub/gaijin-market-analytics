import type {
  LocalRecognitionDraftPayload,
  LocalRecognitionReview,
  LocalRecognitionReviewList
} from "../../lib/types";

export const MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024;

export type ReviewFormState = {
  identityMode: "manual" | "existing";
  selectedItemId: number | null;
  itemKey: string;
  finalItemName: string;
  manualItemKeyProvided: boolean;
  manualFinalItemNameProvided: boolean;
  finalBestBid: string;
  finalBestAsk: string;
  finalTotalBidQuantity: string;
  finalTotalAskQuantity: string;
  observedAtLocal: string;
  reviewerNote: string;
};

export type ClientValidationResult =
  | { ok: true }
  | { ok: false; code: string; message: string };

export function validateScreenshotFile(file: Pick<File, "name" | "size"> | null): ClientValidationResult {
  if (!file) {
    return { ok: false, code: "missing_file", message: "请选择 PNG 或 JPEG 截图。" };
  }
  if (!hasScreenshotExtension(file.name)) {
    return { ok: false, code: "unsupported_image_format", message: "只支持 PNG 或 JPEG 截图。" };
  }
  if (file.size === 0) {
    return { ok: false, code: "empty_file", message: "截图文件为空。" };
  }
  if (file.size > MAX_SCREENSHOT_BYTES) {
    return { ok: false, code: "image_too_large", message: "截图超过 10 MB 限制。" };
  }
  return { ok: true };
}

export function hasScreenshotExtension(filename: string): boolean {
  const lower = filename.trim().toLowerCase();
  return lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg");
}

export function formFromReview(review: LocalRecognitionReview): ReviewFormState {
  return {
    identityMode: review.draft.selected_item_id ? "existing" : "manual",
    selectedItemId: review.draft.selected_item_id,
    itemKey: review.draft.item_key ?? "",
    finalItemName: review.draft.final_item_name ?? "",
    manualItemKeyProvided: isUserProvidedIdentitySource(review.draft.identity_sources.item_key),
    manualFinalItemNameProvided: isUserProvidedIdentitySource(
      review.draft.identity_sources.final_item_name
    ),
    finalBestBid: review.draft.final_best_bid ?? "",
    finalBestAsk: review.draft.final_best_ask ?? "",
    finalTotalBidQuantity:
      review.draft.final_total_bid_quantity === null ? "" : String(review.draft.final_total_bid_quantity),
    finalTotalAskQuantity:
      review.draft.final_total_ask_quantity === null ? "" : String(review.draft.final_total_ask_quantity),
    observedAtLocal: toDatetimeLocal(review.draft.observed_at ?? review.suggested_observed_at),
    reviewerNote: review.draft.reviewer_note ?? ""
  };
}

export function payloadFromForm(form: ReviewFormState): LocalRecognitionDraftPayload {
  const payload: LocalRecognitionDraftPayload = {
    final_item_name: form.finalItemName.trim() || null,
    final_best_bid: form.finalBestBid.trim() || null,
    final_best_ask: form.finalBestAsk.trim() || null,
    final_total_bid_quantity: parseOptionalQuantity(form.finalTotalBidQuantity),
    final_total_ask_quantity: parseOptionalQuantity(form.finalTotalAskQuantity),
    observed_at: fromDatetimeLocal(form.observedAtLocal),
    reviewer_note: form.reviewerNote.trim() || null
  };
  if (form.identityMode === "existing") {
    payload.selected_item_id = form.selectedItemId;
    payload.item_key = null;
  } else {
    payload.selected_item_id = null;
    payload.item_key = form.itemKey.trim() || null;
    if (!form.manualFinalItemNameProvided) {
      delete payload.final_item_name;
    }
  }
  return payload;
}

export function applyReviewFormUpdate(
  current: ReviewFormState,
  values: Partial<ReviewFormState>
): ReviewFormState {
  const next = { ...current, ...values };
  if (values.identityMode === "existing" || values.selectedItemId !== undefined) {
    next.manualItemKeyProvided = false;
    next.manualFinalItemNameProvided = false;
  }
  if (values.identityMode === "manual") {
    next.selectedItemId = null;
  }
  if (values.itemKey !== undefined && next.identityMode === "manual") {
    next.manualItemKeyProvided = values.itemKey.trim().length > 0;
  }
  if (values.finalItemName !== undefined && next.identityMode === "manual") {
    next.manualFinalItemNameProvided = values.finalItemName.trim().length > 0;
  }
  return next;
}

export function validateReviewForm(form: ReviewFormState): ClientValidationResult {
  if (form.identityMode === "existing" && !form.selectedItemId) {
    return { ok: false, code: "item_identity_required", message: "请选择已有商品。" };
  }
  if (
    form.identityMode === "manual" &&
    (
      !form.itemKey.trim() ||
      !form.finalItemName.trim() ||
      !form.manualItemKeyProvided ||
      !form.manualFinalItemNameProvided
    )
  ) {
    return {
      ok: false,
      code: "item_identity_required",
      message: "手工身份需要 item key 和最终商品名称。"
    };
  }
  for (const [label, value] of [
    ["Best bid", form.finalBestBid],
    ["Best ask", form.finalBestAsk]
  ] as const) {
    const result = validatePriceString(value);
    if (!result.ok) {
      return { ok: false, code: result.code, message: `${label}: ${result.message}` };
    }
  }
  for (const [label, value] of [
    ["求购数量", form.finalTotalBidQuantity],
    ["售单数量", form.finalTotalAskQuantity]
  ] as const) {
    const result = validateQuantity(value);
    if (!result.ok) {
      return { ok: false, code: result.code, message: `${label}: ${result.message}` };
    }
  }
  if (!form.observedAtLocal) {
    return { ok: false, code: "observed_at_required", message: "请填写观测时间。" };
  }
  return { ok: true };
}

export function mergeReviewsByPolling(
  current: LocalRecognitionReview | null,
  next: LocalRecognitionReview | null,
  dirty: boolean
): { review: LocalRecognitionReview | null; conflict: boolean } {
  if (!current || !next || current.review_id !== next.review_id) {
    return { review: next, conflict: false };
  }
  if (!dirty) {
    return { review: next, conflict: false };
  }
  const terminal = ["confirmed", "confirmed_with_edits", "rejected", "unreadable", "failed", "expired"];
  if (terminal.includes(next.status) && next.status !== current.status) {
    return { review: { ...current, status: next.status, errors: next.errors, warnings: next.warnings }, conflict: true };
  }
  return {
    review: {
      ...current,
      status: next.status,
      errors: next.errors,
      warnings: next.warnings,
      recognition: next.recognition
    },
    conflict: false
  };
}

export function sortedReviewSections(list: LocalRecognitionReviewList) {
  return {
    pending: list.reviews.filter((review) =>
      ["processing", "pending_review", "failed", "unreadable", "expired"].includes(review.status)
    ),
    finished: list.reviews.filter((review) =>
      ["confirmed", "confirmed_with_edits", "rejected"].includes(review.status)
    )
  };
}

function validatePriceString(value: string): ClientValidationResult {
  if (!value.trim()) {
    return { ok: false, code: "invalid_price_string", message: "价格必填。" };
  }
  if (!/^\d+(?:\.\d{1,2})?$/.test(value.trim())) {
    return { ok: false, code: "invalid_price_string", message: "价格必须是字符串形式的小数。" };
  }
  const parsed = Number(value);
  if (parsed <= 0 || parsed > 2000) {
    return { ok: false, code: "price_out_of_market_range", message: "必须满足 0 < price <= 2000.00。" };
  }
  return { ok: true };
}

function validateQuantity(value: string): ClientValidationResult {
  if (!value.trim()) {
    return { ok: true };
  }
  if (!/^\d+$/.test(value.trim())) {
    return { ok: false, code: "invalid_quantity", message: "数量必须是非负整数或留空。" };
  }
  return { ok: true };
}

function parseOptionalQuantity(value: string): number | null {
  if (!value.trim()) {
    return null;
  }
  return Number.parseInt(value.trim(), 10);
}

export function toDatetimeLocal(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const offsetMs = date.getTimezoneOffset() * 60 * 1000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

export function fromDatetimeLocal(value: string): string | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toISOString();
}

function isUserProvidedIdentitySource(source: string | null): boolean {
  return source === "user_draft" || source === "confirm_request";
}
