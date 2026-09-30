import assert from "node:assert/strict";
import test from "node:test";
import { generalStrategyFromParams, legacyAsOfFromParams } from "./general-strategy-state.ts";

test("general calculator retains exact decimal and timezone strings", () => {
  assert.deepEqual(
    generalStrategyFromParams({
      baseline: " 102.780000000000000001 ",
      quoted_cost: "47.16",
      bought_at: "2025-01-12T09:00:00+09:00",
      fee_rate: "0.15",
      budget: "100",
      as_of: "2026-09-21T00:00:00Z"
    }),
    {
      baseline: "102.780000000000000001",
      quoted_cost: "47.16",
      bought_at: "2025-01-12T09:00:00+09:00",
      fee_rate: "0.15",
      as_of: "2026-09-21T00:00:00Z"
    }
  );
});

test("general calculator omits empty inputs and uses the first repeated value", () => {
  assert.deepEqual(
    generalStrategyFromParams({
      baseline: ["100.00", "1"],
      observation_price: " ",
      entry_cost: undefined,
      event_closes_on: "2026-10-31"
    }),
    { baseline: "100.00", event_closes_on: "2026-10-31" }
  );
});

test("invalid calculator values reach API validation without silent replacement", () => {
  assert.deepEqual(
    generalStrategyFromParams({ baseline: "abc", quoted_cost: "-1", bought_at: "2026-10-01" }),
    { baseline: "abc", quoted_cost: "-1", bought_at: "2026-10-01" }
  );
});

test("legacy as_of remains available independently of calculator inputs", () => {
  assert.equal(legacyAsOfFromParams({ as_of: [" 2026-09-21T00:00:00Z ", "ignored"] }), "2026-09-21T00:00:00Z");
  assert.equal(legacyAsOfFromParams({ as_of: " " }), undefined);
});
