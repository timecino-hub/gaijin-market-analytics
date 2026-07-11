import type { OpportunityRankingQuery } from "./types";

export const OPPORTUNITY_HORIZONS = ["7", "30", "90", "180"] as const;
export const OPPORTUNITY_PAGE_SIZES = ["10", "25", "50", "100"] as const;

export type OpportunityListState = {
  query: OpportunityRankingQuery;
  form: {
    horizon: string;
    asOf: string;
    minimumScore: string;
    search: string;
    category: string;
    rarity: string;
    eligibleOnly: boolean;
    includeInactive: boolean;
    pageSize: string;
  };
};

export function opportunityListStateFromParams(
  params: Record<string, string | string[] | undefined>
): OpportunityListState {
  const horizon = oneOf(readParam(params.horizon), OPPORTUNITY_HORIZONS, "30");
  const pageSize = oneOf(readParam(params.page_size), OPPORTUNITY_PAGE_SIZES, "25");
  const page = positiveInteger(readParam(params.page), "1");
  const asOf = safeText(readParam(params.as_of), 80);
  const minimumScore = scoreText(readParam(params.min_score));
  const search = safeText(readParam(params.search), 200);
  const category = safeText(readParam(params.category), 100);
  const rarity = safeText(readParam(params.rarity), 100);
  const eligibleOnly = booleanText(readParam(params.eligible_only), true);
  const includeInactive = booleanText(readParam(params.include_inactive), false);

  return {
    query: {
      horizon,
      page,
      page_size: pageSize,
      as_of: asOf || undefined,
      eligible_only: String(eligibleOnly),
      min_score: minimumScore,
      search: search || undefined,
      category: category || undefined,
      rarity: rarity || undefined,
      include_inactive: String(includeInactive)
    },
    form: {
      horizon,
      asOf,
      minimumScore,
      search,
      category,
      rarity,
      eligibleOnly,
      includeInactive,
      pageSize
    }
  };
}

export function opportunityPageQuery(
  query: OpportunityRankingQuery,
  page: number
): Record<string, string> {
  return cleanQuery({ ...query, page: String(page) });
}

export function cleanOpportunityQuery(query: OpportunityRankingQuery): Record<string, string> {
  return cleanQuery(query);
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function oneOf<T extends readonly string[]>(
  value: string | undefined,
  options: T,
  fallback: T[number]
): T[number] {
  return options.includes(value as T[number]) ? (value as T[number]) : fallback;
}

function positiveInteger(value: string | undefined, fallback: string): string {
  return value && /^[1-9]\d*$/.test(value) ? value : fallback;
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

function scoreText(value: string | undefined): string {
  if (!value || !/^(?:\d{1,2}|100)(?:\.\d{1,2})?$/.test(value)) {
    return "0";
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 100 ? value : "0";
}

function safeText(value: string | undefined, maximum: number): string {
  const normalized = value?.trim() ?? "";
  return normalized.length <= maximum ? normalized : "";
}

function cleanQuery(query: OpportunityRankingQuery): Record<string, string> {
  return Object.fromEntries(
    Object.entries(query).filter((entry): entry is [string, string] => Boolean(entry[1]))
  );
}
