import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const homeUrl = new URL("./page.tsx", import.meta.url);
const headerUrl = new URL("./market-header.tsx", import.meta.url);
const listUrl = new URL("./items/page.tsx", import.meta.url);
const detailUrl = new URL("./items/[itemId]/page.tsx", import.meta.url);
const cssUrl = new URL("./art-direction.css", import.meta.url);
const layoutUrl = new URL("./layout.tsx", import.meta.url);

test("public homepage is a read-only, evidence-backed catalog", async () => {
  const [source, header] = await Promise.all([readFile(homeUrl, "utf8"), readFile(headerUrl, "utf8")]);
  assert.match(source, /export const dynamic = "force-dynamic"/);
  assert.match(source, /<form[^>]+action="\/items"/);
  assert.match(header, /只读预览/);
  assert.match(source, /潜力分析已开放/);
  assert.match(source, /当前买价/);
  assert.match(source, /当前卖价/);
  assert.match(header, /href="\/opportunities"/);
  assert.doesNotMatch(source, /最佳买价|最佳卖价/);
  assert.match(source, /市场数据暂不可用/);
  assert.doesNotMatch(source, /Math\.random/);
});

test("vehicle potential compares observed prices without claiming future returns", async () => {
  const [page, header, caddy] = await Promise.all([
    readFile(new URL("./opportunities/page.tsx", import.meta.url), "utf8"),
    readFile(headerUrl, "utf8"),
    readFile(new URL("../../../deploy/Caddyfile", import.meta.url), "utf8")
  ]);
  assert.match(page, /export const dynamic = "force-dynamic"/);
  assert.match(page, /<MarketHeader active="opportunities"/);
  assert.match(page, /getVehiclePotential/);
  assert.match(page, /selectVehiclePotentialItems/);
  assert.match(page, /历史比较 · 非收益预测/);
  assert.match(page, /活动记录不参与排序/);
  assert.match(page, /成本参考：最新完整成交日均价/);
  assert.match(page, /未计买入价差/);
  assert.match(page, /净赚50%所需卖价/);
  assert.match(page, /净赚40%所需卖价/);
  assert.match(page, /近30天日成交量中位数/);
  assert.match(page, /<details className="deco-potential-details"/);
  assert.match(page, /活动后首个记录日均价/);
  assert.match(page, /record\.entry_price_day/);
  assert.doesNotMatch(page, /已结算|次日成交均价|日均成交量/);
  // Every event aggregate is shown with the count behind it.
  assert.match(page, /n=\$\{count\}/);
  assert.doesNotMatch(page, /Math\.random/);
  assert.match(header, /载具潜力/);
  assert.match(caddy, /path \/api\/v1\/vehicle-potential/);
  assert.doesNotMatch(caddy, /@blocked_ui path[^\n]*vehicle-potential/);
});

test("the two analyses stay separate and link to each other", async () => {
  const [potential, strategy] = await Promise.all([
    readFile(new URL("./opportunities/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("./strategy/page.tsx", import.meta.url), "utf8")
  ]);
  // Vehicle potential is one vehicle's own character; the strategy page is one
  // rule pooled across every vehicle. Neither should absorb the other.
  assert.match(potential, /href="\/strategy"/);
  assert.doesNotMatch(potential, /drawdown_threshold|take_profit/);
  assert.doesNotMatch(strategy, /price_percentile|getVehiclePotential/);
});

test("the post-event entry page and endpoint are gone, with nothing left pointing at them", async () => {
  // Both were removed. A stale nav entry would 404 a reader, and a gateway
  // rule left behind would keep a route public that no longer exists.
  const [header, potential, strategy, client, caddy, main] = await Promise.all([
    readFile(headerUrl, "utf8"),
    readFile(new URL("./opportunities/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("./strategy/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/api-client.ts", import.meta.url), "utf8"),
    readFile(new URL("../../../deploy/Caddyfile", import.meta.url), "utf8"),
    readFile(new URL("../../api/src/api/main.py", import.meta.url), "utf8")
  ]);
  for (const source of [header, potential, strategy, client, caddy, main]) {
    assert.doesNotMatch(source, /postevent[-_]entry/);
  }
  assert.doesNotMatch(header, /活动后入场/);
});

test("the strategy page publishes the current general strategy separately from legacy performance", async () => {
  const [page, panel, header, caddy] = await Promise.all([
    readFile(new URL("./strategy/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("./strategy/general-rule-panel.tsx", import.meta.url), "utf8"),
    readFile(headerUrl, "utf8"),
    readFile(new URL("../../../deploy/Caddyfile", import.meta.url), "utf8")
  ]);
  assert.match(page, /export const dynamic = "force-dynamic"/);
  assert.match(page, /<MarketHeader active="strategy"/);
  assert.match(page, /getStrategyEvidence/);
  assert.match(page, /getGeneralStrategy/);
  assert.match(page, /<Suspense[^>]*fallback=/);
  assert.match(page, /<LegacyEvidence/);
  assert.doesNotMatch(page, /\[generalResult, legacyResult\]\s*=\s*await/);
  assert.match(page, /<GeneralRulePanel/);
  assert.match(page, /<details\s+open=\{showLegacy\}>/);
  assert.match(page, /showLegacy \? <Suspense/);
  assert.match(page, /旧策略回测/);
  assert.match(page, /旧策略：活动后买入／扣费后赚25%分批卖出/);
  assert.doesNotMatch(page, /Claude|stage25/);
  assert.match(page, /GENERAL STRATEGY · CURRENT/);
  assert.match(page, /当前通用策略 · 人工执行/);
  assert.doesNotMatch(page, /CANDIDATE|当前候选|候选规则/);
  assert.match(panel, /当前通用策略暂不可用/);
  assert.match(panel, /id="general-rule-heading">当前通用策略/);
  assert.doesNotMatch(panel, /当前候选|候选参数/);
  assert.match(panel, /60%研究备选（非默认）/);
  // The current rule is displayed in its own panel, including the unforced review.
  for (const step of ["观察", "买入", "卖出", "复核"]) {
    assert.match(panel, new RegExp(`title="${step}"`));
  }
  assert.match(panel, /180天重新评估/);
  assert.match(panel, /成交状态：未确认/);
  assert.match(panel, /<li>默认手续费：15%<\/li>/);
  assert.match(panel, /活动前参考价（GJN）/);
  assert.match(panel, /当天结束后/);
  assert.match(panel, /买入总价不超过活动前参考价/);
  assert.match(panel, /先按扣费后赚/);
  assert.match(panel, /重新选择退出时机/);
  assert.match(panel, /历史数据截至：/);
  assert.doesNotMatch(panel, /basis=|deco-strategy-basis|<p className="deco-strategy-note">|VWAP|基准 B|成本 C/);
  assert.match(panel, /<form[^>]+action="\/strategy" method="get"/);
  assert.match(panel, /含时区/);
  assert.doesNotMatch(panel, /datetime-local|parseFloat|Math\.random/);
  assert.match(page, /2026-09-21/);
  // The monotone claim is recomputed from the data, never written as prose.
  assert.match(page, /medianRisesWithHolding/);
  assert.match(page, /未随持有期单调上升/);
  // Every aggregate carries its counts, and what was excluded is still counted.
  assert.match(page, /sampleLabel/);
  assert.match(page, /样本不独立/);
  assert.match(page, /pending_count/);
  assert.match(page, /superseded_count/);
  assert.match(page, /非可成交报价/);
  assert.doesNotMatch(page, /Math\.random/);
  assert.match(header, /通用策略/);
  assert.match(caddy, /path \/api\/v1\/strategy-evidence/);
});

test("server-rendered pages show timestamps in the reader's zone, not the container's", async () => {
  // A server component calling formatDateTime prints whatever zone the
  // container happens to be in, which is nobody's. Those pages must go through
  // LocalDateTime, which renders UTC on the server and re-formats after mount.
  const serverPages = [
    "./page.tsx",
    "./items/page.tsx",
    "./items/[itemId]/page.tsx",
    "./opportunities/page.tsx",
    "./strategy/page.tsx"
  ];

  for (const page of serverPages) {
    const source = await readFile(new URL(page, import.meta.url), "utf8");
    assert.doesNotMatch(source, /\bformatDateTime\b/, page);
    assert.match(source, /LocalDateTime/, page);
  }

  const component = await readFile(new URL("./local-date-time.tsx", import.meta.url), "utf8");
  assert.match(component, /^"use client";/);
  // The UTC rendering is the hydration-safe one; the swap happens once hydrated.
  assert.match(component, /formatDateTimeUtc/);
  assert.match(component, /useEffect/);
  // The exact instant stays machine-readable even before the swap.
  assert.match(component, /<time dateTime=\{value\}>/);
});

test("catalog prices only use the approved current order book", async () => {
  const source = await readFile(listUrl, "utf8");
  assert.match(source, /item\.current_order_book/);
  assert.match(source, /canonical_display_text/);
  assert.match(source, /缺失价格不会被填成零或示例值/);
  assert.doesNotMatch(source, /latest_snapshot/);
});

test("item detail renders canonical prices and evidence provenance", async () => {
  const source = await readFile(detailUrl, "utf8");
  assert.match(source, /export const dynamic = "force-dynamic"/);
  assert.match(source, /canonical_display_text/);
  assert.match(source, /side="BUY"/);
  assert.match(source, /side="SELL"/);
  assert.match(source, /未知 · 不作声明/);
  assert.match(source, /<HistorySection/);
  assert.match(source, /历史成交价格/);
  assert.doesNotMatch(source, /市场历史尚未开放/);
  assert.doesNotMatch(source, /price_raw\s*\/\s*(?:100|10000)/);
});

test("art direction uses the approved palette and responsive layouts", async () => {
  const [source, layout] = await Promise.all([readFile(cssUrl, "utf8"), readFile(layoutUrl, "utf8")]);
  for (const color of ["#eee8dc", "#f6f2ea", "#182327", "#5c6665", "#b9b6ac", "#b96741"]) {
    assert.match(source, new RegExp(color));
  }
  assert.match(source, /@media \(max-width: 1024px\)/);
  assert.match(source, /@media \(max-width: 620px\)/);
  assert.match(source, /\.deco-orderbook-grid/);
  assert.match(layout, /BarlowCondensed-Black\.ttf/);
  assert.match(layout, /next\/font\/local/);
  assert.match(source, /--font-art-display/);
  assert.match(source, /--font-art-interface/);
  assert.doesNotMatch(source, /radial-gradient|linear-gradient/);
});
