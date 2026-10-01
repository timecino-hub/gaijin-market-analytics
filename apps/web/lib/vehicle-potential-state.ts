import type { VehiclePotentialQuery } from "./types";

export const POTENTIAL_SORTS = ["percentile", "median_room", "recent_volume", "freshness"] as const;
export type PotentialSort = typeof POTENTIAL_SORTS[number];
export type PotentialRoom = "all" | "50" | "40";

export type VehiclePotentialListState = {
  query: VehiclePotentialQuery;
  form: {
    asOf: string;
    rankedOnly: boolean;
    includeInactive: boolean;
    q: string;
    sort: PotentialSort;
    room: PotentialRoom;
  };
};

export function vehiclePotentialStateFromParams(
  params: Record<string, string | string[] | undefined>
): VehiclePotentialListState {
  const asOf = safeText(readParam(params.as_of), 80);
  const rankedOnly = booleanText(readParam(params.ranked_only), false);
  const includeInactive = booleanText(readParam(params.include_inactive), false);
  const q = safeText(readParam(params.q), 80);
  const rawSort = readParam(params.sort);
  const sort: PotentialSort = POTENTIAL_SORTS.includes(rawSort as PotentialSort)
    ? rawSort as PotentialSort : "percentile";
  const rawRoom = readParam(params.room);
  const room: PotentialRoom = rawRoom === "50" || rawRoom === "40" ? rawRoom : "all";

  return {
    // Comparison controls stay local and never become API query arguments.
    query: {
      as_of: asOf || undefined,
      ranked_only: String(rankedOnly),
      include_inactive: String(includeInactive)
    },
    form: { asOf, rankedOnly, includeInactive, q, sort, room }
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
