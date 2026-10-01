import assert from "node:assert/strict";
import test from "node:test";
import type { VehicleEventRecord, VehiclePotentialItem, VehicleTargetReference } from "./types.ts";
import {
  compareDecimalStrings, eventOutcomeLabel, hasObservedEventOutcome,
  selectVehiclePotentialItems, targetReferenceFor
} from "./vehicle-potential-display.ts";
import { vehiclePotentialStateFromParams } from "./vehicle-potential-state.ts";

const reference: VehicleTargetReference = {
  cost_basis: "LATEST_COMPLETED_DAY_AVERAGE_PROXY", reference_cost: "47.1645792",
  fee_rate: "0.15", preferred_offer: "83.24", minimum_offer: "77.70", median_net_roi: "0.7",
  median_supports_preferred: true, median_supports_minimum: true,
  preferred_within_market_cap: true, minimum_within_market_cap: true
};

function item(id: number, changes: Partial<VehiclePotentialItem> = {}): VehiclePotentialItem {
  return {
    item_id: id, external_key: `vehicle-${id}`, item_name: `Vehicle ${id}`, category: "坦克",
    catalog_match_status: "MATCHED", matched_catalog_name: null, state: "RANKED",
    current_price: "47.1645792", current_price_day: "2026-09-20T00:00:00Z", price_age_days: 1,
    history_day_count: 120, window_start: "2025-01-01T00:00:00Z", window_end: "2026-09-20T00:00:00Z",
    price_percentile: "0.2", median_price: "102.78", low_price: "40.00", high_price: "140.00",
    ratio_to_median: "0.46", median_daily_volume: 2, recent_daily_volume: 2,
    recent_trading_day_count: 12, target_reference: reference,
    events: [], completed_event_count: 0, closed_outcome_count: 0,
    median_drawdown_ratio: null, median_net_roi: null, positive_outcome_count: null,
    days_since_last_event_close: null, explanation_codes: [], research_only: true, version: "1.1.0",
    ...changes
  };
}

function select(items: VehiclePotentialItem[], params: Record<string, string> = {}) {
  return selectVehiclePotentialItems(items, vehiclePotentialStateFromParams(params).form).map((value) => value.item_id);
}

test("price-position default sorts low first with stable item-ID ties and unreadable vehicles last", () => {
  const items = [item(3), item(2, { price_percentile: "0.02" }), item(1),
    item(4, { state: "NO_CURRENT_PRICE", price_percentile: "0.001" })];
  assert.deepEqual(select(items), [2, 1, 3, 4]);
  assert.deepEqual(items.map((value) => value.item_id), [3, 2, 1, 4]);
});

test("exact Decimal ordering preserves money beyond Number precision and signed near-zero ratios", () => {
  assert.equal(compareDecimalStrings("9007199254740992.99", "9007199254740993.00"), -1);
  assert.equal(compareDecimalStrings("0.00000000000000000000000001", "0"), 1);
  assert.equal(compareDecimalStrings("-0.00000000000000000000000001", "0"), -1);
  assert.equal(compareDecimalStrings("00012.2000", "12.2"), 0);
  assert.equal(compareDecimalStrings("-2", "-1.9"), -1);
  assert.equal(compareDecimalStrings("-0.00", "0"), 0);
  assert.equal(compareDecimalStrings("NaN", "0"), null);
});

test("historical-median room sorts descending, including negative room and missing old-API fields", () => {
  const nearZero = (roi: string) => ({ ...reference, median_net_roi: roi });
  assert.deepEqual(select([
    item(1, { target_reference: nearZero("-0.00000000000000000001") }),
    item(2, { target_reference: undefined }),
    item(3, { target_reference: nearZero("0.00000000000000000001") }),
    item(4, { target_reference: nearZero("0") })
  ], { sort: "median_room" }), [3, 4, 1, 2]);
});

test("null and invalid comparable values stay last even in descending order", () => {
  assert.deepEqual(select([
    item(1, { recent_daily_volume: null }), item(2, { recent_daily_volume: 2 }),
    item(3, { recent_daily_volume: 9 }), item(4, { recent_daily_volume: -1 })
  ], { sort: "recent_volume" }), [3, 2, 1, 4]);
  assert.deepEqual(select([
    item(1, { price_age_days: null }), item(2, { price_age_days: 9 }), item(3, { price_age_days: 1 })
  ], { sort: "freshness" }), [3, 2, 1]);
});

test("search matches names, identifiers, categories and bound catalog names without changing API data", () => {
  const items = [item(1, { item_name: "TIS MA" }), item(2, { external_key: "aubl-74" }),
    item(3, { category: "飞机", matched_catalog_name: "P-59A" })];
  assert.deepEqual(select(items, { q: "tis" }), [1]);
  assert.deepEqual(select(items, { q: "AUBL" }), [2]);
  assert.deepEqual(select(items, { q: "飞机" }), [3]);
  assert.deepEqual(select(items, { q: "p-59" }), [3]);
  assert.deepEqual(select(items, { q: "not-present" }), []);
});

test("50-percent filter requires historical median support and an offer within the market cap", () => {
  const items = [item(1), item(2, { target_reference: { ...reference, median_supports_preferred: false } }),
    item(3, { target_reference: { ...reference, preferred_within_market_cap: false } }),
    item(4, { target_reference: null }), item(5, { state: "INSUFFICIENT_HISTORY" })];
  assert.deepEqual(select(items, { room: "50" }), [1]);
  assert.deepEqual(select(items, { room: "40" }), [1, 2, 3]);
});

test("40-percent filter also enforces its own historical support and market cap", () => {
  const items = [item(1), item(2, { target_reference: { ...reference, minimum_within_market_cap: false } }),
    item(3, { target_reference: { ...reference, median_supports_minimum: false } })];
  assert.deepEqual(select(items, { room: "40" }), [1]);
});

test("old API and unknown target bases remain unavailable rather than showing zero room", () => {
  assert.equal(targetReferenceFor(item(1, { target_reference: undefined })), null);
  assert.equal(targetReferenceFor(item(1, { target_reference: null })), null);
  assert.equal(targetReferenceFor(item(1, { target_reference: { ...reference, median_net_roi: "NaN" } })), null);
  const unknown = { ...reference, cost_basis: "UNKNOWN" } as unknown as VehicleTargetReference;
  assert.equal(targetReferenceFor(item(1, { target_reference: unknown })), null);
  assert.deepEqual(select([item(1), item(2, { state: "NO_CURRENT_PRICE" })], { ranked_only: "true" }), [1]);
});

test("historical status distinguishes elapsed observation from missing endpoint and missing entry", () => {
  const record: VehicleEventRecord = {
    event_key: "history", closes_at: "2025-01-01T00:00:00Z", pre_event_baseline: "100.00",
    entry_price: "50.00", drawdown_ratio: "0.5", holding_days: 180,
    exit_price: null, net_roi: null, outcome_closed: false
  };
  assert.equal(eventOutcomeLabel({ ...record, outcome_status: "MISSING_EXIT_PRICE" }), "到期成交价缺失");
  assert.equal(eventOutcomeLabel({ ...record, outcome_status: "PENDING_HORIZON" }), "观察期未满");
  assert.equal(eventOutcomeLabel({ ...record, outcome_status: "NO_ENTRY_PRICE" }), "活动后买价参考缺失");
  assert.equal(hasObservedEventOutcome({ ...record, outcome_status: "MISSING_EXIT_PRICE" }), false);
  assert.equal(hasObservedEventOutcome({ ...record, outcome_closed: true, outcome_status: "OBSERVED" }), true);
  assert.equal(eventOutcomeLabel(record), "结果未完整");
  assert.equal(hasObservedEventOutcome({ ...record, outcome_closed: true }), true);
});
