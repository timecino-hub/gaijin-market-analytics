from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer


class AnalysisFeePolicy(BaseModel):
    name: str
    version: str
    nominal_fee_rate: Decimal
    currency_quantum: Decimal
    proceeds_rounding: str

    @field_serializer("nominal_fee_rate", "currency_quantum")
    def serialize_decimal(self, value: Decimal) -> str:
        return str(value)


class AnalysisMarketRules(BaseModel):
    name: str
    version: str
    maximum_listing_price: Decimal
    maximum_sale_proceeds: Decimal
    currency_quantum: Decimal

    @field_serializer("maximum_listing_price", "maximum_sale_proceeds", "currency_quantum")
    def serialize_decimal(self, value: Decimal) -> str:
        return str(value)


class AnalysisEffectiveInputs(BaseModel):
    horizon: Literal[7, 30, 90, 180]
    as_of: datetime
    maximum_snapshot_age_seconds: int
    minimum_snapshot_count: int
    fee_policy: AnalysisFeePolicy
    market_rules: AnalysisMarketRules


class AnalysisResponse(BaseModel):
    item_id: int
    external_key: str
    item_name: str
    effective_inputs: AnalysisEffectiveInputs
    status: str
    strategy_name: str
    strategy_version: str
    feature_version: str
    observation_count: int
    first_observation_at: datetime | None
    last_observation_at: datetime | None
    current_ask: Decimal | None
    current_bid: Decimal | None
    reference_sell_price: Decimal | None
    sale_proceeds: Decimal | None
    fee_amount: Decimal | None
    gross_profit: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    break_even_sell_price: Decimal | None
    break_even_reachable: bool | None
    maximum_net_profit: Decimal | None
    spread_absolute: Decimal | None
    spread_ratio: Decimal | None
    median_bid: Decimal | None
    median_ask: Decimal | None
    price_volatility: Decimal | None
    liquidity_score: Decimal | None
    risk_score: Decimal | None
    confidence_score: Decimal | None
    reason_codes: list[str]

    @field_serializer(
        "current_ask",
        "current_bid",
        "reference_sell_price",
        "sale_proceeds",
        "fee_amount",
        "gross_profit",
        "net_profit",
        "net_roi",
        "break_even_sell_price",
        "maximum_net_profit",
        "spread_absolute",
        "spread_ratio",
        "median_bid",
        "median_ask",
        "price_volatility",
        "liquidity_score",
        "risk_score",
        "confidence_score",
    )
    def serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class OpportunityResponse(BaseModel):
    item_id: int
    external_key: str
    item_name: str
    effective_inputs: AnalysisEffectiveInputs
    analysis_status: str
    analysis_strategy_name: str
    analysis_strategy_version: str
    strategy_name: str
    strategy_version: str
    feature_version: str
    eligible: bool
    score_published: bool
    score: Decimal | None
    raw_score: Decimal | None
    profitability_score: Decimal | None
    liquidity_score: Decimal | None
    stability_score: Decimal | None
    activity_score: Decimal | None
    data_confidence_score: Decimal | None
    freshness_score: Decimal | None
    risk_penalty: Decimal | None
    liquidity_source: Literal[
        "reviewed_screenshot_quantity",
        "snapshot_counts",
        "unavailable",
    ]
    quantity_observation_count: int
    latest_observed_bid_quantity: int | None
    latest_observed_ask_quantity: int | None
    current_ask: Decimal | None
    current_bid: Decimal | None
    reference_sell_price: Decimal | None
    reference_sell_method: str | None
    reference_sell_version: str | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    historical_bucket_count: int
    historical_effective_day_count: int
    historical_hourly_bucket_count: int
    historical_daily_bucket_count: int
    historical_start_at: datetime | None
    historical_end_at: datetime | None
    historical_latest_age_seconds: int | None
    historical_observed_span_ratio: Decimal
    historical_price_median: Decimal | None = None
    context_weighted_historical_price_median: Decimal | None = None
    historical_mad_ratio: Decimal | None
    context_weighted_historical_mad_ratio: Decimal | None = None
    historical_max_drawdown: Decimal | None
    median_reported_trade_volume: Decimal | None
    manual_strength_index: Decimal = Decimal("1")
    manual_attention_index: Decimal = Decimal("1")
    manual_context_defaulted: bool = True
    historical_context_comparability: Decimal = Decimal("1")
    historical_context_effective_day_count: Decimal = Decimal("0")
    historical_context_effective_sample_size: Decimal = Decimal("0")
    historical_context_high_quality_day_count: Decimal = Decimal("0")
    historical_profitable_context_weight_share: Decimal | None = None
    supply_cycle_state: str
    supply_pool_family: str
    supply_coupon_mode: str
    supply_confirmed_current_membership: bool
    supply_observed_return_count: int
    supply_observed_tradeable_return_count: int
    supply_eligible_family_event_count: int
    supply_recurrence_rate: Decimal | None
    supply_current_event_key: str | None
    supply_current_event_opens_at: datetime | None
    supply_current_event_closes_at: datetime | None
    reissue_known_return_count: int
    reissue_completed_tradeable_supply_count: int
    reissue_completed_account_only_return_count: int
    reissue_distinct_family_count: int
    reissue_trailing_1095d_tradeable_supply_count: int
    reissue_days_since_last_tradeable_supply_close: Decimal | None
    explanation_codes: list[str]
    analysis_reason_codes: list[str]

    @field_serializer(
        "score",
        "raw_score",
        "profitability_score",
        "liquidity_score",
        "stability_score",
        "activity_score",
        "data_confidence_score",
        "freshness_score",
        "risk_penalty",
        "current_ask",
        "current_bid",
        "reference_sell_price",
        "net_profit",
        "net_roi",
        "historical_observed_span_ratio",
        "historical_price_median",
        "context_weighted_historical_price_median",
        "historical_mad_ratio",
        "context_weighted_historical_mad_ratio",
        "historical_max_drawdown",
        "median_reported_trade_volume",
        "manual_strength_index",
        "manual_attention_index",
        "historical_context_comparability",
        "historical_context_effective_day_count",
        "historical_context_effective_sample_size",
        "historical_context_high_quality_day_count",
        "historical_profitable_context_weight_share",
        "supply_recurrence_rate",
        "reissue_days_since_last_tradeable_supply_close",
    )
    def serialize_opportunity_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class OpportunityRankingFilters(BaseModel):
    eligible_only: bool
    minimum_score: Decimal
    search: str | None
    category: str | None
    rarity: str | None
    include_inactive: bool

    @field_serializer("minimum_score")
    def serialize_minimum_score(self, value: Decimal) -> str:
        return str(value)


class OpportunityRankingItem(BaseModel):
    rank: int
    item_id: int
    external_key: str
    item_name: str
    category: str
    rarity: str | None
    is_active: bool
    analysis_status: str
    observation_count: int
    first_observation_at: datetime | None
    last_observation_at: datetime | None
    eligible: bool
    score_published: bool
    score: Decimal | None
    raw_score: Decimal | None
    profitability_score: Decimal | None
    liquidity_score: Decimal | None
    stability_score: Decimal | None
    activity_score: Decimal | None
    data_confidence_score: Decimal | None
    freshness_score: Decimal | None
    risk_penalty: Decimal | None
    liquidity_source: Literal[
        "reviewed_screenshot_quantity",
        "snapshot_counts",
        "unavailable",
    ]
    quantity_observation_count: int
    latest_observed_bid_quantity: int | None
    latest_observed_ask_quantity: int | None
    current_ask: Decimal | None
    current_bid: Decimal | None
    reference_sell_price: Decimal | None
    reference_sell_method: str | None
    reference_sell_version: str | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    historical_bucket_count: int
    historical_effective_day_count: int
    historical_hourly_bucket_count: int
    historical_daily_bucket_count: int
    historical_start_at: datetime | None
    historical_end_at: datetime | None
    historical_latest_age_seconds: int | None
    historical_observed_span_ratio: Decimal
    historical_price_median: Decimal | None = None
    context_weighted_historical_price_median: Decimal | None = None
    historical_mad_ratio: Decimal | None
    context_weighted_historical_mad_ratio: Decimal | None = None
    historical_max_drawdown: Decimal | None
    median_reported_trade_volume: Decimal | None
    manual_strength_index: Decimal = Decimal("1")
    manual_attention_index: Decimal = Decimal("1")
    manual_context_defaulted: bool = True
    historical_context_comparability: Decimal = Decimal("1")
    historical_context_effective_day_count: Decimal = Decimal("0")
    historical_context_effective_sample_size: Decimal = Decimal("0")
    historical_context_high_quality_day_count: Decimal = Decimal("0")
    historical_profitable_context_weight_share: Decimal | None = None
    supply_cycle_state: str
    supply_pool_family: str
    supply_coupon_mode: str
    supply_confirmed_current_membership: bool
    supply_observed_return_count: int
    supply_observed_tradeable_return_count: int
    supply_eligible_family_event_count: int
    supply_recurrence_rate: Decimal | None
    supply_current_event_key: str | None
    supply_current_event_opens_at: datetime | None
    supply_current_event_closes_at: datetime | None
    reissue_known_return_count: int
    reissue_completed_tradeable_supply_count: int
    reissue_completed_account_only_return_count: int
    reissue_distinct_family_count: int
    reissue_trailing_1095d_tradeable_supply_count: int
    reissue_days_since_last_tradeable_supply_close: Decimal | None
    explanation_codes: list[str]
    analysis_reason_codes: list[str]

    @field_serializer(
        "score",
        "raw_score",
        "profitability_score",
        "liquidity_score",
        "stability_score",
        "activity_score",
        "data_confidence_score",
        "freshness_score",
        "risk_penalty",
        "current_ask",
        "current_bid",
        "reference_sell_price",
        "net_profit",
        "net_roi",
        "historical_observed_span_ratio",
        "historical_price_median",
        "context_weighted_historical_price_median",
        "historical_mad_ratio",
        "context_weighted_historical_mad_ratio",
        "historical_max_drawdown",
        "median_reported_trade_volume",
        "manual_strength_index",
        "manual_attention_index",
        "historical_context_comparability",
        "historical_context_effective_day_count",
        "historical_context_effective_sample_size",
        "historical_context_high_quality_day_count",
        "historical_profitable_context_weight_share",
        "supply_recurrence_rate",
        "reissue_days_since_last_tradeable_supply_close",
    )
    def serialize_ranking_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class OpportunityRankingResponse(BaseModel):
    items: list[OpportunityRankingItem]
    page: int
    page_size: int
    total: int
    total_pages: int
    evaluated_total: int
    eligible_total: int
    effective_inputs: AnalysisEffectiveInputs
    strategy_name: str
    strategy_version: str
    feature_version: str
    filters: OpportunityRankingFilters
