from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, localcontext

import pytest

from gaijin_market_analytics.general_strategy import (
    GeneralStrategyError,
    GeneralStrategyInputs,
    evaluate_general_strategy,
    take_profit_offer,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_policy_is_user_selected_candidate_with_no_claimed_fills():
    result = evaluate_general_strategy(GeneralStrategyInputs(), as_of=NOW)
    assert result["policy"]["buy_cost_cap"] == "0.55"
    assert result["policy"]["lower_target_after_days"] == 150
    assert result["policy"]["review_after_days"] == 180
    assert result["policy"]["force_liquidation"] is False
    assert result["research"]["optimality_proven"] is False
    assert result["research"]["fill_claim"] is False
    assert result["automated_trading"] is False


def test_complete_observation_and_quote_are_independent_conditions():
    values = GeneralStrategyInputs(baseline=Decimal(100), observation_price=Decimal(70), quoted_cost=Decimal(55), event_closes_on=date(2026, 10, 3))
    assert evaluate_general_strategy(values, as_of=datetime(2026, 9, 30, 23, 59, tzinfo=UTC))["entry"]["status"] == "OBSERVATION_NOT_COMPLETE"
    result = evaluate_general_strategy(values, as_of=NOW)
    assert result["entry"]["status"] == "PRICE_CONDITIONS_SATISFIED"
    assert result["entry"]["observation_on"] == "2026-09-30"
    assert result["entry"]["quote_check_on"] == "2026-10-01"


def test_price_observation_does_not_allow_a_cost_cap_breach():
    values = GeneralStrategyInputs(baseline=Decimal(100), observation_price=Decimal(40), quoted_cost=Decimal("55.01"), event_closes_on=date(2026, 10, 3))
    assert evaluate_general_strategy(values, as_of=NOW)["entry"]["status"] == "COST_CAP_EXCEEDED"


def test_high_precision_cost_cap_is_not_rounded_up_to_a_bad_quote():
    values = GeneralStrategyInputs(
        baseline=Decimal("999999999999.9999999999999998"),
        observation_price=Decimal("1"),
        quoted_cost=Decimal("549999999999.9999999999999999"),
        event_closes_on=date(2026, 10, 3),
    )
    assert evaluate_general_strategy(values, as_of=NOW)["entry"]["status"] == "COST_CAP_EXCEEDED"


def test_early_entry_does_not_require_future_post_event_gate():
    values = GeneralStrategyInputs(baseline=Decimal(100), observation_price=Decimal(40), quoted_cost=Decimal(50), event_closes_on=date(2026, 10, 3))
    assert evaluate_general_strategy(values, as_of=NOW)["entry"]["status"] == "PRICE_CONDITIONS_SATISFIED"
    assert evaluate_general_strategy(values, as_of=NOW + timedelta(days=1))["entry"]["status"] == "ENTRY_WINDOW_CLOSED"


@pytest.mark.parametrize("target", [Decimal("0.40"), Decimal("0.50")])
def test_offer_rounding_protects_net_target(target):
    cost = Decimal("47.1645792")
    price = take_profit_offer(cost, target, Decimal("0.15"))
    receipt = (price * Decimal("0.85")).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    assert receipt / cost - 1 >= target


@pytest.mark.parametrize("age,status,target", [(149, "TARGET50", "0.50"), (150, "TARGET40", "0.40"), (179, "TARGET40", "0.40"), (180, "REVIEW_REQUIRED", None)])
def test_exit_clock_uses_actual_purchase_instant(age, status, target):
    values = GeneralStrategyInputs(entry_cost=Decimal("58.0837752"), bought_at=NOW - timedelta(days=age))
    result = evaluate_general_strategy(values, as_of=NOW)["exit"]
    assert result["status"] == status
    assert result["current_target"] == target
    assert result["force_liquidation"] is False
    if age == 180:
        assert result["planned_offer"] is None


def test_historical_example_uses_price_conditions_not_real_orders():
    values = GeneralStrategyInputs(entry_cost=Decimal("47.1645792"), bought_at=datetime(2025, 1, 12, tzinfo=UTC))
    result = evaluate_general_strategy(values, as_of=datetime(2025, 5, 20, tzinfo=UTC))
    assert result["exit"]["preferred_offer"] == "83.24"
    assert result["exit"]["minimum_offer"] == "77.70"
    assert all(example["fill_claim"] is False for example in result["examples"])


@pytest.mark.parametrize("values", [
    GeneralStrategyInputs(baseline=Decimal("NaN")),
    GeneralStrategyInputs(entry_cost=Decimal(5)),
    GeneralStrategyInputs(bought_at=NOW),
    GeneralStrategyInputs(fee_rate=Decimal(1)),
    GeneralStrategyInputs(entry_cost=Decimal(5), bought_at=NOW + timedelta(days=1)),
    GeneralStrategyInputs(event_closes_on=date(1, 1, 1)),
])
def test_bad_or_incomplete_inputs_are_not_replaced_by_defaults(values):
    with pytest.raises(GeneralStrategyError):
        evaluate_general_strategy(values, as_of=NOW)


def test_timezone_and_overflow_are_explicit_errors():
    with pytest.raises(GeneralStrategyError):
        evaluate_general_strategy(GeneralStrategyInputs(), as_of=NOW.replace(tzinfo=None))
    late = datetime(9999, 12, 30, tzinfo=UTC)
    with pytest.raises(GeneralStrategyError):
        evaluate_general_strategy(GeneralStrategyInputs(entry_cost=Decimal(5), bought_at=late), as_of=late)


def test_high_precision_fee_does_not_cause_quantize_failure():
    cost, fee = Decimal("1000000000000"), Decimal("0.999999999999999999")
    offer = take_profit_offer(cost, Decimal("0.50"), fee)
    with localcontext() as context:
        context.prec = 64
        assert offer * (1 - fee) >= cost * Decimal("1.50")
