from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from gaijin_market_analytics.contracts import MarketObservation, observation_sort_key
from gaijin_market_analytics.enums import AnalysisStatus
from gaijin_market_analytics.exceptions import ContractValidationError

if TYPE_CHECKING:
    from gaijin_market_analytics.backtesting.trade_evidence_contracts import (
        TradeEvidenceCalibrationResult,
    )
else:
    TradeEvidenceCalibrationResult = Any


SUPPORTED_CALIBRATION_HORIZON_DAYS = frozenset({7, 30, 90, 180})


class CalibrationSplit(str, Enum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class CalibrationCaseStatus(str, Enum):
    EVALUATED = "evaluated"
    ANALYSIS_UNAVAILABLE = "analysis_unavailable"
    FUTURE_DATA_UNAVAILABLE = "future_data_unavailable"
    ENTRY_UNAVAILABLE = "entry_unavailable"
    EXIT_UNAVAILABLE = "exit_unavailable"


class CalibrationExitReason(str, Enum):
    TARGET_CONFIRMED = "target_confirmed"
    TERMINAL_LIQUIDATION = "terminal_liquidation"


class CalibrationSkipReason(str, Enum):
    ANALYSIS_STATUS_NOT_OK = "analysis_status_not_ok"
    FUTURE_WINDOW_INCOMPLETE = "future_window_incomplete"
    NO_FUTURE_OBSERVATIONS = "no_future_observations"
    MISSING_ENTRY_ASK = "missing_entry_ask"
    ENTRY_QUOTE_NOT_CONFIRMED = "entry_quote_not_confirmed"
    MISSING_TARGET_PRICE = "missing_target_price"
    NO_VALID_FUTURE_BID = "no_valid_future_bid"
    TERMINAL_BID_TOO_OLD = "terminal_bid_too_old"
    TERMINAL_LIQUIDATION_DISABLED = "terminal_liquidation_disabled"


@dataclass(frozen=True, slots=True)
class TemporalSplitConfig:
    train_end_at: datetime
    validation_end_at: datetime

    def __post_init__(self) -> None:
        train_end_at = _require_aware_utc(self.train_end_at, "train_end_at")
        validation_end_at = _require_aware_utc(
            self.validation_end_at,
            "validation_end_at",
        )
        if train_end_at >= validation_end_at:
            raise ContractValidationError(
                "train_end_at must be earlier than validation_end_at."
            )
        object.__setattr__(self, "train_end_at", train_end_at)
        object.__setattr__(self, "validation_end_at", validation_end_at)

    def split_for(self, cutoff: datetime) -> CalibrationSplit:
        cutoff = _require_aware_utc(cutoff, "cutoff")
        if cutoff <= self.train_end_at:
            return CalibrationSplit.TRAIN
        if cutoff <= self.validation_end_at:
            return CalibrationSplit.VALIDATION
        return CalibrationSplit.TEST


@dataclass(frozen=True, slots=True)
class OpportunityCalibrationConfig:
    lookback_horizon_days: int
    forward_horizon_days: tuple[int, ...]
    start_at: datetime
    end_at: datetime
    cadence_days: int
    temporal_splits: TemporalSplitConfig
    require_complete_forward_window: bool = True
    maximum_snapshot_age_hours: int = 24
    minimum_snapshot_count: int = 3
    minimum_entry_quote_observations: int = 2
    minimum_exit_quote_observations: int = 2
    maximum_terminal_snapshot_age_hours: int = 24
    force_terminal_liquidation: bool = True
    score_bin_edges: tuple[Decimal, ...] = (
        Decimal("0"),
        Decimal("20"),
        Decimal("40"),
        Decimal("60"),
        Decimal("80"),
        Decimal("100"),
    )

    def __post_init__(self) -> None:
        if self.lookback_horizon_days not in SUPPORTED_CALIBRATION_HORIZON_DAYS:
            raise ContractValidationError(
                "lookback_horizon_days must be one of 7, 30, 90, 180."
            )
        horizons = tuple(sorted(set(self.forward_horizon_days)))
        if not horizons or any(
            horizon not in SUPPORTED_CALIBRATION_HORIZON_DAYS
            for horizon in horizons
        ):
            raise ContractValidationError(
                "forward_horizon_days must contain only 7, 30, 90, 180."
            )
        start_at = _require_aware_utc(self.start_at, "start_at")
        end_at = _require_aware_utc(self.end_at, "end_at")
        if start_at > end_at:
            raise ContractValidationError(
                "start_at must be earlier than or equal to end_at."
            )
        if not start_at <= self.temporal_splits.train_end_at:
            raise ContractValidationError(
                "train_end_at must be on or after start_at."
            )
        if not self.temporal_splits.validation_end_at < end_at:
            raise ContractValidationError(
                "validation_end_at must be earlier than end_at so a test split exists."
            )
        for field_name in (
            "cadence_days",
            "maximum_snapshot_age_hours",
            "minimum_snapshot_count",
            "minimum_entry_quote_observations",
            "minimum_exit_quote_observations",
            "maximum_terminal_snapshot_age_hours",
        ):
            if getattr(self, field_name) <= 0:
                raise ContractValidationError(f"{field_name} must be greater than 0.")
        edges = tuple(self.score_bin_edges)
        if len(edges) < 2:
            raise ContractValidationError("score_bin_edges must contain at least two edges.")
        for edge in edges:
            if not isinstance(edge, Decimal) or not edge.is_finite():
                raise ContractValidationError(
                    "score_bin_edges must contain finite Decimal values."
                )
        if edges[0] != Decimal("0") or edges[-1] != Decimal("100"):
            raise ContractValidationError(
                "score_bin_edges must start at 0 and end at 100."
            )
        if any(left >= right for left, right in zip(edges, edges[1:])):
            raise ContractValidationError(
                "score_bin_edges must be strictly increasing."
            )
        object.__setattr__(self, "forward_horizon_days", horizons)
        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)
        object.__setattr__(self, "score_bin_edges", edges)


@dataclass(frozen=True, slots=True)
class ItemMarketHistory:
    item_id: int
    observations: tuple[MarketObservation, ...]

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise ContractValidationError("item_id must be a positive integer.")
        observations = tuple(self.observations)
        if any(not isinstance(value, MarketObservation) for value in observations):
            raise ContractValidationError(
                "observations must contain MarketObservation values."
            )
        object.__setattr__(
            self,
            "observations",
            tuple(sorted(observations, key=observation_sort_key)),
        )


@dataclass(frozen=True, slots=True)
class OpportunityCalibrationCase:
    item_id: int
    cutoff_as_of: datetime
    split: CalibrationSplit
    forward_horizon_days: int
    status: CalibrationCaseStatus
    skip_reasons: tuple[CalibrationSkipReason, ...]
    analysis_status: AnalysisStatus | None
    analysis_strategy_name: str | None
    analysis_strategy_version: str | None
    opportunity_strategy_name: str | None
    opportunity_strategy_version: str | None
    opportunity_feature_version: str | None
    opportunity_eligible: bool | None
    score: Decimal | None
    profitability_score: Decimal | None
    liquidity_score: Decimal | None
    stability_score: Decimal | None
    data_confidence_score: Decimal | None
    freshness_score: Decimal | None
    risk_penalty: Decimal | None
    observation_count: int
    future_observation_count: int
    future_window_complete: bool
    entry_at: datetime | None
    entry_price: Decimal | None
    entry_confirmation_count: int
    entry_executed: bool
    target_exit_price: Decimal | None
    exit_reason: CalibrationExitReason | None
    exit_at: datetime | None
    exit_price: Decimal | None
    exit_confirmation_count: int
    exit_executed: bool
    holding_seconds: int | None
    sale_proceeds: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    positive_return: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cutoff_as_of",
            _require_aware_utc(self.cutoff_as_of, "cutoff_as_of"),
        )
        if self.entry_at is not None:
            object.__setattr__(
                self,
                "entry_at",
                _require_aware_utc(self.entry_at, "entry_at"),
            )
        if self.exit_at is not None:
            object.__setattr__(
                self,
                "exit_at",
                _require_aware_utc(self.exit_at, "exit_at"),
            )
        object.__setattr__(
            self,
            "skip_reasons",
            tuple(dict.fromkeys(self.skip_reasons)),
        )


@dataclass(frozen=True, slots=True)
class ScoreBinSummary:
    split: CalibrationSplit | None
    forward_horizon_days: int | None
    lower_bound: Decimal
    upper_bound: Decimal
    includes_upper_bound: bool
    scored_case_count: int
    eligible_case_count: int
    entry_executed_count: int
    realized_trade_count: int
    positive_return_count: int
    positive_return_rate: Decimal | None
    mean_net_roi: Decimal | None
    median_net_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class ComponentCorrelation:
    split: CalibrationSplit | None
    forward_horizon_days: int | None
    component_name: str
    sample_count: int
    spearman_with_net_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class CalibrationCohortSummary:
    split: CalibrationSplit | None
    forward_horizon_days: int | None
    total_case_count: int
    scored_case_count: int
    eligible_case_count: int
    complete_future_case_count: int
    entry_executed_count: int
    realized_trade_count: int
    target_exit_count: int
    terminal_liquidation_count: int
    unresolved_exit_count: int
    positive_return_count: int
    eligible_realized_trade_count: int
    eligible_positive_return_count: int
    eligible_positive_return_rate: Decimal | None
    eligible_total_net_profit: Decimal | None
    entry_execution_rate: Decimal | None
    realized_trade_rate: Decimal | None
    positive_return_rate: Decimal | None
    total_net_profit: Decimal | None
    mean_net_roi: Decimal | None
    median_net_roi: Decimal | None
    mean_holding_seconds: int | None
    median_holding_seconds: int | None
    peak_concurrent_entry_cost: Decimal | None
    ending_equity: Decimal | None
    maximum_drawdown: Decimal | None
    maximum_drawdown_rate: Decimal | None
    score_roi_spearman: Decimal | None
    profitability_baseline_spearman: Decimal | None
    score_correlation_lift_over_profitability: Decimal | None
    adjacent_bin_monotonicity_rate: Decimal | None
    assessment_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpportunityCalibrationResult:
    config: OpportunityCalibrationConfig
    engine_name: str
    engine_version: str
    execution_policy_name: str
    execution_policy_version: str
    fee_policy_name: str
    fee_policy_version: str
    market_rules_name: str
    market_rules_version: str
    dataset_sha256: str
    configuration_sha256: str
    cases: tuple[OpportunityCalibrationCase, ...]
    cohorts: tuple[CalibrationCohortSummary, ...]
    score_bins: tuple[ScoreBinSummary, ...]
    component_correlations: tuple[ComponentCorrelation, ...]
    trade_evidence: TradeEvidenceCalibrationResult | None = None


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
