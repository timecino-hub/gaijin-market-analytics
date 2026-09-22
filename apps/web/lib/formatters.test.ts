import assert from "node:assert/strict";
import test from "node:test";
import { formatDateTime, formatDateTimeUtc } from "./formatters.ts";

const PROBE = "2026-05-06T11:00:00Z";

function inZone<T>(timeZone: string, run: () => T): T {
  const previous = process.env.TZ;
  process.env.TZ = timeZone;
  try {
    return run();
  } finally {
    process.env.TZ = previous;
  }
}

test("the UTC formatter is identical in every time zone", () => {
  // This is what makes it safe to render on the server: the browser's first
  // render reproduces it exactly, so LocalDateTime can swap in the reader's
  // zone afterwards without a hydration mismatch.
  const renderings = ["UTC", "Asia/Tokyo", "America/New_York", "Pacific/Kiritimati"].map(
    (zone) => inZone(zone, () => formatDateTimeUtc(PROBE))
  );

  assert.equal(new Set(renderings).size, 1);
  assert.match(renderings[0], /UTC/);
  assert.match(renderings[0], /2026/);
  assert.match(renderings[0], /11:00:00/);
});

test("the runtime formatter follows the zone it runs in", () => {
  // In a client component that zone is the reader's, which is the whole point.
  const tokyo = inZone("Asia/Tokyo", () => formatDateTime(PROBE));
  const newYork = inZone("America/New_York", () => formatDateTime(PROBE));

  assert.match(tokyo, /20:00:00/);
  assert.match(newYork, /07:00:00/);
  assert.notEqual(tokyo, newYork);
});

test("both formatters refuse to invent a timestamp", () => {
  for (const format of [formatDateTime, formatDateTimeUtc]) {
    assert.equal(format(null), "—");
    // An unparsable value is echoed, never turned into an epoch or "now".
    assert.equal(format("not-a-date"), "not-a-date");
  }
});

test("the two agree on the instant, differing only in how they render it", () => {
  const utc = formatDateTimeUtc(PROBE);
  const sameZone = inZone("UTC", () => formatDateTime(PROBE));

  assert.equal(utc, sameZone);
});
