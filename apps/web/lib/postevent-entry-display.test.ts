import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogMatchLabel,
  comparePostEventEntry,
  daysSinceCloseLabel,
  drawdownGaugePercent,
  entryCandidateSummary,
  formatTakeProfitPrice,
  postEventEntryTone,
  postEventExplanationLabel,
  postEventStateDescription,
  postEventStateLabel
} from "./postevent-entry-display.ts";
import type { PostEventEntrySignal } from "./types.ts";

function signal(overrides: Partial<PostEventEntrySignal> = {}): PostEventEntrySignal {
  return {
    item_id: 1,
    external_key: "id1_test",
    item_name: "Test Vehicle",
    catalog_match_status: "MATCHED",
    matched_catalog_name: "Test Vehicle",
    state: "ENTRY_CANDIDATE",
    is_entry_candidate: true,
    event_key: "spring_2026",
    event_closed_at: "2026-05-06T11:00:00Z",
    days_since_close: 1,
    pre_event_baseline: "100",
    latest_price: "60",
    drawdown_ratio: "0.60",
    drawdown_threshold: "0.75",
    take_profit_price: "88.24",
    take_profit_net_roi: "0.25",
    hold_until: "2026-11-02T00:00:00Z",
    research_only: true,
    fill_claim: false,
    explanation_codes: [],
    version: "1.0.0",
    ...overrides
  };
}

test("state labels cover every rule state and sanitize an unknown one", () => {
  assert.equal(postEventStateLabel("ENTRY_CANDIDATE"), "入场候选");
  assert.equal(postEventStateLabel("SHALLOW_DRAWDOWN"), "回撤不足");
  assert.equal(postEventStateLabel("ENTRY_WINDOW_CLOSED"), "入场窗口已关闭");
  assert.equal(postEventStateLabel("SUPERSEDED_BY_NEXT_SUPPLY_EVENT"), "已被新一轮活动取代");
  assert.equal(postEventStateLabel("NO_COMPLETED_EVENT"), "尚无已结束的活动");
  assert.equal(postEventStateLabel("NO_PRE_EVENT_BASELINE"), "缺少活动前基准");
  assert.equal(postEventStateLabel("NO_POST_EVENT_PRICE"), "活动后尚无完整成交日");
  assert.equal(postEventStateLabel("<FUTURE_STATE>"), "FUTURE STATE");
  assert.equal(postEventStateLabel(null), "无法评估");
});

test("every state explains itself rather than leaving a blank row unexplained", () => {
  for (const state of [
    "ENTRY_CANDIDATE",
    "SHALLOW_DRAWDOWN",
    "ENTRY_WINDOW_CLOSED",
    "SUPERSEDED_BY_NEXT_SUPPLY_EVENT",
    "NO_COMPLETED_EVENT",
    "NO_PRE_EVENT_BASELINE",
    "NO_POST_EVENT_PRICE"
  ]) {
    assert.ok(postEventStateDescription(state).length > 10, state);
  }
  assert.match(postEventStateDescription(null), /没有绑定/);
});

test("explanation labels carry the caveats and sanitize unknown codes", () => {
  assert.equal(
    postEventExplanationLabel("postevent_entry_drawdown_gate_passed"),
    "回撤达到门槛"
  );
  assert.equal(
    postEventExplanationLabel(
      "postevent_entry_evidence_is_descriptive_not_walk_forward_validated"
    ),
    "证据为描述性统计，未经严格时点验证"
  );
  assert.equal(
    postEventExplanationLabel("postevent_entry_rule_research_only_not_execution_or_fill"),
    "仅供研究，不是成交或挂单保证"
  );
  assert.equal(postEventExplanationLabel("<future_code>"), "future code");
});

test("catalog match labels name all three outcomes", () => {
  assert.equal(catalogMatchLabel("MATCHED"), "已绑定活动目录");
  assert.equal(catalogMatchLabel("UNMATCHED"), "未绑定活动目录");
  assert.equal(catalogMatchLabel("AMBIGUOUS"), "目录匹配存在歧义");
});

test("tone separates candidates, open windows, closed windows and unbound items", () => {
  assert.equal(postEventEntryTone(signal()), "candidate");
  assert.equal(
    postEventEntryTone(signal({ is_entry_candidate: false, state: "SHALLOW_DRAWDOWN" })),
    "open"
  );
  assert.equal(
    postEventEntryTone(signal({ is_entry_candidate: false, state: "ENTRY_WINDOW_CLOSED" })),
    "closed"
  );
  assert.equal(
    postEventEntryTone(signal({ is_entry_candidate: false, state: null })),
    "unbound"
  );
});

test("ordering puts candidates first, then the freshest window, then the id", () => {
  const candidateLate = signal({ item_id: 9, days_since_close: 100 });
  const shallowFresh = signal({ item_id: 2, is_entry_candidate: false, days_since_close: 1 });
  const unbound = signal({ item_id: 3, is_entry_candidate: false, days_since_close: null });
  const shallowFreshHigherId = signal({
    item_id: 5,
    is_entry_candidate: false,
    days_since_close: 1
  });

  const sorted = [unbound, shallowFreshHigherId, shallowFresh, candidateLate]
    .sort(comparePostEventEntry)
    .map((value) => value.item_id);

  assert.deepEqual(sorted, [9, 2, 5, 3]);
});

test("days since close never invents a value", () => {
  assert.equal(daysSinceCloseLabel(null), "—");
  assert.equal(daysSinceCloseLabel(0), "当天");
  assert.equal(daysSinceCloseLabel(138), "138 天前");
});

test("the gauge is clamped to the baseline and refuses unusable ratios", () => {
  assert.equal(drawdownGaugePercent("0.60"), 60);
  assert.equal(drawdownGaugePercent("0.755"), 75.5);
  assert.equal(drawdownGaugePercent("1.40"), 100);
  assert.equal(drawdownGaugePercent(null), null);
  assert.equal(drawdownGaugePercent("not-a-number"), null);
  assert.equal(drawdownGaugePercent("-0.2"), null);
});

test("the take-profit price rounds UP to the 0.01 quantum so the target is not undershot", () => {
  // The rule's exact quotient carries far more digits than the market quantum.
  assert.equal(formatTakeProfitPrice("113.220588235294117647"), "113.23");
  assert.equal(formatTakeProfitPrice("88.235294117647058823"), "88.24");
  // Already exact at the quantum: no spurious carry.
  assert.equal(formatTakeProfitPrice("113.22"), "113.22");
  assert.equal(formatTakeProfitPrice("113.2200"), "113.22");
  assert.equal(formatTakeProfitPrice("113"), "113.00");
  // A carry that ripples through the whole part.
  assert.equal(formatTakeProfitPrice("9.999"), "10.00");
  assert.equal(formatTakeProfitPrice("0.001"), "0.01");
});

test("the take-profit formatter never invents a value it cannot parse", () => {
  assert.equal(formatTakeProfitPrice(null), "—");
  assert.equal(formatTakeProfitPrice("1e3"), "1e3");
  assert.equal(formatTakeProfitPrice("not-a-price"), "not-a-price");
});

test("the summary states zero candidates as an answer, not as missing data", () => {
  assert.equal(entryCandidateSummary(0, 0), "目前没有可评估的载具。");
  assert.match(entryCandidateSummary(0, 34), /没有一台/);
  assert.match(entryCandidateSummary(3, 34), /其中 3 台是入场候选/);
});

test("under the candidates-only filter the summary never claims the filtered count was the evaluated count", () => {
  // The API's item_count is post-filter, so "evaluated N" would be a lie here.
  const filtered = entryCandidateSummary(5, 5, true);
  assert.doesNotMatch(filtered, /本次评估/);
  assert.match(filtered, /当前只显示入场候选，共 5 台/);
  assert.match(filtered, /已被筛选隐藏/);

  const filteredEmpty = entryCandidateSummary(0, 0, true);
  assert.doesNotMatch(filteredEmpty, /没有可评估的载具/);
  assert.match(filteredEmpty, /去掉筛选/);
});
