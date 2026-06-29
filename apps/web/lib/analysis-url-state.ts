import {
  isValidIsoDateTime,
  isoToDateTimeLocal,
  validateAnalysisHorizon
} from "./analysis-validation.ts";
import type { AnalysisFormValues } from "./analysis-validation.ts";
import type { ItemAnalysisQuery } from "./types";

export type QueryInput = URLSearchParams | Record<string, string | string[] | undefined>;

export type InitialAnalysisState =
  | {
      canAutoRun: true;
      formValues: AnalysisFormValues;
      query: ItemAnalysisQuery;
      error: null;
    }
  | {
      canAutoRun: false;
      formValues: AnalysisFormValues;
      query: null;
      error: string | null;
    };

export function initialAnalysisStateFromQuery(input: QueryInput): InitialAnalysisState {
  const params = toURLSearchParams(input);
  const rawHorizon = params.get("horizon") ?? "30";
  const rawAsOf = params.get("as_of");
  const horizon = validateAnalysisHorizon(rawHorizon);
  const asOfError =
    rawAsOf && !isValidIsoDateTime(rawAsOf)
      ? "URL 中的 as_of 必须是带时区的 ISO-8601 时间。"
      : null;

  const formValues = {
    horizon: horizon.ok ? horizon.value : rawHorizon,
    asOfLocal: isoToDateTimeLocal(rawAsOf)
  };

  if (!params.has("horizon")) {
    return { canAutoRun: false, formValues, query: null, error: null };
  }

  if (!horizon.ok) {
    return { canAutoRun: false, formValues, query: null, error: horizon.message };
  }

  if (asOfError) {
    return { canAutoRun: false, formValues, query: null, error: asOfError };
  }

  return {
    canAutoRun: true,
    formValues,
    query: {
      horizon: horizon.value,
      ...(rawAsOf ? { as_of: rawAsOf } : {})
    },
    error: null
  };
}

export function mergeAnalysisQuery(input: QueryInput, query: ItemAnalysisQuery): URLSearchParams {
  const params = toURLSearchParams(input);
  params.set("horizon", query.horizon);
  params.delete("fee_rate");
  setOptionalParam(params, "as_of", query.as_of);
  return params;
}

export function mergeSnapshotQuery(
  input: QueryInput,
  updates: { from?: string; to?: string; limit?: string; order?: string }
): URLSearchParams {
  const params = toURLSearchParams(input);
  params.delete("fee_rate");
  setOptionalParam(params, "from", updates.from);
  setOptionalParam(params, "to", updates.to);
  setOptionalParam(params, "limit", updates.limit);
  setOptionalParam(params, "order", updates.order);
  return params;
}

export function removeSnapshotQuery(input: QueryInput): URLSearchParams {
  const params = toURLSearchParams(input);
  params.delete("fee_rate");
  for (const key of ["from", "to", "limit", "order"]) {
    params.delete(key);
  }
  return params;
}

export function itemDetailPath(itemId: string, params: URLSearchParams): string {
  const query = params.toString();
  const path = `/items/${encodeURIComponent(itemId)}`;
  return query ? `${path}?${query}` : path;
}

export function toURLSearchParams(input: QueryInput): URLSearchParams {
  if (isURLSearchParamsLike(input)) {
    const params = new URLSearchParams();
    input.forEach((value, key) => {
      params.append(key, value);
    });
    return params;
  }

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    const firstValue = Array.isArray(value) ? value[0] : value;
    if (firstValue !== undefined) {
      params.set(key, firstValue);
    }
  }
  return params;
}

function isURLSearchParamsLike(input: QueryInput): input is URLSearchParams {
  return typeof (input as URLSearchParams).forEach === "function";
}

function setOptionalParam(params: URLSearchParams, key: string, value: string | undefined): void {
  const trimmed = value?.trim() ?? "";
  if (trimmed) {
    params.set(key, trimmed);
  } else {
    params.delete(key);
  }
}
