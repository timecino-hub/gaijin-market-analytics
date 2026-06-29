import assert from "node:assert/strict";
import test from "node:test";
import {
  analysisReasonLabel,
  analysisStatusDescription,
  analysisStatusLabel,
  formatCurrencyDisplay,
  formatDecimalDisplay,
  formatDecimalPercent,
  formatScore
} from "./analysis-display.ts";

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
  assert.equal(analysisReasonLabel("invalid_fee_rate"), "手续费率无效");
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
