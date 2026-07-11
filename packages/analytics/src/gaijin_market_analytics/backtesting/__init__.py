"""Pure walk-forward backtesting contracts and engine."""

from gaijin_market_analytics.backtesting.contracts import (
    BacktestCaseResult,
    BacktestCaseStatus,
    BacktestConfig,
    BacktestResult,
    BacktestSkipReason,
    BacktestSummary,
)
from gaijin_market_analytics.backtesting.calibration import (
    generate_calibration_cutoffs,
    run_opportunity_calibration,
)
from gaijin_market_analytics.backtesting.calibration_contracts import (
    CalibrationCaseStatus,
    CalibrationCohortSummary,
    CalibrationExitReason,
    CalibrationSkipReason,
    CalibrationSplit,
    ComponentCorrelation,
    ItemMarketHistory,
    OpportunityCalibrationCase,
    OpportunityCalibrationConfig,
    OpportunityCalibrationResult,
    ScoreBinSummary,
    TemporalSplitConfig,
)
from gaijin_market_analytics.backtesting.engine import generate_cutoffs, run_backtest

__all__ = [
    "BacktestCaseResult",
    "BacktestCaseStatus",
    "BacktestConfig",
    "BacktestResult",
    "BacktestSkipReason",
    "BacktestSummary",
    "CalibrationCaseStatus",
    "CalibrationCohortSummary",
    "CalibrationExitReason",
    "CalibrationSkipReason",
    "CalibrationSplit",
    "ComponentCorrelation",
    "ItemMarketHistory",
    "OpportunityCalibrationCase",
    "OpportunityCalibrationConfig",
    "OpportunityCalibrationResult",
    "ScoreBinSummary",
    "TemporalSplitConfig",
    "generate_calibration_cutoffs",
    "generate_cutoffs",
    "run_backtest",
    "run_opportunity_calibration",
]
