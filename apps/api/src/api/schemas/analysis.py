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
    score: Decimal
    raw_score: Decimal
    profitability_score: Decimal
    liquidity_score: Decimal
    stability_score: Decimal
    data_confidence_score: Decimal
    freshness_score: Decimal
    risk_penalty: Decimal
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
    net_profit: Decimal | None
    net_roi: Decimal | None
    explanation_codes: list[str]
    analysis_reason_codes: list[str]

    @field_serializer(
        "score",
        "raw_score",
        "profitability_score",
        "liquidity_score",
        "stability_score",
        "data_confidence_score",
        "freshness_score",
        "risk_penalty",
        "current_ask",
        "current_bid",
        "reference_sell_price",
        "net_profit",
        "net_roi",
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
    score: Decimal
    raw_score: Decimal
    profitability_score: Decimal
    liquidity_score: Decimal
    stability_score: Decimal
    data_confidence_score: Decimal
    freshness_score: Decimal
    risk_penalty: Decimal
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
    net_profit: Decimal | None
    net_roi: Decimal | None
    explanation_codes: list[str]
    analysis_reason_codes: list[str]

    @field_serializer(
        "score",
        "raw_score",
        "profitability_score",
        "liquidity_score",
        "stability_score",
        "data_confidence_score",
        "freshness_score",
        "risk_penalty",
        "current_ask",
        "current_bid",
        "reference_sell_price",
        "net_profit",
        "net_roi",
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
