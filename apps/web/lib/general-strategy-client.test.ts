import assert from "node:assert/strict";
import test from "node:test";
import { ApiRequestError, getGeneralStrategy } from "./api-client.ts";

process.env.API_BASE_URL = "http://localhost:8000";

test("candidate planning is a no-store GET with exact decimal and timezone inputs", async () => {
  const originalFetch = globalThis.fetch;
  let captured: { url: string; init?: RequestInit } | undefined;
  globalThis.fetch = async (url, init) => {
    captured = { url: String(url), init };
    return Response.json({ version: "2026-10-01.1" });
  };
  try {
    await getGeneralStrategy({ baseline: "102.780000001", entry_cost: "47.1645792", bought_at: "2025-01-12T09:00:00+09:00", as_of: "2025-05-20T00:00:00Z" });
    assert.match(captured?.url ?? "", /\/api\/v1\/general-strategy\?/);
    assert.match(captured?.url ?? "", /baseline=102\.780000001/);
    assert.match(captured?.url ?? "", /bought_at=2025-01-12T09%3A00%3A00%2B09%3A00/);
    assert.equal(captured?.init?.cache, "no-store");
    assert.equal(captured?.init?.method ?? "GET", "GET");
    assert.equal(captured?.init?.body, undefined);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("candidate endpoint errors remain errors and never load legacy evidence instead", async () => {
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return Response.json({ detail: { code: "invalid_amount", message: "baseline must be positive." } }, { status: 400 });
  };
  try {
    await assert.rejects(getGeneralStrategy({ baseline: "-1" }), (error) => error instanceof ApiRequestError && error.error.code === "invalid_amount");
    assert.deepEqual(urls, ["http://localhost:8000/api/v1/general-strategy?baseline=-1"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
