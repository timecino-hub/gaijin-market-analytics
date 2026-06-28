export type SortField = "name" | "created_at" | "updated_at";
export type SortOrder = "asc" | "desc";

export type SnapshotSummary = {
  observed_at: string;
  best_ask: string;
  best_bid: string | null;
  ask_count: number | null;
  bid_count: number | null;
  estimated_volume: string | null;
};

export type MarketSnapshot = SnapshotSummary & {
  id: number;
  item_id: number;
  source_import_job_id: number | null;
  created_at: string;
};

export type ItemSummary = {
  id: number;
  external_key: string;
  name: string;
  category: string;
  rarity: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  latest_snapshot: SnapshotSummary | null;
};

export type ItemDetail = ItemSummary & {
  snapshot_count: number;
  first_snapshot_at: string | null;
  last_snapshot_at: string | null;
};

export type PaginatedItemsResponse = {
  items: ItemSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type ApiError = {
  status: number;
  code: string;
  message: string;
};

export type ItemListQuery = {
  page?: string;
  page_size?: string;
  search?: string;
  category?: string;
  rarity?: string;
  is_active?: string;
  sort?: SortField;
  order?: SortOrder;
};

export type SnapshotQuery = {
  from?: string;
  to?: string;
  limit?: string;
  order?: SortOrder;
};
