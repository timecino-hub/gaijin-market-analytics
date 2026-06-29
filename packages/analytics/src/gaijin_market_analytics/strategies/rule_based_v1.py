from dataclasses import dataclass
from decimal import Decimal

from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult, MarketObservation
from gaijin_market_analytics.enums import AnalysisStatus, ReasonCode
from gaijin_market_analytics.fees import (
    calculate_break_even_sell_price,
    calculate_gross_profit,
    calculate_net_profit,
    calculate_net_roi,
)
from gaijin_market_analytics.horizons import coverage_ratio, select_horizon_observations
from gaijin_market_analytics.statistics import (
    decimal_median,
    decimal_median_absolute_deviation,
    latest_valid_ask,
    latest_valid_bid,
    spread_absolute,
    spread_ratio,
    valid_price,
)


@dataclass(frozen=True)
class RuleBasedV1Config:
    minimum_coverage_ratio: Decimal = Decimal("0.50")
    low_liquidity_count_threshold: Decimal = Decimal("5")
    large_spread_ratio_threshold: Decimal = Decimal("0.15")
    liquidity_count_full_score: Decimal = Decimal("20")
    spread_full_penalty_ratio: Decimal = Decimal("0.25")
    volatility_full_penalty_ratio: Decimal = Decimal("0.30")
    coverage_weight: Decimal = Decimal("35")
    liquidity_weight: Decimal = Decimal("35")
    risk_weight: Decimal = Decimal("30")


class RuleBasedV1:
    strategy_name = "rule_based"
    strategy_version = "1.0.0"
    feature_version = "market_features_v1"

    def __init__(self, config: RuleBasedV1Config | None = None) -> None:
        self.config = config or RuleBasedV1Config()

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        window = select_horizon_observations(
            request.observations,
            request.as_of,
            request.horizon,
        )
        reason_codes: list[ReasonCode] = []
        observation_count = len(window)
        first_at = window[0].observed_at if window else None
        last_at = window[-1].observed_at if window else None

        current_ask = latest_valid_ask(window)
        current_bid = latest_valid_bid(window)
        latest_snapshot = window[-1] if window else None

        if not window:
            reason_codes.append(ReasonCode.INSUFFICIENT_SNAPSHOTS)
        if observation_count < request.minimum_snapshot_count:
            _append_once(reason_codes, ReasonCode.INSUFFICIENT_SNAPSHOTS)
        if current_ask is None:
            reason_codes.append(ReasonCode.NO_CURRENT_ASK)
        if current_bid is None:
            reason_codes.append(ReasonCode.NO_CURRENT_BID)
        if _has_invalid_price(window):
            reason_codes.append(ReasonCode.INVALID_PRICE)
        if latest_snapshot is not None and request.as_of - latest_snapshot.observed_at > request.maximum_snapshot_age:
            reason_codes.append(ReasonCode.STALE_LATEST_SNAPSHOT)

        coverage = coverage_ratio(window, request.horizon)
        if coverage < self.config.minimum_coverage_ratio:
            reason_codes.append(ReasonCode.INSUFFICIENT_TIME_COVERAGE)

        median_bid = decimal_median(tuple(observation.best_bid for observation in window))
        median_ask = decimal_median(tuple(observation.best_ask for observation in window))
        price_volatility = decimal_median_absolute_deviation(
            tuple(observation.best_bid for observation in window)
        )
        absolute_spread = spread_absolute(current_ask, current_bid)
        ratio_spread = spread_ratio(current_ask, current_bid)

        if ratio_spread is not None and ratio_spread > self.config.large_spread_ratio_threshold:
            reason_codes.append(ReasonCode.LARGE_SPREAD)
        if ratio_spread is not None and ratio_spread < Decimal("0"):
            reason_codes.append(ReasonCode.INVALID_PRICE)

        liquidity_score = self._liquidity_score(window)
        if liquidity_score is not None and liquidity_score < self._liquidity_warning_score():
            reason_codes.append(ReasonCode.LOW_LIQUIDITY)

        risk_score = self._risk_score(ratio_spread, price_volatility, median_bid)
        confidence_score = self._confidence_score(coverage, liquidity_score, risk_score)

        reference_sell_price = median_bid
        gross_profit = None
        net_profit = None
        net_roi = None
        break_even_sell_price = None
        if current_ask is not None:
            break_even_sell_price = calculate_break_even_sell_price(
                current_ask,
                request.marketplace_fee_rate,
            )
        if current_ask is not None and reference_sell_price is not None:
            gross_profit = calculate_gross_profit(current_ask, reference_sell_price)
            net_profit = calculate_net_profit(
                current_ask,
                reference_sell_price,
                request.marketplace_fee_rate,
            )
            net_roi = calculate_net_roi(
                current_ask,
                reference_sell_price,
                request.marketplace_fee_rate,
            )

        status = self._status(reason_codes, window)
        if status == AnalysisStatus.OK:
            reason_codes.append(ReasonCode.ANALYSIS_COMPLETED)

        return AnalysisResult(
            item_id=request.item_id,
            horizon=request.horizon,
            as_of=request.as_of,
            status=status,
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            feature_version=self.feature_version,
            observation_count=observation_count,
            first_observation_at=first_at,
            last_observation_at=last_at,
            current_ask=current_ask,
            current_bid=current_bid,
            reference_sell_price=reference_sell_price,
            gross_profit=gross_profit,
            net_profit=net_profit,
            net_roi=net_roi,
            break_even_sell_price=break_even_sell_price,
            spread_absolute=absolute_spread,
            spread_ratio=ratio_spread,
            median_bid=median_bid,
            median_ask=median_ask,
            price_volatility=price_volatility,
            liquidity_score=liquidity_score,
            risk_score=risk_score,
            confidence_score=confidence_score,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )

    def _status(
        self,
        reason_codes: list[ReasonCode],
        window: tuple[MarketObservation, ...],
    ) -> AnalysisStatus:
        if (
            ReasonCode.INSUFFICIENT_SNAPSHOTS in reason_codes
            or ReasonCode.INSUFFICIENT_TIME_COVERAGE in reason_codes
            or not window
        ):
            return AnalysisStatus.INSUFFICIENT_DATA
        if ReasonCode.NO_CURRENT_ASK in reason_codes or ReasonCode.NO_CURRENT_BID in reason_codes:
            return AnalysisStatus.NO_VALID_PRICE
        if ReasonCode.STALE_LATEST_SNAPSHOT in reason_codes:
            return AnalysisStatus.NO_RECENT_MARKET
        return AnalysisStatus.OK

    def _liquidity_score(self, observations: tuple[MarketObservation, ...]) -> Decimal | None:
        counts = [
            Decimal(observation.ask_count + observation.bid_count)
            for observation in observations
            if observation.ask_count is not None and observation.bid_count is not None
        ]
        if not counts:
            return None
        median_count = decimal_median(tuple(counts))
        if median_count is None:
            return None
        return _clamp_score(
            (median_count / self.config.liquidity_count_full_score) * Decimal("100")
        )

    def _risk_score(
        self,
        ratio_spread: Decimal | None,
        price_volatility: Decimal | None,
        median_bid: Decimal | None,
    ) -> Decimal | None:
        penalties: list[Decimal] = []
        if ratio_spread is not None:
            penalties.append(
                min(
                    Decimal("50"),
                    (abs(ratio_spread) / self.config.spread_full_penalty_ratio) * Decimal("50"),
                )
            )
        if price_volatility is not None and median_bid is not None and median_bid > Decimal("0"):
            volatility_ratio = price_volatility / median_bid
            penalties.append(
                min(
                    Decimal("50"),
                    (volatility_ratio / self.config.volatility_full_penalty_ratio)
                    * Decimal("50"),
                )
            )
        if not penalties:
            return None
        return _clamp_score(sum(penalties, Decimal("0")))

    def _confidence_score(
        self,
        coverage: Decimal,
        liquidity_score: Decimal | None,
        risk_score: Decimal | None,
    ) -> Decimal | None:
        if liquidity_score is None or risk_score is None:
            return None
        risk_component = Decimal("100") - risk_score
        return _clamp_score(
            coverage * self.config.coverage_weight
            + (liquidity_score / Decimal("100")) * self.config.liquidity_weight
            + (risk_component / Decimal("100")) * self.config.risk_weight
        )

    def _liquidity_warning_score(self) -> Decimal:
        return _clamp_score(
            (
                self.config.low_liquidity_count_threshold
                / self.config.liquidity_count_full_score
            )
            * Decimal("100")
        )


def _has_invalid_price(observations: tuple[MarketObservation, ...]) -> bool:
    return any(
        (observation.best_ask is not None and valid_price(observation.best_ask) is None)
        or (observation.best_bid is not None and valid_price(observation.best_bid) is None)
        for observation in observations
    )


def _append_once(reason_codes: list[ReasonCode], reason_code: ReasonCode) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)


def _clamp_score(value: Decimal) -> Decimal:
    return min(Decimal("100"), max(Decimal("0"), value))
