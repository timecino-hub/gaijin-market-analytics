import assert from "node:assert/strict";
import test from "node:test";
import { loadItemDetail } from "./item-detail-data.ts";

process.env.API_BASE_URL = "http://localhost:8000";
const syntheticItem = { id: 7, name: "SYNTHETIC_TEST_FIXTURE" };
const syntheticHistory = { item_id: 7, granularity: "1d", buckets: [], total: 0 };

test("detail starts all reads together and keeps history when no order book exists", async () => {
  const originalFetch = globalThis.fetch;
  const started: string[] = [];
  let resolveItem: ((response: Response) => void) | undefined;
  globalThis.fetch = async (url) => {
    const path = new URL(String(url)).pathname;
    started.push(path);
    if (path.endsWith("/order-book")) return Response.json({ detail: { code: "order_book_not_found" } }, { status: 404 });
    if (path.endsWith("/historical-trades")) return Response.json(syntheticHistory);
    return new Promise<Response>((resolve) => { resolveItem = resolve; });
  };
  try {
    const pending = loadItemDetail("7");
    assert.deepEqual(started, ["/api/v1/items/7", "/api/v1/items/7/order-book", "/api/v1/items/7/historical-trades"]);
    assert.ok(resolveItem);
    resolveItem(Response.json(syntheticItem));
    const result = await pending;
    assert.ok("item" in result);
    assert.equal(result.orderBook, null);
    assert.equal(result.orderBookError, null);
    assert.deepEqual(result.history, syntheticHistory);
    assert.equal(result.historyError, null);
  } finally { globalThis.fetch = originalFetch; }
});

test("an order-book failure does not hide independent history", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const path = new URL(String(url)).pathname;
    if (path.endsWith("/order-book")) throw new TypeError("SYNTHETIC offline book");
    return Response.json(path.endsWith("/historical-trades") ? syntheticHistory : syntheticItem);
  };
  try {
    const result = await loadItemDetail("7");
    assert.ok("item" in result);
    assert.equal(result.orderBookError?.code, "api_unreachable");
    assert.deepEqual(result.history, syntheticHistory);
    assert.equal(result.historyError, null);
  } finally { globalThis.fetch = originalFetch; }
});

test("a history failure is scoped to history while the catalog item survives", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const path = new URL(String(url)).pathname;
    if (path.endsWith("/historical-trades")) return Response.json({ detail: { code: "database_error", message: "SELECT secret" } }, { status: 500 });
    if (path.endsWith("/order-book")) return Response.json({ detail: { code: "order_book_not_found" } }, { status: 404 });
    return Response.json(syntheticItem);
  };
  try {
    const result = await loadItemDetail("7");
    assert.ok("item" in result);
    assert.equal(result.history, null);
    assert.equal(result.historyError?.status, 500);
    assert.doesNotMatch(result.historyError?.message ?? "", /SELECT|secret/);
    assert.equal(result.orderBookError, null);
  } finally { globalThis.fetch = originalFetch; }
});
