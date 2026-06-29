import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./[itemId]/analysis-panel.tsx", import.meta.url), "utf8");

test("analysis controls snapshot event values before state updates", () => {
  assert.match(source, /const nextHorizon = event\.currentTarget\.value;/);
  assert.match(source, /horizon: nextHorizon/);
  assert.doesNotMatch(source, /horizon: event\.currentTarget\.value/);

  assert.match(source, /const nextAsOfLocal = event\.currentTarget\.value;/);
  assert.match(source, /asOfLocal: nextAsOfLocal/);
  assert.doesNotMatch(source, /asOfLocal: event\.currentTarget\.value/);
});

test("successful analysis responses pass contract validation before rendering", () => {
  assert.match(source, /const contract = checkAnalysisResponseContract\(response\);/);
  assert.match(source, /setResult\(null\);[\s\S]*setDisplayError\(contract\.message\);[\s\S]*setPhase\("analysis_status"\);/);
  assert.match(source, /setResult\(contract\.value\);/);
});

test("manual submit keeps at most one direct same-url analysis request", () => {
  const directRuns = source.match(/void runAnalysis\(validation\.value\);/g) ?? [];
  assert.equal(directRuns.length, 1);
});
