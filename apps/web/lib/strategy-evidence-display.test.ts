import assert from "node:assert/strict";
import test from "node:test";
import {
  daysToHitLabel,
  eventStateLabel,
  exitRuleLabel,
  findHorizon,
  horizonLabel,
  horizonStateLabel,
  medianRisesWithHolding,
  sampleLabel,
  sharePercent,
  signedPercent
} from "./strategy-evidence-display.ts";
import type { ExitRule, HorizonEvidence, ReturnSummary } from "./types.ts";

function summary(overrides: Partial<ReturnSummary> = {}): ReturnSummary {
  return {
    sample_size: 43,
    vehicle_count: 16,
    median: "0.237",
    mean: "0.233",
    support_ratio: "0.837",
    tenth_percentile: "-0.033",
    ninetieth_percentile: "0.454",
    ...overrides
  };
}

function horizon(days: number, overrides: Partial<HorizonEvidence> = {}): HorizonEvidence {
  return {
    horizon_days: days,
    all_events: summary(),
    selected: summary(),
    rejected: summary(),
    pending_count: 0,
    superseded_count: 0,
    ...overrides
  };
}

// --- returns must read as gains or losses ----------------------------------

test("a return always carries its sign", () => {
  assert.equal(signedPercent("0.237"), "+23.7%");
  assert.equal(signedPercent("-0.033"), "−3.3%");
  assert.equal(signedPercent("0"), "0.0%");
  assert.equal(signedPercent(null), "—");
});

test("a share is never signed, because it is not a gain", () => {
  assert.equal(sharePercent("0.837"), "83.7%");
  assert.equal(sharePercent("0.698"), "69.8%");
  assert.equal(sharePercent(null), "—");
});

test("an unparsable number is refused rather than rendered as zero", () => {
  assert.equal(signedPercent("not-a-number"), "—");
  assert.equal(sharePercent("NaN"), "—");
});

test("a column keeps a fixed number of decimals", () => {
  // A ratio of exactly 0.52 must not print as "52%" next to "46.8%".
  assert.equal(sharePercent("0.52"), "52.0%");
  assert.equal(sharePercent("1"), "100.0%");
});

// --- every aggregate has to say how much is behind it -----------------------

test("a sample label carries both the event count and the vehicle count", () => {
  // The event count alone overstates the evidence when one vehicle supplies
  // four of them.
  assert.equal(sampleLabel(summary()), "n=43 · 16 台载具");
  assert.equal(sampleLabel(summary({ sample_size: 0, vehicle_count: 0 })), "无样本");
});

// --- the claim the strategy rests on is checked, not asserted ---------------

test("the holding claim holds only while the medians actually rise", () => {
  const rising = [
    horizon(30, { all_events: summary({ median: "-0.105" }) }),
    horizon(60, { all_events: summary({ median: "-0.065" }) }),
    horizon(90, { all_events: summary({ median: "-0.001" }) }),
    horizon(180, { all_events: summary({ median: "0.102" }) })
  ];
  assert.equal(medianRisesWithHolding(rising), true);

  // One step back and the page must stop claiming it.
  const broken = [
    horizon(30, { all_events: summary({ median: "-0.105" }) }),
    horizon(60, { all_events: summary({ median: "-0.120" }) }),
    horizon(180, { all_events: summary({ median: "0.102" }) })
  ];
  assert.equal(medianRisesWithHolding(broken), false);
});

test("a missing median makes the holding claim unprovable rather than true", () => {
  const incomplete = [
    horizon(30, { all_events: summary({ median: null }) }),
    horizon(180, { all_events: summary({ median: "0.102" }) })
  ];
  assert.equal(medianRisesWithHolding(incomplete), false);
  assert.equal(medianRisesWithHolding([]), false);
});

test("the medians are compared in holding order, not in the order they arrive", () => {
  const shuffled = [
    horizon(180, { all_events: summary({ median: "0.102" }) }),
    horizon(30, { all_events: summary({ median: "-0.105" }) }),
    horizon(90, { all_events: summary({ median: "-0.001" }) })
  ];
  assert.equal(medianRisesWithHolding(shuffled), true);
});

// --- labels -----------------------------------------------------------------

test("exit rules are named by what they do, not by their enum", () => {
  const rule = (overrides: Partial<ExitRule>): ExitRule => ({
    kind: "TAKE_PROFIT",
    target_net_roi: "0.25",
    hit_ratio: "0.698",
    median_days_to_hit: 80,
    summary: summary(),
    ...overrides
  });

  assert.equal(exitRuleLabel(rule({ kind: "FIXED_HOLD", target_net_roi: null })), "全部持有到期");
  assert.equal(exitRuleLabel(rule({})), "全部挂单止盈 +25%");
  assert.equal(exitRuleLabel(rule({ kind: "SPLIT_HALF" })), "一半止盈 +25%，一半到期");
});

test("unknown states from the backend are shown, not guessed at", () => {
  assert.equal(eventStateLabel("NO_ENTRY_PRICE"), "活动后无成交");
  assert.equal(eventStateLabel("SOME_NEW_STATE"), "SOME NEW STATE");
  assert.equal(horizonStateLabel("PENDING"), "持有期未走完");
  assert.equal(horizonStateLabel(null), "—");
});

test("a label never passes markup through from the backend", () => {
  assert.doesNotMatch(eventStateLabel("<script>alert(1)</script>"), /[<>]/);
});

test("days to hit reads as a duration and refuses to invent one", () => {
  assert.equal(daysToHitLabel(80), "80 天");
  assert.equal(daysToHitLabel(null), "—");
  assert.equal(horizonLabel(180), "180 天");
});

test("a horizon is found by its length, and a missing one is null", () => {
  const horizons = [horizon(30), horizon(180)];
  assert.equal(findHorizon(horizons, 180)?.horizon_days, 180);
  assert.equal(findHorizon(horizons, 90), null);
});
