from enum import Enum


class AnalysisHorizon(Enum):
    DAYS_7 = 7
    DAYS_30 = 30
    DAYS_90 = 90
    DAYS_180 = 180


class AnalysisStatus(str, Enum):
    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"
    INVALID_INPUT = "invalid_input"
    NO_RECENT_MARKET = "no_recent_market"
    NO_VALID_PRICE = "no_valid_price"


class ReasonCode(str, Enum):
    INSUFFICIENT_SNAPSHOTS = "insufficient_snapshots"
    INSUFFICIENT_TIME_COVERAGE = "insufficient_time_coverage"
    NO_CURRENT_ASK = "no_current_ask"
    NO_CURRENT_BID = "no_current_bid"
    INVALID_PRICE = "invalid_price"
    INVALID_FEE_RATE = "invalid_fee_rate"
    STALE_LATEST_SNAPSHOT = "stale_latest_snapshot"
    LOW_LIQUIDITY = "low_liquidity"
    LARGE_SPREAD = "large_spread"
    ANALYSIS_COMPLETED = "analysis_completed"
