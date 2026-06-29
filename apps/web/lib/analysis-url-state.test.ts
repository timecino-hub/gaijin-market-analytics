import assert from "node:assert/strict";
import test from "node:test";
import {
  initialAnalysisStateFromQuery,
  itemDetailPath,
  mergeAnalysisQuery,
  mergeSnapshotQuery,
  removeSnapshotQuery
} from "./analysis-url-state.ts";

test("initialAnalysisStateFromQuery does not auto-run without submitted horizon", () => {
  const state = initialAnalysisStateFromQuery({});
  assert.equal(state.canAutoRun, false);
  assert.equal(state.error, null);
});

test("initialAnalysisStateFromQuery restores valid horizon and ignores legacy fee_rate", () => {
  const state = initialAnalysisStateFromQuery({
    horizon: "90",
    fee_rate: "0.1500",
    as_of: "2026-06-29T00:00:00Z"
  });

  assert.equal(state.canAutoRun, true);
  assert.deepEqual(state.query, {
    horizon: "90",
    as_of: "2026-06-29T00:00:00Z"
  });
});

test("mergeAnalysisQuery preserves snapshot filters and unknown parameters", () => {
  const params = mergeAnalysisQuery(
    {
      from: "2026-06-01T00:00:00Z",
      to: "2026-06-29T00:00:00Z",
      limit: "500",
      order: "desc",
      custom: "x y"
    },
    { horizon: "7" }
  );

  assert.equal(params.get("from"), "2026-06-01T00:00:00Z");
  assert.equal(params.get("order"), "desc");
  assert.equal(params.get("custom"), "x y");
  assert.equal(params.get("horizon"), "7");
  assert.equal(params.has("fee_rate"), false);
  assert.equal(params.has("as_of"), false);
  assert.equal(itemDetailPath("123", params), `/items/123?${params.toString()}`);
  assert.match(params.toString(), /custom=x\+y/);
});

test("mergeSnapshotQuery preserves analysis parameters", () => {
  const params = mergeSnapshotQuery(
    {
      horizon: "180",
      fee_rate: "0.1500",
      as_of: "2026-06-29T00:00:00Z"
    },
    { from: "2026-06-01T00:00:00Z", order: "asc" }
  );

  assert.equal(params.get("horizon"), "180");
  assert.equal(params.has("fee_rate"), false);
  assert.equal(params.get("as_of"), "2026-06-29T00:00:00Z");
  assert.equal(params.get("from"), "2026-06-01T00:00:00Z");
});

test("removeSnapshotQuery removes only snapshot filters", () => {
  const params = removeSnapshotQuery({
    horizon: "30",
    fee_rate: "0.15",
    from: "x",
    to: "y",
    limit: "10",
    order: "asc"
  });

  assert.equal(params.get("horizon"), "30");
  assert.equal(params.has("fee_rate"), false);
  assert.equal(params.has("from"), false);
  assert.equal(params.has("to"), false);
  assert.equal(params.has("limit"), false);
  assert.equal(params.has("order"), false);
});
