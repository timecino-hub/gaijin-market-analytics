import assert from "node:assert/strict";
import test from "node:test";
import { generalEntryStatusLabel, generalExitStatusLabel, isGeneralStrategyResponse } from "./general-strategy-display.ts";

function response() {
  return {
    version: "2026-10-01.1", as_of: "2026-10-01T00:00:00Z", research_only: true, automated_trading: false, fee_rate: "0.15",
    policy: { candidate: true, observation_lead_days: 3, quote_check_lead_days: 2, observation_gate: "0.75", buy_cost_cap: "0.55", alternative_cost_cap: "0.60", preferred_net_target: "0.50", minimum_net_target: "0.40", lower_target_after_days: 150, review_after_days: 180, minimum_baseline_days: 5, baseline_method: "MEDIAN_COMPLETE_DAYS_IN_PRE_EVENT_EQUAL_SPAN", default_fee_is_assumption: true, force_liquidation: false, cost_input_basis: "ACTUAL_TOTAL_QUOTED_COST_NOT_DAILY_VWAP" },
    entry: { baseline: null, observation_price: null, quoted_cost: null, observation_threshold: null, buy_cost_ceiling: null, alternative_buy_cost_ceiling: null, observation_on: null, quote_check_on: null, status: "BASELINE_REQUIRED", reason_codes: ["BASELINE_REQUIRED"], fill_claim: false },
    exit: { status: "NOT_EVALUATED", entry_cost: null, bought_at: null, preferred_offer: null, minimum_offer: null, lower_target_at: null, review_at: null, current_target: null, planned_offer: null, force_liquidation: false, fill_claim: false },
    research: { data_as_of: "2026-09-21T12:12:19.625000Z", opportunity_count: 46, cycle_count: 13, rule_count: 90, result_sha256: "hash", execution_basis: "PRICE_PROXY_AND_CONDITION_ONLY", optimality_proven: false, fill_claim: false },
    examples: []
  };
}

test("previous deployment remains compatible during adoption rollout", () => {
  assert.equal(isGeneralStrategyResponse(response()), true);
});

test("adopted general strategy accepts incomplete calculator inputs as an empty plan", () => {
  assert.equal(isGeneralStrategyResponse({
    ...response(),
    version: "2026-10-01.2",
    policy: { ...response().policy, candidate: false, designation: "CURRENT_GENERAL_STRATEGY", adoption_basis: "USER_SELECTED" }
  }), true);
});

test("adoption requires a consistent current designation and user selection", () => {
  const previous = response();
  const adopted = { ...previous.policy, candidate: false, designation: "CURRENT_GENERAL_STRATEGY", adoption_basis: "USER_SELECTED" };
  for (const policy of [
    { ...previous.policy, candidate: false },
    { ...adopted, designation: undefined },
    { ...adopted, designation: "UNPROVEN_CURRENT_RULE" },
    { ...adopted, adoption_basis: undefined },
    { ...adopted, adoption_basis: "STATISTICALLY_OPTIMAL" },
    { ...adopted, candidate: true }
  ]) {
    assert.equal(isGeneralStrategyResponse({ ...previous, policy }), false);
  }
});

test("legacy payload and absent safety semantics cannot become the current general strategy", () => {
  assert.equal(isGeneralStrategyResponse({ version: "1.0.0", horizons: [], exit_frontier: [] }), false);
  assert.equal(isGeneralStrategyResponse({ ...response(), automated_trading: true }), false);
  assert.equal(isGeneralStrategyResponse({ ...response(), exit: { ...response().exit, force_liquidation: true } }), false);
  assert.equal(isGeneralStrategyResponse({ ...response(), research: { ...response().research, fill_claim: true } }), false);
});

test("monetary numbers do not silently enter the string decimal contract", () => {
  assert.equal(isGeneralStrategyResponse({ ...response(), entry: { ...response().entry, baseline: 102.78 } }), false);
  assert.equal(isGeneralStrategyResponse({ ...response(), exit: { ...response().exit, preferred_offer: 83.24 } }), false);
  assert.equal(isGeneralStrategyResponse({ ...response(), fee_rate: "0" }), true);
});

test("status labels distinguish price conditions from actual fills and 180 day review", () => {
  assert.match(generalEntryStatusLabel("PRICE_CONDITIONS_SATISFIED"), /待人工核验成交/);
  assert.match(generalEntryStatusLabel("ENTRY_WINDOW_CLOSED"), /已过/);
  assert.match(generalExitStatusLabel("REVIEW_REQUIRED"), /另评估退出/);
  assert.equal(generalEntryStatusLabel("FUTURE_STATUS"), "待核验");
  assert.equal(generalEntryStatusLabel("constructor"), "待核验");
  assert.equal(isGeneralStrategyResponse({ ...response(), exit: { ...response().exit, status: "constructor" } }), false);
});
