import type { HistoricalTradeBucket } from "./types.ts";

const DAY_MILLISECONDS = 86_400_000;
const WIDTH = 900;
const HEIGHT = 240;
const INSET = 12;

export type HistoricalChartPoint = {
  day: string;
  price: string;
  raw: number;
  volume: number;
  time: number;
  x: number;
  y: number;
};

export function historicalDay(value: string): string {
  const time = Date.parse(value);
  return Number.isFinite(time) ? new Date(time).toISOString().slice(0, 10) : "—";
}

export function historicalRawPrice(raw: number): string {
  const integer = BigInt(raw);
  const scale = BigInt(10_000);
  const fraction = (integer % scale).toString().padStart(4, "0").replace(/0+$/, "");
  return `${integer / scale}.${fraction.padEnd(2, "0")}`;
}

export function historicalPriceChart(buckets: HistoricalTradeBucket[]) {
  const valid = buckets.filter((bucket) => {
    const time = Date.parse(bucket.bucket_start_utc);
    return bucket.granularity === "1d" && bucket.bucket_duration_seconds === 86_400 &&
      bucket.price_scale === 10_000 && Number.isSafeInteger(bucket.vwap_price_raw) &&
      bucket.vwap_price_raw > 0 && Number.isFinite(time) && time % DAY_MILLISECONDS === 0;
  }).sort((a, b) => Date.parse(a.bucket_start_utc) - Date.parse(b.bucket_start_utc));
  if (valid.length === 0) return null;
  const first = Date.parse(valid[0].bucket_start_utc);
  const last = Date.parse(valid[valid.length - 1].bucket_start_utc);
  const rawPrices = valid.map((bucket) => bucket.vwap_price_raw);
  const minimum = Math.min(...rawPrices);
  const maximum = Math.max(...rawPrices);
  // Number conversion is confined to SVG coordinates. Labels retain exact raw-scale prices.
  const points: HistoricalChartPoint[] = valid.map((bucket) => {
    const time = Date.parse(bucket.bucket_start_utc);
    return {
      day: historicalDay(bucket.bucket_start_utc),
      price: historicalRawPrice(bucket.vwap_price_raw),
      raw: bucket.vwap_price_raw,
      volume: bucket.reported_trade_volume,
      time,
      x: first === last ? WIDTH / 2 : INSET + (time - first) / (last - first) * (WIDTH - INSET * 2),
      y: minimum === maximum ? HEIGHT / 2 : INSET + (maximum - bucket.vwap_price_raw) / (maximum - minimum) * (HEIGHT - INSET * 2)
    };
  });
  const segments: HistoricalChartPoint[][] = [];
  for (const point of points) {
    const previous = segments.at(-1)?.at(-1);
    if (!previous || point.time - previous.time !== DAY_MILLISECONDS) segments.push([]);
    segments[segments.length - 1].push(point);
  }
  return {
    width: WIDTH,
    height: HEIGHT,
    points,
    segments,
    minimum: historicalRawPrice(minimum),
    maximum: historicalRawPrice(maximum)
  };
}
