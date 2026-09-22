import type { ExitRule, HorizonEvidence, ReturnSummary } from "./types";

const MISSING_VALUE = "—";

const EVENT_STATE_LABELS: Record<string, string> = {
  EVALUATED: "已评估",
  EVENT_NOT_COMPLETED: "活动未结束",
  NO_PRE_EVENT_BASELINE: "无活动前基准",
  NO_ENTRY_PRICE: "活动后无成交"
};

const HORIZON_STATE_LABELS: Record<string, string> = {
  SETTLED: "已结算",
  PENDING: "持有期未走完",
  SUPERSEDED: "被下一次投放打断",
  NO_EXIT_PRICE: "窗口内无成交"
};

export function eventStateLabel(state: string): string {
  return EVENT_STATE_LABELS[state] ?? safeFallback(state);
}

export function horizonStateLabel(state: string | null): string {
  if (state === null) {
    return MISSING_VALUE;
  }
  return HORIZON_STATE_LABELS[state] ?? safeFallback(state);
}

/**
 * A return, always carrying its sign.
 *
 * `formatDecimalPercent` drops the plus, which reads as a neutral magnitude.
 * Every number in these tables is a gain or a loss and has to look like one.
 */
export function signedPercent(value: string | null, digits = 1): string {
  const parsed = toNumber(value);
  if (parsed === null) {
    return MISSING_VALUE;
  }
  const scaled = parsed * 100;
  const sign = scaled > 0 ? "+" : scaled < 0 ? "−" : "";
  return `${sign}${Math.abs(scaled).toFixed(digits)}%`;
}

/** A share of a population — a hit rate, a support share — never signed. */
export function sharePercent(value: string | null, digits = 1): string {
  const parsed = toNumber(value);
  if (parsed === null) {
    return MISSING_VALUE;
  }
  return `${(parsed * 100).toFixed(digits)}%`;
}

/**
 * How much evidence is behind a number, in the form the reader needs.
 *
 * Both counts, always: the event count on its own overstates the evidence when
 * one vehicle contributes four of them.
 */
export function sampleLabel(summary: ReturnSummary): string {
  if (summary.sample_size === 0) {
    return "无样本";
  }
  return `n=${summary.sample_size} · ${summary.vehicle_count} 台载具`;
}

export function horizonLabel(days: number): string {
  return `${days} 天`;
}

export function exitRuleLabel(rule: ExitRule): string {
  if (rule.kind === "FIXED_HOLD") {
    return "全部持有到期";
  }
  const target = signedPercent(rule.target_net_roi, 0);
  if (rule.kind === "TAKE_PROFIT") {
    return `全部挂单止盈 ${target}`;
  }
  if (rule.kind === "SPLIT_HALF") {
    return `一半止盈 ${target}，一半到期`;
  }
  return safeFallback(rule.kind);
}

export function daysToHitLabel(days: number | null): string {
  return days === null ? MISSING_VALUE : `${days} 天`;
}

export function findHorizon(
  horizons: HorizonEvidence[],
  days: number
): HorizonEvidence | null {
  return horizons.find((value) => value.horizon_days === days) ?? null;
}

/**
 * Whether the median return still rises with every step out in holding time.
 *
 * "Hold longer, earn more" is the reason the strategy holds for six months, and
 * it is a property of the data rather than of the strategy. It is checked on
 * every render so the page stops making the claim the day the data stops
 * supporting it.
 */
export function medianRisesWithHolding(horizons: HorizonEvidence[]): boolean {
  const medians = horizons
    .slice()
    .sort((left, right) => left.horizon_days - right.horizon_days)
    .map((value) => toNumber(value.all_events.median));
  if (medians.length < 2 || medians.some((value) => value === null)) {
    return false;
  }
  return medians.every(
    (value, index) => index === 0 || (value as number) > (medians[index - 1] as number)
  );
}

function toNumber(value: string | null): number | null {
  if (value === null) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function safeFallback(value: string): string {
  return value.replace(/[<>{}"'`]/g, "").replaceAll("_", " ").trim() || "未知说明";
}
