import type { PostEventEntrySignal } from "./types";

const MISSING_VALUE = "—";

const STATE_LABELS: Record<string, string> = {
  ENTRY_CANDIDATE: "入场候选",
  SHALLOW_DRAWDOWN: "回撤不足",
  ENTRY_WINDOW_CLOSED: "入场窗口已关闭",
  SUPERSEDED_BY_NEXT_SUPPLY_EVENT: "已被新一轮活动取代",
  NO_COMPLETED_EVENT: "尚无已结束的活动",
  NO_PRE_EVENT_BASELINE: "缺少活动前基准",
  NO_POST_EVENT_PRICE: "活动后尚无完整成交日"
};

// Every refusal says what is missing, so a reader never has to guess whether a
// blank row means "no signal" or "no data".
const STATE_DESCRIPTIONS: Record<string, string> = {
  ENTRY_CANDIDATE: "活动结束后的现价已跌到门槛以下，规则把它列为候选。",
  SHALLOW_DRAWDOWN: "窗口仍然开着，但现价没有跌到门槛以下，规则不列为候选。",
  ENTRY_WINDOW_CLOSED: "距离活动结束已超过入场窗口，这一轮不再评估。",
  SUPERSEDED_BY_NEXT_SUPPLY_EVENT: "上一轮的窗口还没走完，新一轮供给已经开始，旧窗口作废。",
  NO_COMPLETED_EVENT: "活动目录里这台载具还没有任何已结束的活动。",
  NO_PRE_EVENT_BASELINE: "活动开始前的成交日不足，没有可比的基准价，规则拒绝估算。",
  NO_POST_EVENT_PRICE: "活动结束后还没有完整的成交日，没有可用的现价。"
};

const EXPLANATION_LABELS: Record<string, string> = {
  postevent_entry_drawdown_gate_passed: "回撤达到门槛",
  postevent_entry_drawdown_gate_failed: "回撤未达门槛",
  postevent_entry_rule_research_only_not_execution_or_fill:
    "仅供研究，不是成交或挂单保证",
  postevent_entry_evidence_is_descriptive_not_walk_forward_validated:
    "证据为描述性统计，未经严格时点验证",
  postevent_entry_window_has_closed: "入场窗口已过",
  postevent_entry_past_the_first_post_close_day: "已晚于活动结束后第 1 天",
  postevent_entry_annotated_only: "仅标注，不过滤任何结果",
  postevent_entry_no_completed_supply_event: "没有已结束的活动可供比较",
  postevent_entry_no_pre_event_baseline: "活动前成交日不足，无法建立基准",
  postevent_entry_no_completed_day_since_the_event_closed:
    "活动结束后还没有完整成交日",
  postevent_entry_superseded_by_a_newer_supply_event: "更新的一轮活动已经开始",
  strategy_withheld_for_shallow_post_event_drawdown: "回撤不足，策略结果被保留"
};

const MATCH_STATUS_LABELS: Record<string, string> = {
  MATCHED: "已绑定活动目录",
  UNMATCHED: "未绑定活动目录",
  AMBIGUOUS: "目录匹配存在歧义"
};

export type PostEventEntryTone = "candidate" | "open" | "closed" | "unbound";

export function postEventStateLabel(state: string | null): string {
  if (state === null) {
    return "无法评估";
  }
  return STATE_LABELS[state] ?? safeFallback(state);
}

export function postEventStateDescription(state: string | null): string {
  if (state === null) {
    return "活动目录没有绑定这台载具，规则没有可比的活动窗口，因此不产生任何读数。";
  }
  return (
    STATE_DESCRIPTIONS[state] ??
    "该状态由后端规则给出，前端不对其含义做推测。"
  );
}

export function postEventExplanationLabel(code: string): string {
  return EXPLANATION_LABELS[code] ?? safeFallback(code);
}

export function catalogMatchLabel(status: string): string {
  return MATCH_STATUS_LABELS[status] ?? safeFallback(status);
}

export function postEventEntryTone(signal: PostEventEntrySignal): PostEventEntryTone {
  if (signal.is_entry_candidate) {
    return "candidate";
  }
  if (signal.state === null) {
    return "unbound";
  }
  return signal.state === "SHALLOW_DRAWDOWN" ? "open" : "closed";
}

/** Candidates first, then the freshest window, then a stable id tiebreak. */
export function comparePostEventEntry(
  left: PostEventEntrySignal,
  right: PostEventEntrySignal
): number {
  if (left.is_entry_candidate !== right.is_entry_candidate) {
    return left.is_entry_candidate ? -1 : 1;
  }
  const leftDays = left.days_since_close ?? Number.MAX_SAFE_INTEGER;
  const rightDays = right.days_since_close ?? Number.MAX_SAFE_INTEGER;
  if (leftDays !== rightDays) {
    return leftDays - rightDays;
  }
  return left.item_id - right.item_id;
}

export function daysSinceCloseLabel(days: number | null): string {
  if (days === null) {
    return MISSING_VALUE;
  }
  if (days === 0) {
    return "当天";
  }
  return `${days} 天前`;
}

/**
 * Bar width for the drawdown gauge, as a percentage of the pre-event baseline.
 *
 * The baseline is the full track, so a ratio above 1 is clamped rather than
 * overflowing the bar. An unparsable or missing ratio yields null, and the
 * caller omits the gauge instead of drawing an empty one that reads as zero.
 */
export function drawdownGaugePercent(ratio: string | null): number | null {
  if (ratio === null) {
    return null;
  }
  const parsed = Number(ratio);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return null;
  }
  return Math.min(100, parsed * 100);
}

/**
 * A price a reader could actually type into the market, rounded UP to 0.01.
 *
 * The rule returns the exact quotient ``cost * 1.25 / 0.85``, which carries far
 * more digits than the 0.01 GJN quantum. Rounding up rather than to nearest
 * keeps the realised net ROI at or above the 25% target; rounding down would
 * quietly publish a price that misses it. Arithmetic is exact — the value is a
 * decimal string and never becomes a float.
 */
export function formatTakeProfitPrice(value: string | null): string {
  if (value === null) {
    return MISSING_VALUE;
  }
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) {
    return value;
  }
  const [, sign, whole, fraction = ""] = match;
  let unit = whole;
  let cents = Number(fraction.padEnd(2, "0").slice(0, 2));
  // A negative target is not something the rule produces, but a carry would
  // round it the wrong way, so only overshoot in the positive direction.
  if (/[1-9]/.test(fraction.slice(2)) && sign !== "-") {
    cents += 1;
    if (cents === 100) {
      cents = 0;
      unit = incrementDigits(unit);
    }
  }
  return `${sign}${stripLeadingZeros(unit)}.${String(cents).padStart(2, "0")}`;
}

/** Add one to a non-negative integer string of any length, exactly. */
function incrementDigits(value: string): string {
  const digits = value.split("");
  for (let index = digits.length - 1; index >= 0; index -= 1) {
    if (digits[index] !== "9") {
      digits[index] = String(Number(digits[index]) + 1);
      return digits.join("");
    }
    digits[index] = "0";
  }
  return `1${digits.join("")}`;
}

function stripLeadingZeros(value: string): string {
  return value.replace(/^0+(?=\d)/, "");
}

/**
 * What the list actually represents.
 *
 * ``total`` is the API's ``item_count``, which is the count AFTER the
 * candidates-only filter is applied — so while that filter is on it is the
 * number shown, not the number evaluated, and the wording must not claim
 * otherwise.
 */
export function entryCandidateSummary(
  count: number,
  total: number,
  candidatesOnly = false
): string {
  if (candidatesOnly) {
    return count === 0
      ? "当前只显示入场候选，而目前没有任何载具满足条件；去掉筛选可以看到每一台的读数与原因。"
      : `当前只显示入场候选，共 ${count} 台；未达门槛和窗口已关闭的载具已被筛选隐藏。`;
  }
  if (total === 0) {
    return "目前没有可评估的载具。";
  }
  if (count === 0) {
    return `本次评估 ${total} 台载具，没有一台处于入场窗口内且回撤达到门槛。`;
  }
  return `本次评估 ${total} 台载具，其中 ${count} 台是入场候选。`;
}

function safeFallback(value: string): string {
  return value.replace(/[<>{}"'`]/g, "").replaceAll("_", " ").trim() || "未知说明";
}
