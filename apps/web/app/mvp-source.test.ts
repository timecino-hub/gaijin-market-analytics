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

test("vehicle potential ranks on the price position only, and says why", async () => {
  const [page, header, caddy] = await Promise.all([
    readFile(new URL("./opportunities/page.tsx", import.meta.url), "utf8"),
    readFile(headerUrl, "utf8"),
    readFile(new URL("../../../deploy/Caddyfile", import.meta.url), "utf8")
  ]);
  assert.match(page, /export const dynamic = "force-dynamic"/);
  assert.match(page, /<MarketHeader active="opportunities"/);
  assert.match(page, /getVehiclePotential/);
  // The ranking basis, and the reason the other half is not it.
  assert.match(page, /排序只用价格分位/);
  assert.match(page, /按它排名等于按抽样噪声排名/);
  assert.match(page, /每一个汇总数字都带着它的样本量/);
  // The trap a percentile invites.
  assert.match(page, /低分位不等于会涨/);
  assert.match(page, /也可能是因为它还在跌/);
  assert.match(page, /位置描述，不是预测/);
  // Unreadable vehicles must not lead the list by scoring zero.
  assert.match(page, /不会因为算不出分数而排到前面/);
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

test("the strategy page states the rule and keeps what qualifies its numbers", async () => {
  const [page, header, caddy] = await Promise.all([
    readFile(new URL("./strategy/page.tsx", import.meta.url), "utf8"),
    readFile(headerUrl, "utf8"),
    readFile(new URL("../../../deploy/Caddyfile", import.meta.url), "utf8")
  ]);
  assert.match(page, /export const dynamic = "force-dynamic"/);
  assert.match(page, /<MarketHeader active="strategy"/);
  assert.match(page, /getStrategyEvidence/);
  // All four steps, and the two that come from an offline replay say so.
  for (const step of ["筛选", "入场", "卖出", "仓位"]) {
    assert.match(page, new RegExp(`title="${step}"`));
  }
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
  assert.match(source, /当前不展示伪造曲线或推算数据/);
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
