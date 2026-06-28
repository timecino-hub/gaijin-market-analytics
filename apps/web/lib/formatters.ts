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

export function formatDateTime(value: string | null): string {
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
    timeZoneName: "short"
  }).format(date);
}
