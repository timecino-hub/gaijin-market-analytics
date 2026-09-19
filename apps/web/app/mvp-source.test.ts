import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const homeUrl = new URL("./page.tsx", import.meta.url);
const detailUrl = new URL("./items/[itemId]/page.tsx", import.meta.url);
const cssUrl = new URL("./globals.css", import.meta.url);

test("homepage exposes search, preview status, and honest empty states", async () => {
  const source = await readFile(homeUrl, "utf8");
  assert.match(source, /export const dynamic = "force-dynamic"/);
  assert.match(source, /<form[^>]+action="\/items"/);
  assert.match(source, /Read-only preview/);
  assert.match(source, /Historical market data is not available yet\./);
  assert.match(source, /API unavailable/);
  assert.doesNotMatch(source, /Math\.random/);
});

test("item detail renders approved canonical price text and order-book sides", async () => {
  const source = await readFile(detailUrl, "utf8");
  assert.match(source, /export const dynamic = "force-dynamic"/);
  assert.match(source, /canonical_display_text/);
  assert.match(source, /BUY orders/);
  assert.match(source, /SELL orders/);
  assert.match(source, /Unknown · not claimed/);
  assert.match(source, /Historical market data is not available yet\./);
  assert.doesNotMatch(source, /price_raw\s*\/\s*(?:100|10000)/);
});

test("MVP styles include responsive desktop and mobile layouts", async () => {
  const source = await readFile(cssUrl, "utf8");
  assert.match(source, /@media \(max-width: 900px\)/);
  assert.match(source, /@media \(max-width: 620px\)/);
  assert.match(source, /\.orderbook-grid/);
});
