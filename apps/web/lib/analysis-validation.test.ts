import assert from "node:assert/strict";
import test from "node:test";
import {
  datetimeLocalToIso,
  isoToDateTimeLocal,
  validateAnalysisForm,
  validateAnalysisHorizon
} from "./analysis-validation.ts";

test("validateAnalysisHorizon accepts only supported analysis windows", () => {
  assert.deepEqual(validateAnalysisHorizon("7"), { ok: true, value: "7" });
  assert.deepEqual(validateAnalysisHorizon("30"), { ok: true, value: "30" });
  assert.deepEqual(validateAnalysisHorizon("90"), { ok: true, value: "90" });
  assert.deepEqual(validateAnalysisHorizon("180"), { ok: true, value: "180" });
  assert.equal(validateAnalysisHorizon("8").ok, false);
});

test("validateAnalysisForm returns horizon and omits empty as_of", () => {
  const result = validateAnalysisForm({ horizon: "30", asOfLocal: "" });

  assert.deepEqual(result, {
    ok: true,
    value: { horizon: "30" }
  });
});

test("datetimeLocalToIso converts local datetime through Date and rejects invalid values", () => {
  const result = datetimeLocalToIso("2026-06-29T08:30");

  assert.equal(result.ok, true);
  assert.match(result.ok ? result.value ?? "" : "", /^2026-06-29T/);
  assert.equal(datetimeLocalToIso("").ok, true);
  assert.equal(datetimeLocalToIso("Invalid Date").ok, false);
  assert.equal(datetimeLocalToIso("2026-02-31T08:30").ok, false);
});

test("isoToDateTimeLocal safely restores timezone-aware URL values for the control", () => {
  assert.equal(isoToDateTimeLocal("not-a-date"), "");
  assert.match(isoToDateTimeLocal("2026-06-29T00:00:00Z"), /^2026-06-29T/);
});
