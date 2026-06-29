import assert from "node:assert/strict";
import test from "node:test";
import {
  analysisReasonLabel,
  analysisStatusDescription,
  analysisStatusLabel,
  checkAnalysisResponseContract,
  formatBreakEvenReachableDisplay,
  formatBreakEvenSellPriceDisplay,
  formatCurrencyDisplay,
  formatDecimalDisplay,
  formatDecimalPercent,
  formatScore,
  INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE,
  marketRulesFor
} from "./analysis-display.ts";
import type { CurrentItemAnalysisResponse } from "./analysis-display.ts";
import type { ItemAnalysisResponse } from "./types.ts";

test("analysis status mapping covers known values and safely falls back", () => {
  assert.equal(analysisStatusLabel("ok"), "分析完成");
  assert.match(analysisStatusDescription("insufficient_data"), /数据不足/);
  assert.equal(analysisStatusLabel("<unknown>"), "unknown");
});

test("analysis reason mapping covers known values and safely falls back", () => {
  assert.equal(analysisReasonLabel("insufficient_snapshots"), "可用快照数量不足");
  assert.equal(analysisReasonLabel("insufficient_time_coverage"), "所选周期的数据覆盖不足");
  assert.equal(analysisReasonLabel("no_current_ask"), "缺少有效的当前卖价");
  assert.equal(analysisReasonLabel("no_current_bid"), "缺少有效的当前买价");
  assert.equal(analysisReasonLabel("invalid_price"), "存在无效价格");
  assert.equal(analysisReasonLabel("price_above_market_cap"), "存在超过市场上限的价格");
  assert.equal(analysisReasonLabel("invalid_fee_rate"), "手续费率无效");
  assert.equal(
    analysisReasonLabel("break_even_unreachable_under_market_cap"),
    "市场上限内无法盈亏平衡"
  );
  assert.equal(analysisReasonLabel("stale_latest_snapshot"), "最新市场数据已过期");
  assert.equal(analysisReasonLabel("low_liquidity"), "市场流动性偏低");
  assert.equal(analysisReasonLabel("large_spread"), "买卖价差较大");
  assert.equal(analysisReasonLabel("analysis_completed"), "分析计算已完成");
  assert.equal(analysisReasonLabel("<new_reason>"), "new_reason");
});

test("decimal display keeps null as missing and trims display-only zeros", () => {
  assert.equal(formatDecimalDisplay(null), "—");
  assert.equal(formatDecimalDisplay("12.340000"), "12.34");
  assert.equal(formatDecimalDisplay("0.000000"), "0");
  assert.equal(formatScore("87.500000"), "87.5");
});

test("currency display keeps at least two decimal places without float math", () => {
  assert.equal(formatCurrencyDisplay(null), formatDecimalDisplay(null));
  assert.equal(formatCurrencyDisplay("1.990000"), "1.99");
  assert.equal(formatCurrencyDisplay("1.69"), "1.69");
  assert.equal(formatCurrencyDisplay("0.30"), "0.30");
  assert.equal(formatCurrencyDisplay("0"), "0.00");
});

test("percent display moves decimal point without JavaScript float math", () => {
  assert.equal(formatDecimalPercent("0.125"), "12.5%");
  assert.equal(formatDecimalPercent("0.0001"), "0.01%");
  assert.equal(formatDecimalPercent("-0.0125"), "-1.25%");
  assert.equal(formatDecimalPercent(null), "—");
});

test("market rules display fallback is reserved for static summaries", () => {
  assert.equal(marketRulesFor(null).maximum_listing_price, "2000.00");
  assert.equal(marketRulesFor(null).maximum_sale_proceeds, "1700.00");
});

test("analysis response contract rejects older API responses without market rules", () => {
  const response = analysisResponse({ omitMarketRules: true });
  const contract = checkAnalysisResponseContract(response);

  assert.equal(contract.ok, false);
  if (!contract.ok) {
    assert.equal(contract.message, INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE);
  }
});

test("analysis response contract rejects older API responses without break-even reachability", () => {
  const response = analysisResponse({ omitBreakEvenReachable: true });
  const contract = checkAnalysisResponseContract(response);

  assert.equal(contract.ok, false);
  if (!contract.ok) {
    assert.equal(contract.message, INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE);
  }
});

test("analysis response contract rejects missing maximum net profit field", () => {
  const response = analysisResponse({ omitMaximumNetProfit: true });
  const contract = checkAnalysisResponseContract(response);

  assert.equal(contract.ok, false);
  if (!contract.ok) {
    assert.equal(contract.message, INCOMPATIBLE_ANALYSIS_RESPONSE_MESSAGE);
  }
});

test("older break-even values above the market cap are not accepted as current results", () => {
  const response = analysisResponse({
    breakEvenSellPrice: "2000.02",
    omitBreakEvenReachable: true,
    omitMarketRules: true
  });
  const contract = checkAnalysisResponseContract(response);

  assert.equal(contract.ok, false);
});

test("break-even display handles true, false, and null tri-state values", () => {
  const reachable = currentAnalysisResponse({
    breakEvenReachable: true,
    breakEvenSellPrice: "2000.00"
  });
  const unreachable = currentAnalysisResponse({
    breakEvenReachable: false,
    breakEvenSellPrice: null
  });
  const unknown = currentAnalysisResponse({
    breakEvenReachable: null,
    breakEvenSellPrice: null
  });

  assert.equal(formatBreakEvenReachableDisplay(reachable), "可达");
  assert.equal(formatBreakEvenSellPriceDisplay(reachable), "2000.00 GJN");
  assert.equal(
    formatBreakEvenReachableDisplay(unreachable),
    "在2000.00 GJN市场上限内无法盈亏平衡"
  );
  assert.equal(
    formatBreakEvenSellPriceDisplay(unreachable),
    "在2000.00 GJN市场上限内无法盈亏平衡"
  );
  assert.equal(formatBreakEvenReachableDisplay(unknown), "暂无足够数据计算盈亏平衡价格");
  assert.equal(formatBreakEvenSellPriceDisplay(unknown), "—");
});

test("current analysis response contract passes through safe result type", () => {
  const response = analysisResponse({
    breakEvenReachable: true,
    breakEvenSellPrice: "12.35"
  });
  const contract = checkAnalysisResponseContract(response);

  assert.equal(contract.ok, true);
  if (contract.ok) {
    assert.equal(formatBreakEvenSellPriceDisplay(contract.value), "12.35 GJN");
    assert.equal(marketRulesFor(contract.value).maximum_listing_price, "2000.00");
  }
});

function analysisResponse({
  breakEvenReachable = true,
  breakEvenSellPrice = "14.12",
  omitBreakEvenReachable = false,
  omitMaximumNetProfit = false,
  omitMarketRules = false,
  marketRules = {
    name: "gaijin_market",
    version: "1.0.0",
    maximum_listing_price: "2000.00",
    maximum_sale_proceeds: "1700.00",
    currency_quantum: "0.01"
  }
}: {
  breakEvenReachable?: boolean | null;
  breakEvenSellPrice?: string | null;
  omitBreakEvenReachable?: boolean;
  omitMaximumNetProfit?: boolean;
  omitMarketRules?: boolean;
  marketRules?: ItemAnalysisResponse["effective_inputs"]["market_rules"];
}): ItemAnalysisResponse {
  const response: ItemAnalysisResponse = {
    item_id: 1,
    external_key: "synthetic",
    item_name: "Synthetic",
    effective_inputs: {
      horizon: 7,
      as_of: "2026-06-29T00:00:00Z",
      maximum_snapshot_age_seconds: 86400,
      minimum_snapshot_count: 3,
      fee_policy: {
        name: "gaijin_market",
        version: "1.0.0",
        nominal_fee_rate: "0.15",
        currency_quantum: "0.01",
        proceeds_rounding: "seller_proceeds_round_down"
      }
    },
    status: "ok",
    strategy_name: "rule_based",
    strategy_version: "1.0.0",
    feature_version: "market_features_v1",
    observation_count: 3,
    first_observation_at: "2026-06-22T00:00:00Z",
    last_observation_at: "2026-06-29T00:00:00Z",
    current_ask: "12.00",
    current_bid: "11.00",
    reference_sell_price: "11.00",
    sale_proceeds: "9.35",
    fee_amount: "1.65",
    gross_profit: "-1.00",
    net_profit: "-2.65",
    net_roi: "-0.22",
    break_even_sell_price: breakEvenSellPrice,
    maximum_net_profit: "1688.00",
    spread_absolute: "1.00",
    spread_ratio: "0.09",
    median_bid: "10.00",
    median_ask: "11.00",
    price_volatility: "1.00",
    liquidity_score: "100",
    risk_score: "50",
    confidence_score: "85",
    reason_codes: ["analysis_completed"]
  };
  if (!omitMarketRules) {
    response.effective_inputs.market_rules = marketRules;
  }
  if (!omitBreakEvenReachable) {
    response.break_even_reachable = breakEvenReachable;
  }
  if (omitMaximumNetProfit) {
    delete response.maximum_net_profit;
  }
  return response;
}

function currentAnalysisResponse(
  options: Parameters<typeof analysisResponse>[0]
): CurrentItemAnalysisResponse {
  const contract = checkAnalysisResponseContract(analysisResponse(options));
  if (contract.ok) {
    return contract.value;
  }
  throw new Error(contract.message);
}
