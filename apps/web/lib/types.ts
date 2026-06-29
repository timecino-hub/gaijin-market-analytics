export type SortField = "name" | "created_at" | "updated_at";
export type SortOrder = "asc" | "desc";
export type AnalysisHorizon = 7 | 30 | 90 | 180;
export type AnalysisHorizonParam = "7" | "30" | "90" | "180";

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

export type ImportJobStatus = "pending" | "processing" | "completed" | "failed" | "duplicate";

export type ImportErrorEntry = {
  row_number: number | null;
  field: string | null;
  error_code: string;
  message: string;
};

export type ImportWarningEntry = ImportErrorEntry;

export type ImportErrorReport = {
  errors: ImportErrorEntry[];
  warnings: ImportWarningEntry[];
  duplicate_of_job_id: number | null;
};

export type ImportJobResponse = {
  job_id: number;
  status: ImportJobStatus;
  filename: string;
  checksum: string;
  row_count: number;
  valid_row_count: number;
  invalid_row_count: number;
  duplicate_of_job_id: number | null;
  source_type?: string;
  started_at?: string;
  finished_at?: string | null;
  error_report?: ImportErrorReport;
};

export type CsvUploadResult = ImportJobResponse;

export type ApiError = {
  status: number;
  code: string;
  message: string;
};

export type AnalysisStatus =
  | "ok"
  | "insufficient_data"
  | "invalid_input"
  | "no_recent_market"
  | "no_valid_price";

export type AnalysisReasonCode =
  | "insufficient_snapshots"
  | "insufficient_time_coverage"
  | "no_current_ask"
  | "no_current_bid"
  | "invalid_price"
  | "price_above_market_cap"
  | "invalid_fee_rate"
  | "break_even_unreachable_under_market_cap"
  | "stale_latest_snapshot"
  | "low_liquidity"
  | "large_spread"
  | "analysis_completed";

export type AnalysisEffectiveInputs = {
  horizon: AnalysisHorizon;
  as_of: string;
  maximum_snapshot_age_seconds: number;
  minimum_snapshot_count: number;
  fee_policy: AnalysisFeePolicy;
  market_rules?: AnalysisMarketRules;
};

export type AnalysisFeePolicy = {
  name: string;
  version: string;
  nominal_fee_rate: string;
  currency_quantum: string;
  proceeds_rounding: string;
};

export type AnalysisMarketRules = {
  name: string;
  version: string;
  maximum_listing_price: string;
  maximum_sale_proceeds: string;
  currency_quantum: string;
};

export type ItemAnalysisResponse = {
  item_id: number;
  external_key: string;
  item_name: string;
  effective_inputs: AnalysisEffectiveInputs;
  status: AnalysisStatus | string;
  strategy_name: string;
  strategy_version: string;
  feature_version: string;
  observation_count: number;
  first_observation_at: string | null;
  last_observation_at: string | null;
  current_ask: string | null;
  current_bid: string | null;
  reference_sell_price: string | null;
  sale_proceeds: string | null;
  fee_amount: string | null;
  gross_profit: string | null;
  net_profit: string | null;
  net_roi: string | null;
  break_even_sell_price: string | null;
  break_even_reachable?: boolean | null;
  maximum_net_profit?: string | null;
  spread_absolute: string | null;
  spread_ratio: string | null;
  median_bid: string | null;
  median_ask: string | null;
  price_volatility: string | null;
  liquidity_score: string | null;
  risk_score: string | null;
  confidence_score: string | null;
  reason_codes: string[];
};

export type ItemAnalysisQuery = {
  horizon: AnalysisHorizonParam;
  as_of?: string;
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
