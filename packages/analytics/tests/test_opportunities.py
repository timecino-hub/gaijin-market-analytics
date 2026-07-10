from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.contracts import AnalysisRequest, MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.opportunities import OpportunityScoreConfig, OpportunityScoreV1
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1

AS_OF = datetime(2026, 6, 29, tzinfo=UTC)


def observation(
    days_back: int,
    *,
    ask: str,
    bid: str,
    ask_count: int | None = None,
    bid_count: int | None = None,
    observed_ask_quantity: int | None = None,
    observed_bid_quantity: int | None = None,
    reviewed: bool = False,
) -> MarketObservation:
    return MarketObservation(
        observed_at=AS_OF - timedelta(days=days_back),
        best_ask=Decimal(ask),
        best_bid=Decimal(bid),
        ask_count=ask_count,
        bid_count=bid_count,
        estimated_volume=None,
        observation_key=f"obs-{days_back}",
        observed_ask_quantity=observed_ask_quantity,
        observed_bid_quantity=observed_bid_quantity,
        quantity_semantics="screenshot_display_quantity" if reviewed else None,
        source_type="screen_review" if reviewed else None,
        review_status="confirmed_with_edits" if reviewed else None,
    )


def analysis_for(
    observations: tuple[MarketObservation, ...],
    *,
    maximum_snapshot_age: timedelta = timedelta(days=2),
):
    request = AnalysisRequest(
        item_id=123,
        horizon=AnalysisHorizon.DAYS_7,
        as_of=AS_OF,
        observations=observations,
        fee_policy=GAIJIN_MARKET_RULES_V1.fee_policy,
        market_rules=GAIJIN_MARKET_RULES_V1,
        maximum_snapshot_age=maximum_snapshot_age,
        minimum_snapshot_count=3,
    )
    return RuleBasedV1().analyze(request)


def profitable_reviewed_observations() -> tuple[MarketObservation, ...]:
    return (
        observation(
            7,
            ask="11",
            bid="10",
            observed_ask_quantity=20,
            observed_bid_quantity=20,
            reviewed=True,
        ),
        observation(
            3,
            ask="11",
            bid="10",
            observed_ask_quantity=20,
            observed_bid_quantity=20,
            reviewed=True,
        ),
        observation(
            0,
            ask="5",
            bid="4.5",
            observed_ask_quantity=25,
            observed_bid_quantity=15,
            reviewed=True,
        ),
    )


def test_opportunity_score_uses_reviewed_quantities_and_is_explainable() -> None:
    observations = profitable_reviewed_observations()
    result = OpportunityScoreV1().score(
        analysis=analysis_for(observations),
        observations=observations,
        maximum_snapshot_age=timedelta(days=2),
        minimum_snapshot_count=3,
    )

    assert result.eligible is True
    assert result.liquidity_source == "reviewed_screenshot_quantity"
    assert result.quantity_observation_count == 3
    assert result.latest_observed_bid_quantity == 15
    assert result.latest_observed_ask_quantity == 25
    assert result.profitability_score == Decimal("100.00")
    assert result.liquidity_score == Decimal("100.00")
    assert result.stability_score == Decimal("100.00")
    assert result.freshness_score == Decimal("100.00")
    assert Decimal("0") < result.risk_penalty < Decimal("15")
    assert Decimal("80") < result.score <= Decimal("100")
    assert "reviewed_screenshot_liquidity_proxy" in result.explanation_codes
    assert "eligible_positive_after_fee_opportunity" in result.explanation_codes


def test_opportunity_score_falls_back_to_snapshot_counts() -> None:
    observations = (
        observation(7, ask="11", bid="10", ask_count=20, bid_count=20),
        observation(3, ask="11", bid="10", ask_count=20, bid_count=20),
        observation(0, ask="5", bid="4.5", ask_count=20, bid_count=20),
    )
    result = OpportunityScoreV1().score(
        analysis=analysis_for(observations),
        observations=observations,
        maximum_snapshot_age=timedelta(days=2),
        minimum_snapshot_count=3,
    )

    assert result.liquidity_source == "snapshot_counts"
    assert result.quantity_observation_count == 3
    assert result.latest_observed_bid_quantity is None
    assert result.latest_observed_ask_quantity is None
    assert result.liquidity_score == Decimal("100.00")
    assert "snapshot_count_liquidity_proxy" in result.explanation_codes


def test_non_positive_after_fee_result_is_not_eligible() -> None:
    observations = (
        observation(7, ask="10", bid="9", ask_count=20, bid_count=20),
        observation(3, ask="10", bid="9", ask_count=20, bid_count=20),
        observation(0, ask="12", bid="11", ask_count=20, bid_count=20),
    )
    result = OpportunityScoreV1().score(
        analysis=analysis_for(observations),
        observations=observations,
        maximum_snapshot_age=timedelta(days=2),
        minimum_snapshot_count=3,
    )

    assert result.eligible is False
    assert result.profitability_score == Decimal("0.00")
    assert "non_positive_or_unavailable_net_profit" in result.explanation_codes
    assert "not_eligible_for_opportunity_ranking" in result.explanation_codes


def test_stale_latest_observation_has_zero_freshness() -> None:
    observations = (
        observation(
            7,
            ask="11",
            bid="10",
            observed_ask_quantity=20,
            observed_bid_quantity=20,
            reviewed=True,
        ),
        observation(
            5,
            ask="11",
            bid="10",
            observed_ask_quantity=20,
            observed_bid_quantity=20,
            reviewed=True,
        ),
        observation(
            2,
            ask="5",
            bid="4.5",
            observed_ask_quantity=25,
            observed_bid_quantity=15,
            reviewed=True,
        ),
    )
    analysis = analysis_for(observations, maximum_snapshot_age=timedelta(hours=12))
    result = OpportunityScoreV1().score(
        analysis=analysis,
        observations=observations,
        maximum_snapshot_age=timedelta(hours=12),
        minimum_snapshot_count=3,
    )

    assert result.freshness_score == Decimal("0.00")
    assert result.eligible is False
    assert "latest_observation_stale_or_missing" in result.explanation_codes


def test_scorer_rejects_observations_different_from_analysis_window() -> None:
    observations = profitable_reviewed_observations()
    analysis = analysis_for(observations)

    with pytest.raises(ContractValidationError, match="match the observations"):
        OpportunityScoreV1().score(
            analysis=analysis,
            observations=observations[:-1],
            maximum_snapshot_age=timedelta(days=2),
            minimum_snapshot_count=3,
        )



def test_opportunity_without_liquidity_evidence_is_not_eligible() -> None:
    observations = (
        observation(7, ask="11", bid="10"),
        observation(3, ask="11", bid="10"),
        observation(0, ask="5", bid="4.5"),
    )
    result = OpportunityScoreV1().score(
        analysis=analysis_for(observations),
        observations=observations,
        maximum_snapshot_age=timedelta(days=2),
        minimum_snapshot_count=3,
    )

    assert result.liquidity_source == "unavailable"
    assert result.eligible is False
    assert "liquidity_proxy_unavailable" in result.explanation_codes
    assert "not_eligible_for_opportunity_ranking" in result.explanation_codes


def test_scorer_rejects_observation_bounds_different_from_analysis_window() -> None:
    observations = profitable_reviewed_observations()
    analysis = analysis_for(observations)
    replacement = observation(6, ask="11", bid="10", ask_count=20, bid_count=20)

    with pytest.raises(ContractValidationError, match="bounds must match"):
        OpportunityScoreV1().score(
            analysis=analysis,
            observations=(replacement, observations[1], observations[2]),
            maximum_snapshot_age=timedelta(days=2),
            minimum_snapshot_count=3,
        )

def test_invalid_opportunity_config_is_rejected() -> None:
    with pytest.raises(ContractValidationError, match="sum to 100"):
        OpportunityScoreConfig(profitability_weight=Decimal("34"))

    with pytest.raises(ContractValidationError, match="finite Decimal"):
        OpportunityScoreConfig(full_profitability_roi=0.20)  # type: ignore[arg-type]
