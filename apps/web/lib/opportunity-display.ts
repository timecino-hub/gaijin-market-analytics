import type { OpportunityLiquiditySource } from "./types";

const EXPLANATION_LABELS: Record<string, string> = {
  positive_net_profit: "手续费后净利润为正",
  non_positive_or_unavailable_net_profit: "手续费后利润非正或不可用",
  reviewed_screenshot_liquidity_proxy: "流动性使用人工确认的截图数量",
  snapshot_count_liquidity_proxy: "流动性使用导入快照中的订单计数代理",
  liquidity_proxy_unavailable: "缺少可用的流动性代理",
  stability_unavailable: "价格稳定性数据不足",
  insufficient_observation_count: "有效快照数量不足",
  latest_observation_stale_or_missing: "最新快照过旧或缺失",
  risk_score_unavailable: "风险分无法计算",
  elevated_market_risk: "价差或波动风险偏高",
  eligible_positive_after_fee_opportunity: "满足当前 V1 排名资格",
  not_eligible_for_opportunity_ranking: "不满足当前 V1 排名资格"
};

const LIQUIDITY_LABELS: Record<OpportunityLiquiditySource, string> = {
  reviewed_screenshot_quantity: "人工确认截图数量",
  snapshot_counts: "CSV/导入订单计数",
  unavailable: "无可用代理"
};

export function opportunityExplanationLabel(code: string): string {
  if (code in EXPLANATION_LABELS) {
    return EXPLANATION_LABELS[code];
  }
  if (code.startsWith("analysis_status_")) {
    return `分析状态：${safeFallback(code.slice("analysis_status_".length))}`;
  }
  return safeFallback(code);
}

export function opportunityLiquidityLabel(source: OpportunityLiquiditySource): string {
  return LIQUIDITY_LABELS[source];
}

export function opportunityEligibilityLabel(eligible: boolean): string {
  return eligible ? "可参与排名" : "诊断记录";
}

export function opportunityQuantityLabel(
  bidQuantity: number | null,
  askQuantity: number | null
): string {
  if (bidQuantity === null && askQuantity === null) {
    return "—";
  }
  return `买 ${bidQuantity ?? "—"} / 卖 ${askQuantity ?? "—"}`;
}

function safeFallback(value: string): string {
  return value.replace(/[<>{}"'`]/g, "").replaceAll("_", " ").trim() || "未知说明";
}
