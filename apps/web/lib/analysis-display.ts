import type {
  AnalysisMarketRules,
  AnalysisReasonCode,
  AnalysisStatus,
  ItemAnalysisResponse
} from "./types";

const MISSING_VALUE = "—";
export const INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE =
  "分析服务版本与当前网页不兼容，请重启或更新API后重试。";

export type CurrentItemAnalysisResponse = ItemAnalysisResponse & {
  effective_inputs: ItemAnalysisResponse["effective_inputs"] & {
    market_rules: AnalysisMarketRules;
  };
  break_even_reachable: boolean | null;
  maximum_net_profit: string | null;
};

export type AnalysisResponseContractCheck =
  | { ok: true; value: CurrentItemAnalysisResponse }
  | { ok: false; message: string };

export const DEFAULT_MARKET_RULES: AnalysisMarketRules = {
  name: "gaijin_market",
  version: "1.0.0",
  maximum_listing_price: "2000.00",
  maximum_sale_proceeds: "1700.00",
  currency_quantum: "0.01"
};

const STATUS_LABELS: Record<AnalysisStatus, string> = {
  ok: "分析完成",
  insufficient_data: "数据不足",
  invalid_input: "输入无效",
  no_recent_market: "最新市场数据过时",
  no_valid_price: "缺少有效价格"
};

const STATUS_DESCRIPTIONS: Record<AnalysisStatus, string> = {
  ok: "当前窗口满足 RuleBasedV1 的基础条件，结果仍只是透明基线参考。",
  insufficient_data: "所选周期内的数据不足，分析结果可能不可用或不完整。",
  invalid_input: "分析输入未通过策略校验。",
  no_recent_market: "最新快照超过允许时间，不能视为当前市场状态。",
  no_valid_price: "缺少可用的当前买价或卖价，无法形成完整参考。"
};

const REASON_LABELS: Record<AnalysisReasonCode, string> = {
  insufficient_snapshots: "可用快照数量不足",
  insufficient_time_coverage: "所选周期的数据覆盖不足",
  no_current_ask: "缺少有效的当前卖价",
  no_current_bid: "缺少有效的当前买价",
  invalid_price: "存在无效价格",
  price_above_market_cap: "存在超过市场上限的价格",
  invalid_fee_rate: "手续费率无效",
  break_even_unreachable_under_market_cap: "市场上限内无法盈亏平衡",
  stale_latest_snapshot: "最新市场数据已过期",
  low_liquidity: "市场流动性偏低",
  large_spread: "买卖价差较大",
  analysis_completed: "分析计算已完成"
};

export function analysisStatusLabel(status: string): string {
  return status in STATUS_LABELS ? STATUS_LABELS[status as AnalysisStatus] : safeFallback(status);
}

export function analysisStatusDescription(status: string): string {
  return status in STATUS_DESCRIPTIONS
    ? STATUS_DESCRIPTIONS[status as AnalysisStatus]
    : "分析返回了当前前端尚未识别的状态，请以 reason codes 和后端实际输入为准。";
}

export function analysisReasonLabel(reasonCode: string): string {
  return reasonCode in REASON_LABELS
    ? REASON_LABELS[reasonCode as AnalysisReasonCode]
    : safeFallback(reasonCode);
}

export function formatDecimalDisplay(value: string | null, maxFractionDigits = 6): string {
  return formatDecimalWithScale(value, maxFractionDigits, 0);
}

export function formatCurrencyDisplay(value: string | null): string {
  return formatDecimalWithScale(value, 6, 2);
}

function formatDecimalWithScale(
  value: string | null,
  maxFractionDigits: number,
  minFractionDigits: number
): string {
  if (value === null) {
    return MISSING_VALUE;
  }

  const parsed = splitPlainDecimal(value);
  if (!parsed) {
    return value;
  }

  return joinDecimal(
    parsed.sign,
    parsed.whole,
    trimFraction(parsed.fraction, maxFractionDigits, minFractionDigits)
  );
}

export function formatDecimalPercent(value: string | null, maxFractionDigits = 4): string {
  if (value === null) {
    return MISSING_VALUE;
  }

  const parsed = splitPlainDecimal(value);
  if (!parsed) {
    return `${value}%`;
  }

  const shifted = shiftDecimal(parsed.whole, parsed.fraction, 2);
  return `${joinDecimal(
    parsed.sign,
    shifted.whole,
    trimFraction(shifted.fraction, maxFractionDigits, 0)
  )}%`;
}

export function formatScore(value: string | null): string {
  return formatDecimalDisplay(value, 2);
}

export function checkAnalysisResponseContract(
  result: ItemAnalysisResponse
): AnalysisResponseContractCheck {
  const marketRules = result.effective_inputs?.market_rules;
  if (
    !isObject(marketRules) ||
    typeof marketRules.maximum_listing_price !== "string" ||
    typeof marketRules.maximum_sale_proceeds !== "string" ||
    typeof marketRules.currency_quantum !== "string" ||
    typeof marketRules.name !== "string" ||
    typeof marketRules.version !== "string"
  ) {
    return { ok: false, message: INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE };
  }

  if (
    !hasOwn(result, "break_even_reachable") ||
    (result.break_even_reachable !== true &&
      result.break_even_reachable !== false &&
      result.break_even_reachable !== null)
  ) {
    return { ok: false, message: INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE };
  }

  if (
    !hasOwn(result, "maximum_net_profit") ||
    (typeof result.maximum_net_profit !== "string" && result.maximum_net_profit !== null)
  ) {
    return { ok: false, message: INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE };
  }

  return { ok: true, value: result as CurrentItemAnalysisResponse };
}

export function marketRulesFor(result: CurrentItemAnalysisResponse | null): AnalysisMarketRules {
  return result?.effective_inputs.market_rules ?? DEFAULT_MARKET_RULES;
}

export function formatBreakEvenReachableDisplay(result: CurrentItemAnalysisResponse): string {
  if (result.break_even_reachable === true) {
    return "可达";
  }
  if (result.break_even_reachable === false) {
    return `在${formatCurrencyDisplay(
      marketRulesFor(result).maximum_listing_price
    )} GJN市场上限内无法盈亏平衡`;
  }
  return "暂无足够数据计算盈亏平衡价格";
}

export function formatBreakEvenSellPriceDisplay(result: CurrentItemAnalysisResponse): string {
  if (result.break_even_reachable === true) {
    return `${formatCurrencyDisplay(result.break_even_sell_price)} GJN`;
  }
  if (result.break_even_reachable === false) {
    return `在${formatCurrencyDisplay(
      marketRulesFor(result).maximum_listing_price
    )} GJN市场上限内无法盈亏平衡`;
  }
  return MISSING_VALUE;
}

function safeFallback(value: string): string {
  return value.replace(/[<>{}"'`]/g, "").trim() || "未知状态";
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function hasOwn(value: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function splitPlainDecimal(value: string):
  | {
      sign: "" | "-";
      whole: string;
      fraction: string;
    }
  | null {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) {
    return null;
  }

  return {
    sign: match[1] === "-" ? "-" : "",
    whole: stripLeadingZeros(match[2]),
    fraction: match[3] ?? ""
  };
}

function shiftDecimal(whole: string, fraction: string, places: number): { whole: string; fraction: string } {
  const digits = `${whole}${fraction}`.padEnd(whole.length + places, "0");
  const decimalIndex = whole.length + places;
  return {
    whole: stripLeadingZeros(digits.slice(0, decimalIndex)),
    fraction: digits.slice(decimalIndex)
  };
}

function joinDecimal(sign: "" | "-", whole: string, fraction: string): string {
  const unsigned = fraction ? `${whole}.${fraction}` : whole;
  return unsigned === "0" ? unsigned : `${sign}${unsigned}`;
}

function trimFraction(fraction: string, maxFractionDigits: number, minFractionDigits: number): string {
  const trimmed = fraction.slice(0, maxFractionDigits).replace(/0+$/, "");
  return trimmed.padEnd(minFractionDigits, "0");
}

function stripLeadingZeros(value: string): string {
  return value.replace(/^0+(?=\d)/, "") || "0";
}
