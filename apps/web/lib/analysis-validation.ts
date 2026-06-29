import type { AnalysisHorizonParam, ItemAnalysisQuery } from "./types";

export const ANALYSIS_HORIZONS = ["7", "30", "90", "180"] as const;

export type ValidationResult<T> = { ok: true; value: T } | { ok: false; message: string };

export type AnalysisFormValues = {
  horizon: string;
  asOfLocal: string;
};

export function validateAnalysisHorizon(value: string): ValidationResult<AnalysisHorizonParam> {
  if ((ANALYSIS_HORIZONS as readonly string[]).includes(value)) {
    return { ok: true, value: value as AnalysisHorizonParam };
  }

  return { ok: false, message: "分析周期必须是 7、30、90 或 180 天。" };
}

export function validateAnalysisForm(values: AnalysisFormValues): ValidationResult<ItemAnalysisQuery> {
  const horizon = validateAnalysisHorizon(values.horizon);
  if (!horizon.ok) {
    return horizon;
  }

  const asOf = datetimeLocalToIso(values.asOfLocal);
  if (!asOf.ok) {
    return asOf;
  }

  return {
    ok: true,
    value: {
      horizon: horizon.value,
      ...(asOf.value ? { as_of: asOf.value } : {})
    }
  };
}

export function datetimeLocalToIso(value: string): ValidationResult<string | undefined> {
  if (!value) {
    return { ok: true, value: undefined };
  }

  const parsed = parseDateTimeLocal(value);
  if (!parsed) {
    return { ok: false, message: "分析基准时间无效，请选择有效的本地日期和时间。" };
  }

  return { ok: true, value: parsed.toISOString() };
}

export function isoToDateTimeLocal(value: string | null): string {
  if (!value) {
    return "";
  }

  const date = new Date(value);
  if (!isValidDate(date)) {
    return "";
  }

  return [
    pad(date.getFullYear(), 4),
    "-",
    pad(date.getMonth() + 1, 2),
    "-",
    pad(date.getDate(), 2),
    "T",
    pad(date.getHours(), 2),
    ":",
    pad(date.getMinutes(), 2)
  ].join("");
}

export function isValidIsoDateTime(value: string): boolean {
  const date = new Date(value);
  return isValidDate(date);
}

function parseDateTimeLocal(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) {
    return null;
  }

  const [, yearText, monthText, dayText, hourText, minuteText, secondText = "00"] = match;
  const year = decimalTextToInteger(yearText);
  const month = decimalTextToInteger(monthText);
  const day = decimalTextToInteger(dayText);
  const hour = decimalTextToInteger(hourText);
  const minute = decimalTextToInteger(minuteText);
  const second = decimalTextToInteger(secondText);

  if (
    year === null ||
    month === null ||
    day === null ||
    hour === null ||
    minute === null ||
    second === null
  ) {
    return null;
  }

  const date = new Date(year, month - 1, day, hour, minute, second, 0);
  if (!isValidDate(date)) {
    return null;
  }

  if (
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day ||
    date.getHours() !== hour ||
    date.getMinutes() !== minute ||
    date.getSeconds() !== second
  ) {
    return null;
  }

  return date;
}

function decimalTextToInteger(value: string): number | null {
  let result = 0;
  for (const character of value) {
    const digit = character.charCodeAt(0) - 48;
    if (digit < 0 || digit > 9) {
      return null;
    }
    result = result * 10 + digit;
  }
  return result;
}

function isValidDate(date: Date): boolean {
  return !Number.isNaN(date.getTime());
}

function pad(value: number, width: number): string {
  return String(value).padStart(width, "0");
}
