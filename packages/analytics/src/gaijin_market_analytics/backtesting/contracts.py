from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from gaijin_market_analytics.enums import AnalysisStatus, ReasonCode
from gaijin_market_analytics.exceptions import ContractValidationError


SUPPORTED_BACKTEST_HORIZON_DAYS = frozenset({7, 30, 90, 180})


class BacktestCaseStatus(str, Enum):
    EVALUATED = "evaluated"
    ANALYSIS_UNAVAILABLE = "analysis_unavailable"
    FUTURE_DATA_UNAVAILABLE = "future_data_unavailable"


class BacktestSkipReason(str, Enum):
    ANALYSIS_STATUS_NOT_OK = "analysis_status_not_ok"
    MISSING_ENTRY_ASK = "missing_entry_ask"
    MISSING_REFERENCE_SELL_PRICE = "missing_reference_sell_price"
    NO_FUTURE_OBSERVATIONS = "no_future_observations"
    NO_VALID_FUTURE_BID = "no_valid_future_bid"
    FUTURE_WINDOW_INCOMPLETE = "future_window_incomplete"


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    strategy_name: str
    strategy_version: str
    lookback_horizon_days: int
    forward_horizon_days: int
    start_at: datetime
    end_at: datetime
    cadence_days: int
    require_complete_forward_window: bool = True
    maximum_snapshot_age_hours: int = 24
    minimum_snapshot_count: int = 3

    def __post_init__(self) -> None:
        if self.lookback_horizon_days not in SUPPORTED_BACKTEST_HORIZON_DAYS:
            raise ContractValidationError("lookback_horizon_days must be one of 7, 30, 90, 180.")
        if self.forward_horizon_days not in SUPPORTED_BACKTEST_HORIZON_DAYS:
            raise ContractValidationError("forward_horizon_days must be one of 7, 30, 90, 180.")
        start_at = _require_aware_utc(self.start_at, "start_at")
        end_at = _require_aware_utc(self.end_at, "end_at")
        if start_at > end_at:
            raise ContractValidationError("start_at must be earlier than or equal to end_at.")
        if self.cadence_days <= 0:
            raise ContractValidationError("cadence_days must be greater than 0.")
        if self.maximum_snapshot_age_hours <= 0:
            raise ContractValidationError("maximum_snapshot_age_hours must be greater than 0.")
        if self.minimum_snapshot_count <= 0:
            raise ContractValidationError("minimum_snapshot_count must be greater than 0.")
        if not self.strategy_name:
            raise ContractValidationError("strategy_name must not be empty.")
        if not self.strategy_version:
            raise ContractValidationError("strategy_version must not be empty.")
        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)


@dataclass(frozen=True, slots=True)
class BacktestCaseResult:
    cutoff_as_of: datetime
    status: BacktestCaseStatus
    skip_reasons: tuple[BacktestSkipReason, ...]
    strategy_name: str
    strategy_version: str
    market_rules_name: str
    market_rules_version: str
    lookback_horizon_days: int
    forward_horizon_days: int
    analysis_status: AnalysisStatus | None
    analysis_reason_codes: tuple[ReasonCode, ...]
    observation_count: int
    current_ask: Decimal | None
    current_bid: Decimal | None
    reference_sell_price: Decimal | None
    break_even_sell_price: Decimal | None
    break_even_reachable: bool | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    risk_score: Decimal | None
    liquidity_score: Decimal | None
    confidence_score: Decimal | None
    future_observation_count: int
    future_valid_bid_count: int
    terminal_bid: Decimal | None
    maximum_future_bid: Decimal | None
    minimum_future_bid: Decimal | None
    reference_reached: bool | None
    break_even_reached: bool | None
    positive_terminal_return: bool | None
    terminal_sale_proceeds: Decimal | None
    terminal_net_profit: Decimal | None
    terminal_net_roi: Decimal | None
    maximum_sale_proceeds_in_window: Decimal | None
    maximum_net_profit_in_window: Decimal | None
    maximum_net_roi_in_window: Decimal | None
    reference_price_error: Decimal | None
    absolute_reference_price_error: Decimal | None
    time_to_reference_seconds: int | None
    time_to_break_even_seconds: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cutoff_as_of", _require_aware_utc(self.cutoff_as_of, "cutoff_as_of"))
        object.__setattr__(self, "skip_reasons", tuple(dict.fromkeys(self.skip_reasons)))


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    """Aggregate metrics with explicit denominators.

    reference_evaluable_count counts complete-window cases where
    reference_sell_price exists. break_even_evaluable_count counts
    complete-window cases where a legal entry ask exists.
    terminal_return_evaluable_count counts complete-window cases where entry ask
    and terminal bid both exist. Rates are None, not zero, when the matching
    denominator is zero.
    """

    strategy_name: str
    strategy_version: str
    market_rules_name: str
    market_rules_version: str
    lookback_horizon_days: int
    forward_horizon_days: int
    start_at: datetime
    end_at: datetime
    cadence_days: int
    require_complete_forward_window: bool
    total_case_count: int
    analysis_ok_case_count: int
    evaluated_case_count: int
    analysis_unavailable_count: int
    future_data_unavailable_count: int
    reference_evaluable_count: int
    reference_reached_count: int
    reference_reach_rate: Decimal | None
    break_even_evaluable_count: int
    break_even_reached_count: int
    break_even_reach_rate: Decimal | None
    terminal_return_evaluable_count: int
    positive_terminal_return_count: int
    positive_terminal_return_rate: Decimal | None
    median_terminal_net_roi: Decimal | None
    median_maximum_net_roi: Decimal | None
    mean_absolute_reference_price_error: Decimal | None
    median_absolute_reference_price_error: Decimal | None
    median_time_to_reference_seconds: int | None
    median_time_to_break_even_seconds: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_at", _require_aware_utc(self.start_at, "start_at"))
        object.__setattr__(self, "end_at", _require_aware_utc(self.end_at, "end_at"))


@dataclass(frozen=True, slots=True)
class BacktestResult:
    config: BacktestConfig
    summary: BacktestSummary
    cases: tuple[BacktestCaseResult, ...]


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
