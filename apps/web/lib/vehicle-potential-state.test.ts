import assert from "node:assert/strict";
import test from "node:test";
import { vehiclePotentialStateFromParams } from "./vehicle-potential-state.ts";

test("vehicle comparisons preserve URL controls without forwarding them to the API", () => {
  const state = vehiclePotentialStateFromParams({
    q: "  TIS MA  ", sort: "median_room", room: "50",
    as_of: "2026-09-21T12:12:19Z", ranked_only: "true", include_inactive: "true"
  });
  assert.deepEqual(state.form, {
    q: "TIS MA", sort: "median_room", room: "50", asOf: "2026-09-21T12:12:19Z",
    rankedOnly: true, includeInactive: true
  });
  assert.deepEqual(state.query, {
    as_of: "2026-09-21T12:12:19Z", ranked_only: "true", include_inactive: "true"
  });
});

test("invalid controls retain the historical price-position default", () => {
  const state = vehiclePotentialStateFromParams({
    sort: "profit_prediction", room: "100", ranked_only: "yes",
    q: "x".repeat(81), as_of: "x".repeat(81)
  });
  assert.equal(state.form.sort, "percentile");
  assert.equal(state.form.room, "all");
  assert.equal(state.form.rankedOnly, false);
  assert.equal(state.form.q, "");
  assert.equal(state.query.as_of, undefined);
});

test("repeated query arguments consistently use their first value", () => {
  const state = vehiclePotentialStateFromParams({
    sort: ["freshness", "median_room"], room: ["40", "50"], q: ["M8", "TIS"],
    include_inactive: []
  });
  assert.equal(state.form.sort, "freshness");
  assert.equal(state.form.room, "40");
  assert.equal(state.form.q, "M8");
  assert.equal(state.form.includeInactive, false);
});
