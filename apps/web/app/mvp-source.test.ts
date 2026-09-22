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

test("opportunity analysis is public, read-only, and honest about its evidence gate", async () => {
  const [page, header, caddy] = await Promise.all([
    readFile(new URL("./opportunities/page.tsx", import.meta.url), "utf8"),
    readFile(headerUrl, "utf8"),
    readFile(new URL("../../../deploy/Caddyfile", import.meta.url), "utf8")
  ]);
  assert.match(page, /<MarketHeader active="opportunities"/);
  assert.match(page, /至少需要 3 个真实市场快照/);
  assert.match(page, /最新观测不能早于 7 天前/);
  assert.match(page, /formatFreshnessLimit/);
  assert.match(page, /getItems/);
  assert.match(page, /一周内当前盘口/);
  assert.match(page, /它们不替代历史趋势或收益排名/);
  assert.match(page, /历史不足，不发布评分/);
  assert.match(page, /不会复制当前报价、补零或生成示例分数/);
  assert.match(header, /潜力分析/);
  assert.match(caddy, /path \/api\/v1\/opportunities/);
  assert.doesNotMatch(caddy, /@blocked_ui path[^\n]*opportunities/);
});

test("post-event entry is published as an annotation, with its weak evidence stated", async () => {
  const [page, header, caddy] = await Promise.all([
    readFile(new URL("./postevent-entry/page.tsx", import.meta.url), "utf8"),
    readFile(headerUrl, "utf8"),
    readFile(new URL("../../../deploy/Caddyfile", import.meta.url), "utf8")
  ]);
  assert.match(page, /export const dynamic = "force-dynamic"/);
  assert.match(page, /<MarketHeader active="postevent-entry"/);
  // The page must publish the honest gap between the descriptive number and the
  // walk-forward one, not just the flattering one.
  assert.match(page, /\+25\.2%/);
  assert.match(page, /67\.0% 提升到 69\.6%/);
  assert.match(page, /\+2\.6 个百分点/);
  assert.match(page, /描述性统计/);
  // Zero candidates is an answer, not an outage.
  assert.match(page, /这是规则给出的答案，不是数据缺失/);
  assert.match(page, /不代表成交保证或交易建议/);
  assert.match(page, /只标注，不隐藏任何载具/);
  // Unmatched vehicles are shown, never silently dropped.
  assert.match(page, /不会被悄悄省略/);
  assert.doesNotMatch(page, /Math\.random/);
  assert.match(header, /href="\/postevent-entry"/);
  assert.match(caddy, /path \/api\/v1\/postevent-entry/);
  assert.doesNotMatch(caddy, /@blocked_ui path[^\n]*postevent/);
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
