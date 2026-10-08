import assert from "node:assert/strict";
import test from "node:test";
import { historicalDay, historicalPriceChart, historicalRawPrice } from "./historical-price-chart.ts";
import type { HistoricalTradeBucket } from "./types.ts";

function bucket(day: string, raw = 100_000): HistoricalTradeBucket {
  return {
    id: Date.parse(day), item_id: 7, granularity: "1d", bucket_start_utc: `${day}T00:00:00Z`,
    bucket_duration_seconds: 86_400, vwap_price_raw: raw, price_scale: 10000,
    reported_vwap_price: historicalRawPrice(raw), reported_trade_volume: 5,
    price_semantics: "bucket_volume_weighted_average_trade_price",
    volume_semantics: "reported_trade_volume_unknown_unit", source_schema_version: "SYNTHETIC_TEST_FIXTURE",
    first_seen_at: "2000-01-10T00:00:00Z", last_seen_at: "2000-01-10T00:00:00Z"
  };
}

test("historical chart uses UTC dates and leaves missing days as gaps", () => {
  const chart = historicalPriceChart([bucket("2000-01-04", 200_000), bucket("2000-01-01"), bucket("2000-01-03", 150_000)]);
  assert.ok(chart);
  assert.deepEqual(chart.points.map((point) => point.day), ["2000-01-01", "2000-01-03", "2000-01-04"]);
  assert.deepEqual(chart.segments.map((segment) => segment.length), [1, 2]);
  const [first, third, fourth] = chart.points;
  assert.equal(third.x - first.x, 2 * (fourth.x - third.x));
  assert.equal(chart.minimum, "10.00");
  assert.equal(chart.maximum, "20.00");
});

test("raw price labels retain exact 1/10000 precision and dates stay in UTC", () => {
  assert.equal(historicalRawPrice(80001), "8.0001");
  assert.equal(historicalRawPrice(80010), "8.001");
  assert.equal(historicalRawPrice(80000), "8.00");
  assert.equal(historicalDay("2000-01-02T01:00:00+09:00"), "2000-01-01");
  assert.equal(historicalDay("invalid"), "—");
});

test("flat and single-day series remain visible without invalid coordinates", () => {
  for (const input of [[bucket("2000-01-01")], [bucket("2000-01-01"), bucket("2000-01-02")]]) {
    const chart = historicalPriceChart(input);
    assert.ok(chart);
    assert.ok(chart.points.every((point) => Number.isFinite(point.x) && point.y === chart.height / 2));
    assert.equal(chart.minimum, chart.maximum);
  }
});

test("hourly, misaligned, zero, unsafe and wrong-scale records never produce a daily chart", () => {
  const daily = bucket("2000-01-01");
  assert.equal(historicalPriceChart([]), null);
  assert.equal(historicalPriceChart([
    { ...daily, granularity: "1h", bucket_duration_seconds: 3600 },
    { ...daily, bucket_start_utc: "2000-01-01T01:00:00Z" },
    { ...daily, bucket_start_utc: "invalid" },
    { ...daily, vwap_price_raw: 0 },
    { ...daily, vwap_price_raw: Number.MAX_SAFE_INTEGER + 1 },
    { ...daily, price_scale: 100 as 10000 }
  ]), null);
});
