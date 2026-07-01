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

export type ReviewStatus =
  | "processing"
  | "pending_review"
  | "confirmed"
  | "confirmed_with_edits"
  | "rejected"
  | "unreadable"
  | "failed"
  | "expired";

export type ObservedAtSource = "review_created_default" | "user_edited";
export type IdentityFieldSource = "ocr_initial" | "user_draft" | "confirm_request" | "canonical_item";

export type LocalRecognitionCapabilities = {
  ocr_backend: string;
  ocr_backend_version: string;
  installed_ocr_languages: string[];
  current_layout_profile: string;
  layout_version: string;
  config_sha256: string;
  max_image_bytes: number;
  max_image_pixels: number;
  supported_image_formats: string[];
  store_capacity: number;
  store_ttl_seconds: number;
  database_written: false;
  handles_history_images: false;
  browser_extension_connected: false;
  automatic_recognition_available: false;
  local_extension_bridge_available: boolean;
  pairing_code_ttl_seconds: number;
  pairing_code_entropy_bits: number;
  pairing_code_max_failed_attempts: number;
  pair_attempts_per_client_per_minute: number;
  global_pair_attempts_per_minute: number;
  extension_uploads_per_minute: number;
  extension_upload_burst: number;
  extension_dedup_window_seconds: number;
};

export type LocalRecognitionReviewCreate = {
  review_id: string;
  status: ReviewStatus;
  created_at: string;
  expires_at: string;
};

export type LocalRecognitionImage = {
  original_filename: string;
  width: number;
  height: number;
  format: "png" | "jpeg";
};

export type LocalRecognitionMetadata = {
  ocr_backend: string;
  ocr_backend_version: string;
  layout_name: string;
  layout_version: string;
  config_sha256: string;
  parser_version: string;
  runner_version: string;
  processing_duration_ms: number | null;
};

export type LocalRecognitionOcrCandidate = {
  item_name_raw: string | null;
  item_name_normalized: string | null;
  best_bid: string | null;
  best_ask: string | null;
  total_bid_quantity: number | null;
  total_ask_quantity: number | null;
};

export type LocalRecognitionDraft = {
  selected_item_id: number | null;
  item_key: string | null;
  final_item_name: string | null;
  identity_sources: {
    selected_item_id: IdentityFieldSource | null;
    item_key: IdentityFieldSource | null;
    final_item_name: IdentityFieldSource | null;
  };
  final_best_bid: string | null;
  final_best_ask: string | null;
  final_total_bid_quantity: number | null;
  final_total_ask_quantity: number | null;
  observed_at: string | null;
  observed_at_source: ObservedAtSource;
  reviewer_note: string | null;
};

export type LocalRecognitionCandidate = {
  candidate_version: "screen_review_candidate_v1";
  review_id: string;
  observed_at: string;
  observed_at_source: ObservedAtSource;
  item_identity: {
    item_id: number | null;
    item_key: string;
    item_name: string;
  };
  best_bid: string;
  best_ask: string;
  total_bid_quantity: number | null;
  total_ask_quantity: number | null;
  recognition: {
    layout_name: string;
    layout_version: string;
    config_sha256: string;
    ocr_backend: string;
    edited_fields: string[];
    parser_version: string;
    runner_version: string;
  };
  status: "confirmed" | "confirmed_with_edits";
  imported: false;
  database_written: false;
  quantity_semantics: "screenshot_display_quantity";
  csv_quantity_mapping: "not_mapped_to_ask_count_or_bid_count";
  market_snapshot_created: false;
};

export type LocalRecognitionSourceMetadata = {
  source: "manual_upload" | "browser_extension";
  extension_version: string | null;
  source_url_safe: string | null;
  source_tab_title: string | null;
  capture_sha256: string | null;
  pairing_id: string | null;
};

export type LocalRecognitionReview = {
  review_id: string;
  created_at: string;
  expires_at: string;
  status: ReviewStatus;
  suggested_observed_at: string;
  image: LocalRecognitionImage | null;
  recognition: LocalRecognitionMetadata;
  ocr_candidate: LocalRecognitionOcrCandidate;
  draft: LocalRecognitionDraft;
  ocr_evidence_summary: {
    fields: Record<string, unknown>;
    confidence_source: string;
    confidence_available: boolean;
  };
  source_metadata: LocalRecognitionSourceMetadata;
  warnings: string[];
  errors: string[];
  candidate: LocalRecognitionCandidate | null;
  confirmed_at: string | null;
  rejected_at: string | null;
};

export type LocalExtensionPairingCode = {
  pairing_code_id: string;
  pairing_code: string;
  expires_at: string;
  ttl_seconds: number;
};

export type LocalExtensionPairingSummary = {
  pairing_id: string;
  created_at: string;
  last_seen_at: string;
  revoked_at: string | null;
  extension_version: string | null;
  client_name: string | null;
};

export type LocalExtensionStatus = {
  bridge_available: boolean;
  restart_notice: string;
  pairings: LocalExtensionPairingSummary[];
  pairing_code_ttl_seconds: number;
  pairing_code_max_failed_attempts: number;
  pair_attempts_per_client_per_minute: number;
  global_pair_attempts_per_minute: number;
  extension_uploads_per_minute: number;
  extension_upload_burst: number;
  extension_dedup_window_seconds: number;
};

export type LocalRecognitionReviewList = {
  reviews: LocalRecognitionReview[];
  total: number;
  store_count: number;
  store_capacity: number;
};

export type LocalRecognitionDraftPayload = {
  selected_item_id?: number | null;
  item_key?: string | null;
  final_item_name?: string | null;
  final_best_bid?: string | null;
  final_best_ask?: string | null;
  final_total_bid_quantity?: number | null;
  final_total_ask_quantity?: number | null;
  observed_at?: string | null;
  reviewer_note?: string | null;
};
