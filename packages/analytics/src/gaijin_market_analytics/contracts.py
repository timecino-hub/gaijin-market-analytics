from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from gaijin_market_analytics.enums import AnalysisHorizon, AnalysisStatus, ReasonCode
from gaijin_market_analytics.exceptions import ContractValidationError, InvalidDecimalError
from gaijin_market_analytics.fees import FeePolicy


@dataclass(frozen=True)
class MarketObservation:
    observed_at: datetime
    best_ask: Decimal | None
    best_bid: Decimal | None
    ask_count: int | None
    bid_count: int | None
    estimated_volume: Decimal | None
    observation_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _require_aware_utc(self.observed_at, "observed_at"))
        for field_name in ("best_ask", "best_bid", "estimated_volume"):
            _require_decimal_or_none(getattr(self, field_name), field_name)
        for field_name in ("ask_count", "bid_count"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ContractValidationError(f"{field_name} must be greater than or equal to 0.")


@dataclass(frozen=True)
class AnalysisRequest:
    item_id: int
    horizon: AnalysisHorizon
    as_of: datetime
    observations: tuple[MarketObservation, ...]
    fee_policy: FeePolicy
    maximum_snapshot_age: timedelta
    minimum_snapshot_count: int

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise ContractValidationError("item_id must be a positive integer.")
        if not isinstance(self.horizon, AnalysisHorizon):
            raise ContractValidationError("horizon must be an AnalysisHorizon value.")
        as_of = _require_aware_utc(self.as_of, "as_of")
        _require_fee_policy(self.fee_policy)
        if self.maximum_snapshot_age <= timedelta(0):
            raise ContractValidationError("maximum_snapshot_age must be greater than 0.")
        if self.minimum_snapshot_count <= 0:
            raise ContractValidationError("minimum_snapshot_count must be greater than 0.")

        normalized_observations = tuple(self.observations)
        for observation in normalized_observations:
            if not isinstance(observation, MarketObservation):
                raise ContractValidationError("observations must contain MarketObservation values.")
            if observation.observed_at > as_of:
                raise ContractValidationError("observations must not be later than as_of.")

        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(
            self,
            "observations",
            tuple(sorted(normalized_observations, key=observation_sort_key)),
        )


@dataclass(frozen=True)
class AnalysisResult:
    item_id: int
    horizon: AnalysisHorizon
    as_of: datetime
    status: AnalysisStatus
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
    spread_absolute: Decimal | None
    spread_ratio: Decimal | None
    median_bid: Decimal | None
    median_ask: Decimal | None
    price_volatility: Decimal | None
    liquidity_score: Decimal | None
    risk_score: Decimal | None
    confidence_score: Decimal | None
    fee_policy_name: str
    fee_policy_version: str
    nominal_fee_rate: Decimal
    currency_quantum: Decimal
    proceeds_rounding: str
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _require_aware_utc(self.as_of, "as_of"))
        if self.first_observation_at is not None:
            object.__setattr__(
                self,
                "first_observation_at",
                _require_aware_utc(self.first_observation_at, "first_observation_at"),
            )
        if self.last_observation_at is not None:
            object.__setattr__(
                self,
                "last_observation_at",
                _require_aware_utc(self.last_observation_at, "last_observation_at"),
            )


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def _require_decimal_or_none(value: Any, field_name: str) -> None:
    if value is None:
        return
    _require_decimal(value, field_name)


def _require_decimal(value: Any, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise InvalidDecimalError(f"{field_name} must be a Decimal or None.")
    if not value.is_finite():
        raise InvalidDecimalError(f"{field_name} must be a finite Decimal.")


def _require_fee_policy(value: Any) -> None:
    if not isinstance(value, FeePolicy):
        raise ContractValidationError("fee_policy must be a FeePolicy value.")
    _require_decimal(value.nominal_rate, "fee_policy.nominal_rate")
    _require_decimal(value.currency_quantum, "fee_policy.currency_quantum")
    if value.nominal_rate < Decimal("0") or value.nominal_rate >= Decimal("1"):
        raise ContractValidationError("fee_policy.nominal_rate must satisfy 0 <= fee < 1.")
    if value.currency_quantum <= Decimal("0"):
        raise ContractValidationError("fee_policy.currency_quantum must be greater than 0.")


def observation_sort_key(observation: MarketObservation) -> tuple[object, ...]:
    return (
        observation.observed_at,
        observation.observation_key or "",
        _decimal_sort_value(observation.best_ask),
        _decimal_sort_value(observation.best_bid),
        observation.ask_count if observation.ask_count is not None else -1,
        observation.bid_count if observation.bid_count is not None else -1,
        _decimal_sort_value(observation.estimated_volume),
    )


def _decimal_sort_value(value: Decimal | None) -> tuple[int, str]:
    if value is None:
        return (0, "")
    return (1, str(value.normalize()))
