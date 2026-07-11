from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum

from gaijin_market_analytics.backtesting.calibration_contracts import CalibrationSplit
from gaijin_market_analytics.exceptions import ContractValidationError


PRICE_SEMANTICS = "bucket_volume_weighted_average_trade_price"
VOLUME_SEMANTICS = "reported_trade_volume_unknown_unit"
TRADE_EVIDENCE_ENGINE_NAME = "historical_trade_evidence"
TRADE_EVIDENCE_ENGINE_VERSION = "1.0.0"
TRADE_EVIDENCE_POLICY_NAME = "completed_bucket_vwap_support"
TRADE_EVIDENCE_POLICY_VERSION = "1.0.0"


class HistoricalTradeGranularity(str, Enum):
    HOUR_1 = "1h"
    DAY_1 = "1d"

    @property
    def duration_seconds(self) -> int:
        if self is HistoricalTradeGranularity.HOUR_1:
            return 3_600
        return 86_400


class TradeEvidenceLevel(str, Enum):
    UNRESOLVED = "unresolved"
    QUOTE_TOUCH = "quote_touch"
    TRADE_VWAP_SUPPORT = "trade_vwap_support"
    TRADE_VOLUME_SUPPORT = "trade_volume_support"


class TradeEvidenceView(str, Enum):
    QUOTE_ONLY = "quote_only"
    TRADE_SUPPORTED = "trade_supported"
    COMBINED = "combined"


class TradeEvidenceSegmentDimension(str, Enum):
    SCORE_BIN = "score_bin"
    LIQUIDITY_BAND = "liquidity_band"
    MARKET_PHASE = "market_phase"


class TradeLiquidityBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNAVAILABLE = "unavailable"


class TradeMarketPhase(str, Enum):
    UNAVAILABLE = "unavailable"
    THIN = "thin"
    NORMAL = "normal"
    SUPPLY_SHOCK = "supply_shock"
    RECOVERY = "recovery"


@dataclass(frozen=True, slots=True)
class HistoricalTradeBucket:
    item_id: int
    granularity: HistoricalTradeGranularity
    bucket_start_utc: datetime
    bucket_duration_seconds: int
    reported_vwap_price: Decimal
    reported_trade_volume: int
    price_semantics: str = PRICE_SEMANTICS
    volume_semantics: str = VOLUME_SEMANTICS
    source_schema_version: str = "gaijin_trade_history_v1"

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise ContractValidationError("item_id must be a positive integer.")
        if not isinstance(self.granularity, HistoricalTradeGranularity):
            raise ContractValidationError(
                "granularity must be a HistoricalTradeGranularity value."
            )
        start = _require_aware_utc(self.bucket_start_utc, "bucket_start_utc")
        if self.bucket_duration_seconds != self.granularity.duration_seconds:
            raise ContractValidationError(
                "bucket_duration_seconds must match granularity."
            )
        if int(start.timestamp()) % self.bucket_duration_seconds != 0:
            raise ContractValidationError(
                "bucket_start_utc must align to its granularity boundary."
            )
        if (
            not isinstance(self.reported_vwap_price, Decimal)
            or not self.reported_vwap_price.is_finite()
            or self.reported_vwap_price <= Decimal("0")
        ):
            raise ContractValidationError(
                "reported_vwap_price must be a positive finite Decimal."
            )
        if self.reported_trade_volume <= 0:
            raise ContractValidationError(
                "reported_trade_volume must be a positive integer."
            )
        if self.price_semantics != PRICE_SEMANTICS:
            raise ContractValidationError("Unsupported historical trade price semantics.")
        if self.volume_semantics != VOLUME_SEMANTICS:
            raise ContractValidationError("Unsupported historical trade volume semantics.")
        if not self.source_schema_version.strip():
            raise ContractValidationError("source_schema_version must not be empty.")
        object.__setattr__(self, "bucket_start_utc", start)

    @property
    def bucket_end_utc(self) -> datetime:
        return self.bucket_start_utc + timedelta(seconds=self.bucket_duration_seconds)


@dataclass(frozen=True, slots=True)
class ItemHistoricalTradeHistory:
    item_id: int
    buckets: tuple[HistoricalTradeBucket, ...]

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise ContractValidationError("item_id must be a positive integer.")
        buckets = tuple(self.buckets)
        if any(not isinstance(value, HistoricalTradeBucket) for value in buckets):
            raise ContractValidationError(
                "buckets must contain HistoricalTradeBucket values."
            )
        if any(value.item_id != self.item_id for value in buckets):
            raise ContractValidationError(
                "Every historical trade bucket must match the history item_id."
            )
        keys = tuple((value.granularity, value.bucket_start_utc) for value in buckets)
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "Historical trade histories cannot contain duplicate natural keys."
            )
        object.__setattr__(
            self,
            "buckets",
            tuple(
                sorted(
                    buckets,
                    key=lambda value: (
                        value.bucket_start_utc,
                        value.bucket_duration_seconds,
                    ),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class TradeEvidenceConfig:
    minimum_reported_trade_volume: int = 2
    phase_lookback_days: int = 30
    minimum_phase_bucket_count: int = 3
    supply_shock_price_ratio: Decimal = Decimal("0.75")
    supply_shock_volume_multiplier: Decimal = Decimal("3")
    recovery_price_ratio: Decimal = Decimal("1.20")
    liquidity_band_edges: tuple[Decimal, Decimal] = (
        Decimal("40"),
        Decimal("70"),
    )

    def __post_init__(self) -> None:
        for field_name in (
            "minimum_reported_trade_volume",
            "phase_lookback_days",
            "minimum_phase_bucket_count",
        ):
            if getattr(self, field_name) <= 0:
                raise ContractValidationError(f"{field_name} must be greater than 0.")
        for field_name in (
            "supply_shock_price_ratio",
            "supply_shock_volume_multiplier",
            "recovery_price_ratio",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ContractValidationError(
                    f"{field_name} must be a positive finite Decimal."
                )
        if self.supply_shock_price_ratio >= Decimal("1"):
            raise ContractValidationError(
                "supply_shock_price_ratio must be less than 1."
            )
        if self.supply_shock_volume_multiplier <= Decimal("1"):
            raise ContractValidationError(
                "supply_shock_volume_multiplier must be greater than 1."
            )
        if self.recovery_price_ratio <= Decimal("1"):
            raise ContractValidationError(
                "recovery_price_ratio must be greater than 1."
            )
        edges = tuple(self.liquidity_band_edges)
        if len(edges) != 2:
            raise ContractValidationError(
                "liquidity_band_edges must contain exactly two values."
            )
        lower, upper = edges
        if not (
            isinstance(lower, Decimal)
            and isinstance(upper, Decimal)
            and lower.is_finite()
            and upper.is_finite()
            and Decimal("0") < lower < upper < Decimal("100")
        ):
            raise ContractValidationError(
                "liquidity_band_edges must contain two increasing Decimal values between 0 and 100."
            )
        object.__setattr__(self, "liquidity_band_edges", edges)


@dataclass(frozen=True, slots=True)
class TradeEvidenceCase:
    item_id: int
    cutoff_as_of: datetime
    split: CalibrationSplit
    forward_horizon_days: int
    entry_executed: bool
    entry_price: Decimal | None
    target_exit_price: Decimal | None
    opportunity_eligible: bool | None
    score: Decimal | None
    liquidity_score: Decimal | None
    market_phase: TradeMarketPhase
    quote_touch: bool
    quote_touch_at: datetime | None
    trade_vwap_support: bool
    trade_volume_support: bool
    trade_support_at: datetime | None
    trade_support_granularity: HistoricalTradeGranularity | None
    trade_support_vwap: Decimal | None
    trade_support_volume: int | None
    selected_trade_bucket_count: int
    selected_hourly_bucket_count: int
    selected_daily_fallback_count: int
    combined_support: bool
    strongest_evidence: TradeEvidenceLevel
    supported_sale_proceeds: Decimal | None
    supported_net_profit: Decimal | None
    supported_net_roi: Decimal | None
    positive_return: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cutoff_as_of",
            _require_aware_utc(self.cutoff_as_of, "cutoff_as_of"),
        )
        if self.quote_touch_at is not None:
            object.__setattr__(
                self,
                "quote_touch_at",
                _require_aware_utc(self.quote_touch_at, "quote_touch_at"),
            )
        if self.trade_support_at is not None:
            object.__setattr__(
                self,
                "trade_support_at",
                _require_aware_utc(self.trade_support_at, "trade_support_at"),
            )


@dataclass(frozen=True, slots=True)
class TradeEvidenceCohortSummary:
    view: TradeEvidenceView
    split: CalibrationSplit | None
    forward_horizon_days: int
    total_case_count: int
    evaluable_case_count: int
    supported_case_count: int
    volume_supported_count: int
    eligible_supported_count: int
    positive_return_count: int
    support_rate: Decimal | None
    positive_return_rate: Decimal | None
    mean_supported_net_roi: Decimal | None
    median_supported_net_roi: Decimal | None
    mean_time_to_support_seconds: int | None
    score_roi_spearman: Decimal | None


@dataclass(frozen=True, slots=True)
class TradeEvidenceSegmentSummary:
    view: TradeEvidenceView
    split: CalibrationSplit | None
    forward_horizon_days: int
    dimension: TradeEvidenceSegmentDimension
    segment: str
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    includes_upper_bound: bool
    total_case_count: int
    evaluable_case_count: int
    supported_case_count: int
    volume_supported_count: int
    positive_return_count: int
    support_rate: Decimal | None
    positive_return_rate: Decimal | None
    mean_supported_net_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class TradeEvidenceCalibrationResult:
    config: TradeEvidenceConfig
    engine_name: str
    engine_version: str
    policy_name: str
    policy_version: str
    dataset_sha256: str
    configuration_sha256: str
    cases: tuple[TradeEvidenceCase, ...]
    cohorts: tuple[TradeEvidenceCohortSummary, ...]
    segments: tuple[TradeEvidenceSegmentSummary, ...]


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
