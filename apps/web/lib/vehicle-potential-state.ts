import type { VehiclePotentialQuery } from "./types";

export type VehiclePotentialListState = {
  query: VehiclePotentialQuery;
  form: {
    asOf: string;
    rankedOnly: boolean;
    includeInactive: boolean;
  };
};

export function vehiclePotentialStateFromParams(
  params: Record<string, string | string[] | undefined>
): VehiclePotentialListState {
  const asOf = safeText(readParam(params.as_of), 80);
  const rankedOnly = booleanText(readParam(params.ranked_only), false);
  const includeInactive = booleanText(readParam(params.include_inactive), false);

  return {
    query: {
      as_of: asOf || undefined,
      ranked_only: String(rankedOnly),
      include_inactive: String(includeInactive)
    },
    form: { asOf, rankedOnly, includeInactive }
  };
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function booleanText(value: string | undefined, fallback: boolean): boolean {
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  return fallback;
}

function safeText(value: string | undefined, maximum: number): string {
  const normalized = value?.trim() ?? "";
  return normalized.length <= maximum ? normalized : "";
}
