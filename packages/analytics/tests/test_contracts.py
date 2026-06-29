from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult, MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon, AnalysisStatus, ReasonCode
from gaijin_market_analytics.exceptions import ContractValidationError, InvalidDecimalError
from gaijin_market_analytics.fees import GAIJIN_MARKET_FEE_POLICY_V1, FeePolicy
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1


def aware(hour: int = 0, tz=UTC) -> datetime:
    return datetime(2026, 6, 29, hour, tzinfo=tz)


def observation(
    observed_at: datetime,
    *,
    ask: Decimal | None = Decimal("10"),
    bid: Decimal | None = Decimal("9"),
    key: str | None = None,
) -> MarketObservation:
    return MarketObservation(
        observed_at=observed_at,
        best_ask=ask,
        best_bid=bid,
        ask_count=2,
        bid_count=3,
        estimated_volume=Decimal("5"),
        observation_key=key,
    )


def test_request_normalizes_timezone_aware_datetimes_to_utc() -> None:
    tz = timezone(timedelta(hours=8))
    request = AnalysisRequest(
        item_id=1,
        horizon=AnalysisHorizon.DAYS_7,
        as_of=aware(8, tz),
        observations=(observation(aware(7, tz)),),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        maximum_snapshot_age=timedelta(days=1),
        minimum_snapshot_count=1,
    )

    assert request.as_of == datetime(2026, 6, 29, 0, tzinfo=UTC)
    assert request.observations[0].observed_at == datetime(2026, 6, 28, 23, tzinfo=UTC)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ContractValidationError):
        observation(datetime(2026, 6, 29))


def test_future_observation_is_rejected() -> None:
    with pytest.raises(ContractValidationError):
        AnalysisRequest(
            item_id=1,
            horizon=AnalysisHorizon.DAYS_7,
            as_of=aware(0),
            observations=(observation(aware(1)),),
            fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
            market_rules=GAIJIN_MARKET_RULES_V1,
            maximum_snapshot_age=timedelta(days=1),
            minimum_snapshot_count=1,
        )


def test_invalid_item_id_and_request_parameters_are_rejected() -> None:
    with pytest.raises(ContractValidationError):
        AnalysisRequest(
            item_id=0,
            horizon=AnalysisHorizon.DAYS_7,
            as_of=aware(0),
            observations=(),
            fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
            market_rules=GAIJIN_MARKET_RULES_V1,
            maximum_snapshot_age=timedelta(days=1),
            minimum_snapshot_count=1,
        )


@pytest.mark.parametrize("fee", [Decimal("-0.01"), Decimal("1")])
def test_invalid_fee_policy_is_rejected_at_contract_boundary(fee: Decimal) -> None:
    with pytest.raises(ContractValidationError):
        AnalysisRequest(
            item_id=1,
            horizon=AnalysisHorizon.DAYS_7,
            as_of=aware(0),
            observations=(),
            fee_policy=FeePolicy(
                name="bad",
                version="1",
                nominal_rate=fee,
                currency_quantum=Decimal("0.01"),
                proceeds_rounding="seller_proceeds_round_down",
            ),
            market_rules=GAIJIN_MARKET_RULES_V1,
            maximum_snapshot_age=timedelta(days=1),
            minimum_snapshot_count=1,
        )


def test_float_decimal_fields_are_rejected() -> None:
    with pytest.raises(InvalidDecimalError):
        MarketObservation(
            observed_at=aware(0),
            best_ask=10.0,  # type: ignore[arg-type]
            best_bid=Decimal("9"),
            ask_count=1,
            bid_count=1,
            estimated_volume=None,
        )


def test_observations_are_sorted_without_mutating_callers_collection() -> None:
    observations = [
        observation(aware(0), ask=Decimal("12"), bid=Decimal("11"), key="b"),
        observation(aware(0), ask=Decimal("10"), bid=Decimal("9"), key="a"),
    ]
    original = list(observations)

    request = AnalysisRequest(
        item_id=1,
        horizon=AnalysisHorizon.DAYS_7,
        as_of=aware(0),
        observations=tuple(observations),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        maximum_snapshot_age=timedelta(days=1),
        minimum_snapshot_count=1,
    )

    assert observations == original
    assert [item.observation_key for item in request.observations] == ["a", "b"]


def test_output_object_is_immutable_and_preserves_decimal_fields() -> None:
    result = AnalysisResult(
        item_id=1,
        horizon=AnalysisHorizon.DAYS_7,
        as_of=aware(0),
        status=AnalysisStatus.OK,
        strategy_name="rule_based",
        strategy_version="1.0.0",
        feature_version="market_features_v1",
        observation_count=1,
        first_observation_at=aware(0),
        last_observation_at=aware(0),
        current_ask=Decimal("10"),
        current_bid=Decimal("9"),
        reference_sell_price=Decimal("9"),
        sale_proceeds=Decimal("7.65"),
        fee_amount=Decimal("1.35"),
        gross_profit=Decimal("-1"),
        net_profit=Decimal("-1.9"),
        net_roi=Decimal("-0.19"),
        break_even_sell_price=Decimal("11.11111111111111111111111111"),
        break_even_reachable=True,
        maximum_listing_price=Decimal("2000.00"),
        maximum_sale_proceeds=Decimal("1700.00"),
        maximum_net_profit=Decimal("1690.00"),
        spread_absolute=Decimal("1"),
        spread_ratio=Decimal("0.1111111111111111111111111111"),
        median_bid=Decimal("9"),
        median_ask=Decimal("10"),
        price_volatility=Decimal("0"),
        liquidity_score=Decimal("25"),
        risk_score=Decimal("10"),
        confidence_score=Decimal("70"),
        fee_policy_name="gaijin_market",
        fee_policy_version="1.0.0",
        nominal_fee_rate=Decimal("0.15"),
        currency_quantum=Decimal("0.01"),
        proceeds_rounding="seller_proceeds_round_down",
        market_rules_name="gaijin_market",
        market_rules_version="1.0.0",
        reason_codes=(ReasonCode.ANALYSIS_COMPLETED,),
    )

    assert isinstance(result.net_roi, Decimal)
    with pytest.raises(FrozenInstanceError):
        result.observation_count = 2  # type: ignore[misc]
