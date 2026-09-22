import type { StrategyEvidenceQuery } from "./types";

/** What the page falls back to when nothing is asked for. */
export const DEFAULT_BUDGET = "100";

/**
 * Read a budget out of the query string.
 *
 * Anything that is not a positive amount falls back to the default rather than
 * reaching the API: an unusable budget is a typo, not a question worth a 400.
 * The value is normalised so `100`, `100.00` and ` 100 ` produce one URL.
 */
export function budgetFromParams(
  params: Record<string, string | string[] | undefined>
): { budget: string; query: StrategyEvidenceQuery } {
  const raw = params.budget;
  const value = (Array.isArray(raw) ? raw[0] : raw)?.trim() ?? "";
  const parsed = Number(value);
  const budget =
    value !== "" && Number.isFinite(parsed) && parsed > 0
      ? String(parsed)
      : DEFAULT_BUDGET;
  return { budget, query: { budget } };
}
