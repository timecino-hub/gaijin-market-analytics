import assert from "node:assert/strict";
import test from "node:test";
import {
  cleanOpportunityQuery,
  opportunityListStateFromParams,
  opportunityPageQuery
} from "./opportunity-list-state.ts";

test("opportunity list state applies reproducible defaults", () => {
  const state = opportunityListStateFromParams({});

  assert.deepEqual(state.query, {
    horizon: "30",
    page: "1",
    page_size: "25",
    eligible_only: "true",
    min_score: "0",
    include_inactive: "false",
    as_of: undefined,
    search: undefined,
    category: undefined,
    rarity: undefined
  });
  assert.equal(state.form.eligibleOnly, true);
});

test("opportunity list state sanitizes invalid query values", () => {
  const state = opportunityListStateFromParams({
    horizon: "8",
    page: "0",
    page_size: "500",
    min_score: "101",
    eligible_only: "yes",
    include_inactive: "1",
    search: "  alpha  "
  });

  assert.equal(state.query.horizon, "30");
  assert.equal(state.query.page, "1");
  assert.equal(state.query.page_size, "25");
  assert.equal(state.query.min_score, "0");
  assert.equal(state.query.eligible_only, "true");
  assert.equal(state.query.include_inactive, "false");
  assert.equal(state.query.search, "alpha");
});

test("pagination preserves filters and changes only page", () => {
  const state = opportunityListStateFromParams({
    horizon: "90",
    page: "3",
    min_score: "55.5",
    category: "vehicle",
    eligible_only: "false"
  });

  assert.deepEqual(opportunityPageQuery(state.query, 4), {
    horizon: "90",
    page: "4",
    page_size: "25",
    eligible_only: "false",
    min_score: "55.5",
    category: "vehicle",
    include_inactive: "false"
  });
});

test("clean query omits empty optional values", () => {
  assert.deepEqual(
    cleanOpportunityQuery({
      horizon: "7",
      search: undefined,
      category: "",
      page: "1"
    }),
    { horizon: "7", page: "1" }
  );
});
