import type { GeneralStrategyResponse } from "./types";

const ENTRY_STATUS_LABELS: Record<string, string> = {
  BASELINE_REQUIRED: "等待活动前参考价",
  EVENT_DATE_REQUIRED: "等待活动结束日期",
  OBSERVATION_NOT_COMPLETE: "观察日尚未结束",
  ENTRY_WINDOW_CLOSED: "买价检查日已过",
  OBSERVATION_PRICE_REQUIRED: "等待观察日均成交价",
  OBSERVATION_GATE_FAILED: "观察日均成交价超过75%上限",
  QUOTED_COST_REQUIRED: "观察价达标 · 等待买入总价",
  COST_CAP_EXCEEDED: "买入总价超过55%上限",
  PRICE_CONDITIONS_SATISFIED: "买价符合条件 · 成交未确认"
};

const EXIT_STATUS_LABELS: Record<GeneralStrategyResponse["exit"]["status"], string> = {
  NOT_EVALUATED: "等待实际买入花费与买入时间",
  TARGET50: "扣费后赚50%",
  TARGET40: "满150天 · 扣费后赚40%",
  REVIEW_REQUIRED: "满180天 · 重新选择退出时机"
};

export function generalEntryStatusLabel(status: string): string {
  return Object.hasOwn(ENTRY_STATUS_LABELS, status) ? ENTRY_STATUS_LABELS[status] : "待核验";
}

export function generalExitStatusLabel(status: GeneralStrategyResponse["exit"]["status"]): string {
  return Object.hasOwn(EXIT_STATUS_LABELS, status) ? EXIT_STATUS_LABELS[status] : "待核验";
}

/** Accept the previous deployment during rollout, while requiring explicit adoption for the current designation. */
export function isGeneralStrategyResponse(value: unknown): value is GeneralStrategyResponse {
  if (!isRecord(value) || value.research_only !== true || value.automated_trading !== false) {
    return false;
  }
  if (typeof value.version !== "string" || typeof value.as_of !== "string" || !isDecimal(value.fee_rate)) {
    return false;
  }
  const { policy, entry, exit, research } = value;
  if (!isRecord(policy) || !isRecord(entry) || !isRecord(exit) || !isRecord(research)) {
    return false;
  }
  return (
    ((policy.candidate === true && policy.designation === undefined && policy.adoption_basis === undefined) ||
      (policy.candidate === false && policy.designation === "CURRENT_GENERAL_STRATEGY" && policy.adoption_basis === "USER_SELECTED")) &&
    policy.force_liquidation === false &&
    policy.default_fee_is_assumption === true &&
    typeof policy.cost_input_basis === "string" &&
    ["observation_gate", "buy_cost_cap", "alternative_cost_cap", "preferred_net_target", "minimum_net_target"].every((key) => isDecimal(policy[key])) &&
    ["observation_lead_days", "quote_check_lead_days", "lower_target_after_days", "review_after_days", "minimum_baseline_days"].every((key) => Number.isInteger(policy[key])) &&
    typeof policy.baseline_method === "string" &&
    ["baseline", "observation_price", "quoted_cost", "observation_threshold", "buy_cost_ceiling", "alternative_buy_cost_ceiling"].every((key) => isNullableDecimal(entry[key])) &&
    ["observation_on", "quote_check_on"].every((key) => isNullableString(entry[key])) &&
    typeof entry.status === "string" &&
    entry.fill_claim === false &&
    Array.isArray(entry.reason_codes) && entry.reason_codes.every((code) => typeof code === "string") &&
    ["entry_cost", "preferred_offer", "minimum_offer", "current_target", "planned_offer"].every((key) => isNullableDecimal(exit[key])) &&
    ["bought_at", "lower_target_at", "review_at"].every((key) => isNullableString(exit[key])) &&
    typeof exit.status === "string" && Object.hasOwn(EXIT_STATUS_LABELS, exit.status) &&
    exit.force_liquidation === false && exit.fill_claim === false &&
    typeof research.data_as_of === "string" &&
    Number.isInteger(research.opportunity_count) &&
    Number.isInteger(research.cycle_count) &&
    Number.isInteger(research.rule_count) &&
    typeof research.result_sha256 === "string" &&
    typeof research.execution_basis === "string" &&
    research.optimality_proven === false &&
    research.fill_claim === false &&
    Array.isArray(value.examples)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDecimal(value: unknown): value is string {
  return typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value);
}

function isNullableDecimal(value: unknown): value is string | null {
  return value === null || isDecimal(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}
