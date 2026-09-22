export function formatDecimal(value: string | null): string {
  if (value === null) {
    return "—";
  }

  const [whole, fraction = ""] = value.split(".");
  const trimmedFraction = fraction.padEnd(2, "0").slice(0, 2);
  return `${whole}.${trimmedFraction}`;
}

export function formatOptionalText(value: string | null): string {
  return value && value.trim() ? value : "—";
}

export function formatBoolean(value: boolean): string {
  return value ? "启用" : "停用";
}

/**
 * A timestamp in whichever zone the runtime is in.
 *
 * In a client component that is the reader's own zone, which is what we want.
 * On the server it is the container's zone, which is nobody's — server-rendered
 * pages go through `LocalDateTime` instead of calling this directly.
 */
export function formatDateTime(value: string | null): string {
  return formatDateTimeInZone(value);
}

/**
 * The same timestamp pinned to UTC.
 *
 * Deterministic wherever it runs, which makes it the one safe thing to render
 * on the server: the browser's first render reproduces it exactly, so
 * `LocalDateTime` can swap in the reader's zone after mount without a
 * hydration mismatch.
 */
export function formatDateTimeUtc(value: string | null): string {
  return formatDateTimeInZone(value, "UTC");
}

function formatDateTimeInZone(value: string | null, timeZone?: string): string {
  if (value === null) {
    return "—";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
    ...(timeZone === undefined ? {} : { timeZone })
  }).format(date);
}
