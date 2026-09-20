from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from gaijin_market_analytics.contracts import (
    AnalysisResult,
    MarketObservation,
    observation_sort_key,
)
from gaijin_market_analytics.enums import AnalysisStatus, ReasonCode
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.statistics import decimal_median

LiquiditySource = Literal[
    "reviewed_screenshot_quantity",
    "snapshot_counts",
    "unavailable",
]


@dataclass(frozen=True)
class OpportunityScoreConfig:
    profitability_weight: Decimal = Decimal("35")
    liquidity_weight: Decimal = Decimal("25")
    stability_weight: Decimal = Decimal("15")
    confidence_weight: Decimal = Decimal("15")
    freshness_weight: Decimal = Decimal("10")
    full_profitability_roi: Decimal = Decimal("0.20")
    full_liquidity_quantity: Decimal = Decimal("40")
    full_volatility_ratio: Decimal = Decimal("0.30")
    risk_penalty_weight: Decimal = Decimal("0.15")

    def __post_init__(self) -> None:
        decimal_fields = (
            ("profitability_weight", self.profitability_weight),
            ("liquidity_weight", self.liquidity_weight),
            ("stability_weight", self.stability_weight),
            ("confidence_weight", self.confidence_weight),
            ("freshness_weight", self.freshness_weight),
            ("full_profitability_roi", self.full_profitability_roi),
            ("full_liquidity_quantity", self.full_liquidity_quantity),
            ("full_volatility_ratio", self.full_volatility_ratio),
            ("risk_penalty_weight", self.risk_penalty_weight),
        )
        for field_name, value in decimal_fields:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ContractValidationError(
                    f"{field_name} must be a finite Decimal."
                )

        weights = (
            self.profitability_weight,
            self.liquidity_weight,
            self.stability_weight,
            self.confidence_weight,
            self.freshness_weight,
        )
        if any(weight < Decimal("0") for weight in weights):
            raise ContractValidationError("Opportunity component weights must be non-negative.")
        if sum(weights, Decimal("0")) != Decimal("100"):
            raise ContractValidationError("Opportunity component weights must sum to 100.")
        if self.full_profitability_roi <= Decimal("0"):
            raise ContractValidationError("full_profitability_roi must be greater than 0.")
        if self.full_liquidity_quantity <= Decimal("0"):
            raise ContractValidationError("full_liquidity_quantity must be greater than 0.")
        if self.full_volatility_ratio <= Decimal("0"):
            raise ContractValidationError("full_volatility_ratio must be greater than 0.")
        if not Decimal("0") <= self.risk_penalty_weight <= Decimal("1"):
            raise ContractValidationError("risk_penalty_weight must satisfy 0 <= value <= 1.")


@dataclass(frozen=True)
class OpportunityScoreResult:
    item_id: int
    as_of: datetime
    eligible: bool
    score: Decimal
    raw_score: Decimal
    profitability_score: Decimal
    liquidity_score: Decimal
    stability_score: Decimal
    data_confidence_score: Decimal
    freshness_score: Decimal
    risk_penalty: Decimal
    liquidity_source: LiquiditySource
    quantity_observation_count: int
    latest_observed_bid_quantity: int | None
    latest_observed_ask_quantity: int | None
    strategy_name: str
    strategy_version: str
    feature_version: str
    explanation_codes: tuple[str, ...]


class OpportunityScoreV1:
    strategy_name = "opportunity_score"
    strategy_version = "1.1.0"
    feature_version = "opportunity_features_v1"

    def __init__(self, config: OpportunityScoreConfig | None = None) -> None:
        self.config = config or OpportunityScoreConfig()

    def score(
        self,
        *,
        analysis: AnalysisResult,
        observations: tuple[MarketObservation, ...],
        maximum_snapshot_age: timedelta,
        minimum_snapshot_count: int,
    ) -> OpportunityScoreResult:
        if maximum_snapshot_age <= timedelta(0):
            raise ContractValidationError("maximum_snapshot_age must be greater than 0.")
        if minimum_snapshot_count <= 0:
            raise ContractValidationError("minimum_snapshot_count must be greater than 0.")
        if analysis.item_id <= 0:
            raise ContractValidationError("analysis.item_id must be a positive integer.")

        ordered = tuple(sorted(observations, key=observation_sort_key))
        if any(observation.observed_at > analysis.as_of for observation in ordered):
            raise ContractValidationError(
                "Opportunity observations must not be later than analysis.as_of."
            )
        if analysis.observation_count != len(ordered):
            raise ContractValidationError(
                "Opportunity observations must match the observations used by analysis."
            )
        expected_first = ordered[0].observed_at if ordered else None
        expected_last = ordered[-1].observed_at if ordered else None
        if (
            analysis.first_observation_at != expected_first
            or analysis.last_observation_at != expected_last
        ):
            raise ContractValidationError(
                "Opportunity observation bounds must match the analyzed window."
            )
        explanation_codes: list[str] = []

        profitability_score = self._profitability_score(analysis)
        if analysis.net_profit is None or analysis.net_profit <= Decimal("0"):
            explanation_codes.append("non_positive_or_unavailable_net_profit")
        else:
            explanation_codes.append("positive_net_profit")

        (
            liquidity_score,
            liquidity_source,
            quantity_observation_count,
            latest_bid_quantity,
            latest_ask_quantity,
        ) = self._liquidity_score(analysis, ordered)
        if liquidity_source == "reviewed_screenshot_quantity":
            explanation_codes.append("reviewed_screenshot_liquidity_proxy")
        elif liquidity_source == "snapshot_counts":
            explanation_codes.append("snapshot_count_liquidity_proxy")
        else:
            explanation_codes.append("liquidity_proxy_unavailable")

        stability_score = self._stability_score(analysis)
        if analysis.price_volatility is None or analysis.median_bid in (None, Decimal("0")):
            explanation_codes.append("stability_unavailable")

        data_confidence_score = self._data_confidence_score(
            analysis=analysis,
            observation_count=len(ordered),
            quantity_observation_count=quantity_observation_count,
            minimum_snapshot_count=minimum_snapshot_count,
        )
        if len(ordered) < minimum_snapshot_count:
            explanation_codes.append("insufficient_observation_count")

        freshness_score = self._freshness_score(
            analysis=analysis,
            maximum_snapshot_age=maximum_snapshot_age,
        )
        if freshness_score == Decimal("0"):
            explanation_codes.append("latest_observation_stale_or_missing")

        risk_penalty = self._risk_penalty(analysis)
        if analysis.risk_score is None:
            explanation_codes.append("risk_score_unavailable")
        elif analysis.risk_score >= Decimal("50"):
            explanation_codes.append("elevated_market_risk")

        raw_score = (
            profitability_score * self.config.profitability_weight
            + liquidity_score * self.config.liquidity_weight
            + stability_score * self.config.stability_weight
            + data_confidence_score * self.config.confidence_weight
            + freshness_score * self.config.freshness_weight
        ) / Decimal("100")
        score = _quantize_score(_clamp_score(raw_score - risk_penalty))
        raw_score = _quantize_score(_clamp_score(raw_score))

        eligible = (
            analysis.status == AnalysisStatus.OK
            and ReasonCode.INVALID_PRICE not in analysis.reason_codes
            and ReasonCode.PRICE_ABOVE_MARKET_CAP not in analysis.reason_codes
            and analysis.current_ask is not None
            and analysis.reference_sell_price is not None
            and analysis.net_profit is not None
            and analysis.net_profit > Decimal("0")
            and analysis.net_roi is not None
            and analysis.net_roi > Decimal("0")
            and liquidity_source != "unavailable"
        )
        if analysis.status != AnalysisStatus.OK:
            explanation_codes.append(f"analysis_status_{analysis.status.value}")
            score = Decimal("0.00")
        if eligible:
            explanation_codes.append("eligible_positive_after_fee_opportunity")
        else:
            explanation_codes.append("not_eligible_for_opportunity_ranking")

        return OpportunityScoreResult(
            item_id=analysis.item_id,
            as_of=analysis.as_of,
            eligible=eligible,
            score=score,
            raw_score=raw_score,
            profitability_score=_quantize_score(profitability_score),
            liquidity_score=_quantize_score(liquidity_score),
            stability_score=_quantize_score(stability_score),
            data_confidence_score=_quantize_score(data_confidence_score),
            freshness_score=_quantize_score(freshness_score),
            risk_penalty=_quantize_score(risk_penalty),
            liquidity_source=liquidity_source,
            quantity_observation_count=quantity_observation_count,
            latest_observed_bid_quantity=latest_bid_quantity,
            latest_observed_ask_quantity=latest_ask_quantity,
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            feature_version=self.feature_version,
            explanation_codes=tuple(dict.fromkeys(explanation_codes)),
        )

    def _profitability_score(self, analysis: AnalysisResult) -> Decimal:
        if analysis.net_roi is None or analysis.net_roi <= Decimal("0"):
            return Decimal("0")
        return _clamp_score(
            (analysis.net_roi / self.config.full_profitability_roi) * Decimal("100")
        )

    def _liquidity_score(
        self,
        analysis: AnalysisResult,
        observations: tuple[MarketObservation, ...],
    ) -> tuple[Decimal, LiquiditySource, int, int | None, int | None]:
        reviewed = tuple(
            observation
            for observation in observations
            if _is_reviewed_screenshot_quantity(observation)
        )
        if reviewed:
            totals = tuple(
                Decimal(observation.observed_bid_quantity + observation.observed_ask_quantity)
                for observation in reviewed
                if observation.observed_bid_quantity is not None
                and observation.observed_ask_quantity is not None
            )
            median_total = decimal_median(totals)
            latest = reviewed[-1]
            if median_total is not None:
                return (
                    _clamp_score(
                        (median_total / self.config.full_liquidity_quantity) * Decimal("100")
                    ),
                    "reviewed_screenshot_quantity",
                    len(reviewed),
                    latest.observed_bid_quantity,
                    latest.observed_ask_quantity,
                )

        count_observations = tuple(
            observation
            for observation in observations
            if observation.ask_count is not None and observation.bid_count is not None
        )
        if analysis.liquidity_score is not None and count_observations:
            return (
                _clamp_score(analysis.liquidity_score),
                "snapshot_counts",
                len(count_observations),
                None,
                None,
            )

        return Decimal("0"), "unavailable", 0, None, None

    def _stability_score(self, analysis: AnalysisResult) -> Decimal:
        if (
            analysis.price_volatility is None
            or analysis.median_bid is None
            or analysis.median_bid <= Decimal("0")
        ):
            return Decimal("0")
        volatility_ratio = analysis.price_volatility / analysis.median_bid
        penalty = _clamp_score(
            (volatility_ratio / self.config.full_volatility_ratio) * Decimal("100")
        )
        return Decimal("100") - penalty

    def _data_confidence_score(
        self,
        *,
        analysis: AnalysisResult,
        observation_count: int,
        quantity_observation_count: int,
        minimum_snapshot_count: int,
    ) -> Decimal:
        count_score = _clamp_score(
            (Decimal(observation_count) / Decimal(minimum_snapshot_count)) * Decimal("100")
        )
        quantity_coverage = (
            Decimal("0")
            if observation_count == 0
            else _clamp_score(
                (Decimal(quantity_observation_count) / Decimal(observation_count))
                * Decimal("100")
            )
        )
        base_confidence = analysis.confidence_score or Decimal("0")
        return _clamp_score(
            count_score * Decimal("0.40")
            + quantity_coverage * Decimal("0.30")
            + base_confidence * Decimal("0.30")
        )

    def _freshness_score(
        self,
        *,
        analysis: AnalysisResult,
        maximum_snapshot_age: timedelta,
    ) -> Decimal:
        if analysis.last_observation_at is None:
            return Decimal("0")
        age = analysis.as_of - analysis.last_observation_at
        if age <= timedelta(0):
            return Decimal("100")
        age_ratio = _timedelta_seconds(age) / _timedelta_seconds(maximum_snapshot_age)
        return _clamp_score((Decimal("1") - age_ratio) * Decimal("100"))

    def _risk_penalty(self, analysis: AnalysisResult) -> Decimal:
        if analysis.risk_score is None:
            return Decimal("0")
        return _clamp_score(analysis.risk_score) * self.config.risk_penalty_weight


def _is_reviewed_screenshot_quantity(observation: MarketObservation) -> bool:
    return (
        observation.quantity_semantics == "screenshot_display_quantity"
        and observation.source_type == "screen_review"
        and observation.review_status in {"confirmed", "confirmed_with_edits"}
        and observation.observed_bid_quantity is not None
        and observation.observed_ask_quantity is not None
    )


def _clamp_score(value: Decimal) -> Decimal:
    return min(Decimal("100"), max(Decimal("0"), value))


def _quantize_score(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _timedelta_seconds(value: timedelta) -> Decimal:
    return (
        Decimal(value.days) * Decimal("86400")
        + Decimal(value.seconds)
        + Decimal(value.microseconds) / Decimal("1000000")
    )


@dataclass(frozen=True)
class RankedOpportunityScore:
    rank: int
    opportunity: OpportunityScoreResult


def rank_opportunity_scores(
    opportunities: tuple[OpportunityScoreResult, ...],
    *,
    eligible_only: bool = True,
    minimum_score: Decimal = Decimal("0"),
) -> tuple[RankedOpportunityScore, ...]:
    """Return a deterministic cross-item ranking without changing score semantics.

    Ranking happens only after each item has been scored at the same ``as_of`` and
    horizon by the caller. Eligible opportunities are always ordered before
    ineligible diagnostics when ``eligible_only`` is false. Equal scores use
    component scores and finally ``item_id`` as stable tie-breakers.
    """

    if not isinstance(minimum_score, Decimal) or not minimum_score.is_finite():
        raise ContractValidationError("minimum_score must be a finite Decimal.")
    if not Decimal("0") <= minimum_score <= Decimal("100"):
        raise ContractValidationError("minimum_score must satisfy 0 <= value <= 100.")

    item_ids = [opportunity.item_id for opportunity in opportunities]
    if len(item_ids) != len(set(item_ids)):
        raise ContractValidationError("Opportunity rankings require unique item_id values.")

    filtered = tuple(
        opportunity
        for opportunity in opportunities
        if opportunity.score >= minimum_score
        and (opportunity.eligible or not eligible_only)
    )
    ordered = sorted(
        filtered,
        key=lambda opportunity: (
            0 if opportunity.eligible else 1,
            -opportunity.score,
            -opportunity.profitability_score,
            -opportunity.liquidity_score,
            -opportunity.freshness_score,
            -opportunity.data_confidence_score,
            opportunity.item_id,
        ),
    )
    return tuple(
        RankedOpportunityScore(rank=index, opportunity=opportunity)
        for index, opportunity in enumerate(ordered, start=1)
    )
