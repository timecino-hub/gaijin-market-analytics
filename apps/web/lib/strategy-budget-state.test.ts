import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_BUDGET, budgetFromParams } from "./strategy-budget-state.ts";

test("a usable budget is kept and normalised", () => {
  assert.equal(budgetFromParams({ budget: "250" }).budget, "250");
  assert.equal(budgetFromParams({ budget: " 250 " }).budget, "250");
  assert.equal(budgetFromParams({ budget: "100.00" }).budget, "100");
  assert.equal(budgetFromParams({ budget: "12.5" }).budget, "12.5");
});

test("an unusable budget falls back rather than being sent to the API", () => {
  for (const value of ["", "0", "-5", "abc", "NaN", "Infinity"]) {
    assert.equal(budgetFromParams({ budget: value }).budget, DEFAULT_BUDGET, value);
  }
  assert.equal(budgetFromParams({}).budget, DEFAULT_BUDGET);
});

test("a repeated parameter takes the first value", () => {
  assert.equal(budgetFromParams({ budget: ["300", "400"] }).budget, "300");
});

test("the query carries exactly what the page resolved", () => {
  assert.deepEqual(budgetFromParams({ budget: "250" }).query, { budget: "250" });
  assert.deepEqual(budgetFromParams({}).query, { budget: DEFAULT_BUDGET });
});
