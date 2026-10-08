import type { MarketDataStatus, VehiclePotentialQuery } from "./types";

export function potentialReferenceQuery(
  query: VehiclePotentialQuery, status: MarketDataStatus | null
): VehiclePotentialQuery {
  if (query.as_of || !status?.reference_as_of) return query;
  return { ...query, as_of: status.reference_as_of };
}
