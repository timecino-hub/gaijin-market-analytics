import type { VehiclePotentialItem } from "./types";

const MISSING_VALUE = "—";

const STATE_LABELS: Record<string, string> = {
  RANKED: "已定位",
  INSUFFICIENT_HISTORY: "历史不足",
  NO_CURRENT_PRICE: "现价过旧"
};

const STATE_DESCRIPTIONS: Record<string, string> = {
  RANKED: "已定位",
  INSUFFICIENT_HISTORY: "完整成交日不足 120 天，不计算分位",
  NO_CURRENT_PRICE: "最后成交价超过 14 天，不作为现价"
};

const EXPLANATION_LABELS: Record<string, string> = {
  vehicle_potential_descriptive_not_a_forecast: "位置描述，不是预测",
  vehicle_potential_near_the_bottom_of_its_own_range: "处于自身历史区间底部",
  vehicle_potential_near_the_top_of_its_own_range: "处于自身历史区间顶部",
  vehicle_potential_not_enough_trading_history: "成交历史不足，无法定位",
  vehicle_potential_latest_price_is_stale: "最新成交价已过期",
  vehicle_potential_no_completed_event_record: "还没有已结束的活动记录",
  vehicle_potential_event_record_too_small_to_generalise:
    "活动样本太少，不足以推广"
};

/** Below this many closed outcomes an event aggregate is not worth reading as a rate. */
export const THIN_EVIDENCE_THRESHOLD = 3;

export function vehicleStateLabel(state: string): string {
  return STATE_LABELS[state] ?? safeFallback(state);
}

export function vehicleStateDescription(state: string): string {
  return STATE_DESCRIPTIONS[state] ?? safeFallback(state);
}

export function vehicleExplanationLabel(code: string): string {
  return EXPLANATION_LABELS[code] ?? safeFallback(code);
}

/**
 * Bar position for the price gauge, as a percentage of the vehicle's own range.
 *
 * The percentile is already 0..1 over this vehicle's own completed days, so it
 * maps straight onto the track. A missing or unparsable value yields null and
 * the caller omits the gauge rather than drawing an empty one that reads as
 * "cheapest ever".
 */
export function pricePercentileBar(percentile: string | null): number | null {
  if (percentile === null) {
    return null;
  }
  const parsed = Number(percentile);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
    return null;
  }
  return parsed * 100;
}

/**
 * The percentile restated as the count it came from.
 *
 * A share is easy to read as a probability; the count of days is the thing
 * that was actually measured, so the card carries both.
 */
export function pricePositionLabel(item: VehiclePotentialItem): string {
  if (item.price_percentile === null) {
    return vehicleStateDescription(item.state);
  }
  const days = item.history_day_count;
  const atOrBelow = Math.round(Number(item.price_percentile) * days);
  return `${days} 个成交日中 ${atOrBelow} 天不高于现价`;
}

/**
 * Whether an event aggregate rests on too few closed outcomes to read as a rate.
 *
 * A vehicle in this cohort has between one and five completed events, so this
 * is true far more often than not. That is the point: the page states the
 * sample size everywhere and refuses to let a median of two stand unqualified.
 */
export function isThinEvidence(item: VehiclePotentialItem): boolean {
  return item.closed_outcome_count < THIN_EVIDENCE_THRESHOLD;
}

export function eventEvidenceLabel(item: VehiclePotentialItem): string {
  if (item.completed_event_count === 0) {
    return "无已结束的活动";
  }
  if (item.closed_outcome_count === 0) {
    return `${item.completed_event_count} 次已结束 · 持有期均未走完`;
  }
  const positive = item.positive_outcome_count ?? 0;
  const base = `${item.closed_outcome_count} 次已结算 · ${positive} 次为正`;
  return isThinEvidence(item) ? `${base} · 样本过少，不成规律` : base;
}

export function catalogNoteFor(item: VehiclePotentialItem): string | null {
  return item.catalog_match_status === "MATCHED"
    ? null
    : "未绑定活动目录，无活动记录；价格定位不受影响";
}

export function volumeLabel(median: number | null, recent: number | null): string {
  if (median === null && recent === null) {
    return MISSING_VALUE;
  }
  return `${median ?? MISSING_VALUE} / ${recent ?? MISSING_VALUE}`;
}

function safeFallback(value: string): string {
  return value.replace(/[<>{}"'`]/g, "").replaceAll("_", " ").trim() || "未知说明";
}
