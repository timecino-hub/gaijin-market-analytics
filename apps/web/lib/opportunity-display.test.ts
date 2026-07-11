import assert from "node:assert/strict";
import test from "node:test";
import {
  opportunityEligibilityLabel,
  opportunityExplanationLabel,
  opportunityLiquidityLabel,
  opportunityQuantityLabel
} from "./opportunity-display.ts";

test("opportunity explanation labels cover V1 codes and sanitize unknown codes", () => {
  assert.equal(opportunityExplanationLabel("positive_net_profit"), "手续费后净利润为正");
  assert.equal(
    opportunityExplanationLabel("analysis_status_insufficient_data"),
    "分析状态：insufficient data"
  );
  assert.equal(opportunityExplanationLabel("<future_code>"), "future code");
});

test("opportunity liquidity and eligibility labels are explicit", () => {
  assert.equal(opportunityLiquidityLabel("reviewed_screenshot_quantity"), "人工确认截图数量");
  assert.equal(opportunityEligibilityLabel(true), "可参与排名");
  assert.equal(opportunityEligibilityLabel(false), "诊断记录");
});

test("opportunity quantity label does not invent missing values", () => {
  assert.equal(opportunityQuantityLabel(null, null), "—");
  assert.equal(opportunityQuantityLabel(12, null), "买 12 / 卖 —");
  assert.equal(opportunityQuantityLabel(12, 7), "买 12 / 卖 7");
});
