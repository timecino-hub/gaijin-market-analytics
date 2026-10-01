import type { VehicleEventRecord, VehiclePotentialItem, VehicleTargetReference } from "./types";
import type { PotentialSort, VehiclePotentialListState } from "./vehicle-potential-state";

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
 * Visual coordinate on a percentile axis, not a linear monetary price range.
 *
 * The percentile is already 0..1 over this vehicle's own completed days, so it
 * maps straight onto the track. A missing or unparsable value yields null and
 * the caller omits the gauge rather than drawing an empty one that reads as
 * "cheapest ever".
 */
export function pricePercentileBar(percentile: string | null): number | null {
  if (percentile === null || !/^\d+(?:\.\d+)?$/.test(percentile)) {
    return null;
  }
  const upperBound = compareDecimalStrings(percentile, "1");
  if (upperBound === null || upperBound > 0) {
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
    return `${item.completed_event_count} 次活动 · 无完整收益记录`;
  }
  const positive = item.positive_outcome_count ?? 0;
  const base = `${item.closed_outcome_count} 次观察期完整 · ${positive} 次净空间为正`;
  return isThinEvidence(item) ? `${base} · 样本不足3次` : base;
}

export function catalogNoteFor(item: VehiclePotentialItem): string | null {
  return item.catalog_match_status === "MATCHED"
    ? null
    : "活动目录未绑定";
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

const SORT_LABELS: Record<PotentialSort, string> = {
  percentile: "历史价格分位：低到高",
  median_room: "到历史中位价净空间：高到低",
  recent_volume: "近30天日成交量中位数：高到低",
  freshness: "成交日期：新到旧"
};

export function potentialSortLabel(sort: PotentialSort): string {
  return SORT_LABELS[sort];
}

/** Validate the basis before using an optional new field from an older API. */
export function targetReferenceFor(item: VehiclePotentialItem): VehicleTargetReference | null {
  const target = item.target_reference;
  if (item.state !== "RANKED" || !target || target.cost_basis !== "LATEST_COMPLETED_DAY_AVERAGE_PROXY") {
    return null;
  }
  return [target.reference_cost, target.fee_rate, target.preferred_offer,
    target.minimum_offer, target.median_net_roi].every((value) => parseDecimal(value) !== null)
    ? target : null;
}

export function selectVehiclePotentialItems(
  items: readonly VehiclePotentialItem[],
  form: VehiclePotentialListState["form"]
): VehiclePotentialItem[] {
  const query = form.q.toLocaleLowerCase("en-US");
  const selected = items.filter((item) => {
    if (query && ![item.item_name, item.external_key, item.category ?? "", item.matched_catalog_name ?? ""]
      .some((value) => value.toLocaleLowerCase("en-US").includes(query))) {
      return false;
    }
    if (form.rankedOnly && item.state !== "RANKED") return false;
    if (form.room === "all") return true;
    const target = targetReferenceFor(item);
    return form.room === "50"
      ? target?.median_supports_preferred === true && target.preferred_within_market_cap === true
      : target?.median_supports_minimum === true && target.minimum_within_market_cap === true;
  });
  return selected.sort((left, right) => {
    const stateOrder = Number(left.state !== "RANKED") - Number(right.state !== "RANKED");
    if (stateOrder) return stateOrder;
    let order = 0;
    if (form.sort === "percentile") {
      order = compareOptionalDecimals(left.price_percentile, right.price_percentile, false);
    } else if (form.sort === "median_room") {
      order = compareOptionalDecimals(targetReferenceFor(left)?.median_net_roi ?? null,
        targetReferenceFor(right)?.median_net_roi ?? null, true);
    } else {
      const leftValue = form.sort === "recent_volume" ? left.recent_daily_volume : left.price_age_days;
      const rightValue = form.sort === "recent_volume" ? right.recent_daily_volume : right.price_age_days;
      order = compareOptionalCounts(leftValue, rightValue, form.sort === "recent_volume");
    }
    return order || left.item_id - right.item_id;
  });
}

/** Exact comparison; monetary and ratio strings never pass through Number. */
export function compareDecimalStrings(left: string, right: string): number | null {
  const first = parseDecimal(left);
  const second = parseDecimal(right);
  if (!first || !second) return null;
  const scale = Math.max(first.scale, second.scale);
  const leftValue = first.integer * BigInt(10) ** BigInt(scale - first.scale);
  const rightValue = second.integer * BigInt(10) ** BigInt(scale - second.scale);
  return leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0;
}

function parseDecimal(value: unknown): { integer: bigint; scale: number } | null {
  if (typeof value !== "string" || value.length > 1024) return null;
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return null;
  const fraction = match[3] ?? "";
  const integer = BigInt(`${match[2]}${fraction}`) * BigInt(match[1] ? -1 : 1);
  return { integer, scale: fraction.length };
}

function compareOptionalDecimals(left: string | null, right: string | null, descending: boolean): number {
  const first = parseDecimal(left);
  const second = parseDecimal(right);
  if (!first || !second) return !first && !second ? 0 : !first ? 1 : -1;
  const comparison = compareDecimalStrings(left!, right!)!;
  return descending ? -comparison : comparison;
}

function compareOptionalCounts(left: number | null, right: number | null, descending: boolean): number {
  const first = left !== null && Number.isSafeInteger(left) && left >= 0 ? left : null;
  const second = right !== null && Number.isSafeInteger(right) && right >= 0 ? right : null;
  if (first === null || second === null) return first === second ? 0 : first === null ? 1 : -1;
  return descending ? second - first : first - second;
}

export function eventOutcomeLabel(record: VehicleEventRecord): string {
  switch (record.outcome_status) {
    case "OBSERVED": return "观察期完整";
    case "PENDING_HORIZON": return "观察期未满";
    case "MISSING_EXIT_PRICE": return "到期成交价缺失";
    case "NO_ENTRY_PRICE": return "活动后买价参考缺失";
    default: return record.outcome_closed ? "观察期完整" : "结果未完整";
  }
}

export function hasObservedEventOutcome(record: VehicleEventRecord): boolean {
  return record.outcome_status === undefined ? record.outcome_closed : record.outcome_status === "OBSERVED";
}
