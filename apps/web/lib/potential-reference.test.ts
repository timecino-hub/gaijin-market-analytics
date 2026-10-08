import assert from "node:assert/strict";
import test from "node:test";
import { potentialReferenceQuery } from "./potential-reference.ts";
import type { MarketDataStatus } from "./types";

const status: MarketDataStatus = {
  item_count: 34, historical_bucket_count: 54133, daily_record_count: 48907,
  latest_complete_day: "2026-09-21T00:00:00Z", latest_capture_at: "2026-09-21T12:12:19Z",
  reference_as_of: "2026-09-22T00:00:00Z", price_age_days: 16, stale: true
};

test("stale default compares at explicit stored-data reference time", () => {
  assert.deepEqual(potentialReferenceQuery({ ranked_only: "true" }, status), {
    ranked_only: "true", as_of: status.reference_as_of
  });
});

test("fresh data also uses its explicit record reference; explicit times and unavailable status are preserved", () => {
  const exact = { as_of: "2026-09-21T12:12:19.625Z" };
  assert.equal(potentialReferenceQuery(exact, status), exact);
  const current = {};
  assert.deepEqual(potentialReferenceQuery(current, { ...status, stale: false }), { as_of: status.reference_as_of });
  assert.equal(potentialReferenceQuery(current, null), current);
  assert.equal(potentialReferenceQuery(current, { ...status, reference_as_of: null }), current);
});
