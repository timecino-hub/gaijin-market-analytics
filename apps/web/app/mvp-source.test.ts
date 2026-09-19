import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const homeUrl = new URL("./page.tsx", import.meta.url);
const headerUrl = new URL("./market-header.tsx", import.meta.url);
const listUrl = new URL("./items/page.tsx", import.meta.url);
const detailUrl = new URL("./items/[itemId]/page.tsx", import.meta.url);
const cssUrl = new URL("./art-direction.css", import.meta.url);

test("public homepage is a read-only, evidence-backed catalog", async () => {
  const [source, header] = await Promise.all([readFile(homeUrl, "utf8"), readFile(headerUrl, "utf8")]);
  assert.match(source, /export const dynamic = "force-dynamic"/);
  assert.match(source, /<form[^>]+action="\/items"/);
  assert.match(header, /只读预览/);
  assert.match(source, /当前不生成示例评分、收益预测或历史图表/);
  assert.match(source, /市场数据暂不可用/);
  assert.doesNotMatch(source, /Math\.random/);
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
  const source = await readFile(cssUrl, "utf8");
  for (const color of ["#eee8dc", "#f6f2ea", "#182327", "#5c6665", "#b9b6ac", "#b96741"]) {
    assert.match(source, new RegExp(color));
  }
  assert.match(source, /@media \(max-width: 1024px\)/);
  assert.match(source, /@media \(max-width: 620px\)/);
  assert.match(source, /\.deco-orderbook-grid/);
  assert.doesNotMatch(source, /radial-gradient|linear-gradient/);
});
