import type { GeneralStrategyQuery } from "./types";

export const GENERAL_STRATEGY_FIELDS = [
  "as_of",
  "baseline",
  "observation_price",
  "quoted_cost",
  "event_closes_on",
  "entry_cost",
  "bought_at",
  "fee_rate"
] as const;

/** Keep decimal and timezone inputs intact; validation and calculations belong to the API. */
export function generalStrategyFromParams(
  params: Record<string, string | string[] | undefined>
): GeneralStrategyQuery {
  const query: GeneralStrategyQuery = {};
  for (const name of GENERAL_STRATEGY_FIELDS) {
    const raw = params[name];
    const value = (Array.isArray(raw) ? raw[0] : raw)?.trim();
    if (value) {
      query[name] = value;
    }
  }
  return query;
}

export function legacyAsOfFromParams(
  params: Record<string, string | string[] | undefined>
): string | undefined {
  const raw = params.as_of;
  return (Array.isArray(raw) ? raw[0] : raw)?.trim() || undefined;
}
