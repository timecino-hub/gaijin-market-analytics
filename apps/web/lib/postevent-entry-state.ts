import type { PostEventEntryQuery } from "./types";

export type PostEventEntryListState = {
  query: PostEventEntryQuery;
  form: {
    asOf: string;
    candidatesOnly: boolean;
    includeInactive: boolean;
  };
};

export function postEventEntryStateFromParams(
  params: Record<string, string | string[] | undefined>
): PostEventEntryListState {
  const asOf = safeText(readParam(params.as_of), 80);
  const candidatesOnly = booleanText(readParam(params.candidates_only), false);
  const includeInactive = booleanText(readParam(params.include_inactive), false);

  return {
    query: {
      as_of: asOf || undefined,
      candidates_only: String(candidatesOnly),
      include_inactive: String(includeInactive)
    },
    form: { asOf, candidatesOnly, includeInactive }
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
