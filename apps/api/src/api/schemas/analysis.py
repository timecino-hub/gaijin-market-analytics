from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer


class AnalysisEffectiveInputs(BaseModel):
    horizon: Literal[7, 30, 90, 180]
    as_of: datetime
    fee_rate: Decimal
    maximum_snapshot_age_hours: int
    minimum_snapshot_count: int

    @field_serializer("fee_rate")
    def serialize_decimal(self, value: Decimal) -> str:
        return str(value)


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
    gross_profit: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    break_even_sell_price: Decimal | None
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
        "gross_profit",
        "net_profit",
        "net_roi",
        "break_even_sell_price",
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
