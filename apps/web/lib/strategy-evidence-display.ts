import type { BudgetRung, ExitRule, HorizonEvidence, ReturnSummary } from "./types";

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

/** A budget as a round coin amount — nobody holds 100.0000 GJN. */
export function coinLabel(value: string | null): string {
  const parsed = toNumber(value);
  if (parsed === null) {
    return MISSING_VALUE;
  }
  return `${Number.isInteger(parsed) ? parsed : parsed.toFixed(2)} GJN`;
}

/**
 * How many whole units a budget bought, and what it could not reach.
 *
 * The unreachable count is the point of the row: an opportunity priced above
 * the budget was never available, however well it went afterwards.
 */
export function reachLabel(rung: BudgetRung): string {
  const missed = rung.total_count - rung.affordable_count;
  if (rung.total_count === 0) {
    return "无已结算机会";
  }
  return missed === 0
    ? `${rung.affordable_count}/${rung.total_count} 全部买得起`
    : `${rung.affordable_count}/${rung.total_count}，${missed} 次买不起`;
}

export function unitsLabel(units: number | null): string {
  return units === null ? MISSING_VALUE : `${units} 份`;
}

/**
 * Whether a budget can hold more than one position at a time.
 *
 * Below that, portfolio construction has nothing to construct, and what the
 * reader faces is the single-trade distribution rather than an average.
 */
export function holdsOnePositionOnly(rung: BudgetRung | null): boolean {
  return rung !== null && rung.median_units !== null && rung.median_units <= 1;
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
