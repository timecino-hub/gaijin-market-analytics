from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from gaijin_market_analytics.exceptions import InvalidFeeRateError, InvalidPriceError
from gaijin_market_analytics.fees import (
    GAIJIN_MARKET_FEE_POLICY_V1,
    FeePolicy,
    calculate_break_even_sell_price,
    calculate_fee_amount,
    calculate_gross_profit,
    calculate_net_profit,
    calculate_net_roi,
    calculate_sale_proceeds,
    floor_to_quantum,
)


def test_fixed_policy_metadata_and_immutability() -> None:
    policy = GAIJIN_MARKET_FEE_POLICY_V1

    assert policy.name == "gaijin_market"
    assert policy.version == "1.0.0"
    assert policy.nominal_rate == Decimal("0.15")
    assert policy.currency_quantum == Decimal("0.01")
    assert policy.proceeds_rounding == "seller_proceeds_round_down"
    with pytest.raises(FrozenInstanceError):
        policy.nominal_rate = Decimal("0.10")  # type: ignore[misc]


def test_floor_to_quantum_rounds_non_negative_values_down() -> None:
    assert floor_to_quantum(Decimal("1.6915"), Decimal("0.01")) == Decimal("1.69")
    assert floor_to_quantum(Decimal("1.7000"), Decimal("0.01")) == Decimal("1.70")
    assert floor_to_quantum(Decimal("0"), Decimal("0.01")) == Decimal("0.00")


def test_floor_to_quantum_rejects_invalid_inputs() -> None:
    with pytest.raises(InvalidPriceError):
        floor_to_quantum(Decimal("-0.01"), Decimal("0.01"))
    with pytest.raises(InvalidPriceError):
        floor_to_quantum(Decimal("1.00"), Decimal("0"))


def test_sale_proceeds_and_fee_amount_use_gaijin_policy_rounding() -> None:
    assert calculate_sale_proceeds(Decimal("1.99")) == Decimal("1.69")
    assert calculate_fee_amount(Decimal("1.99")) == Decimal("0.30")
    assert calculate_sale_proceeds(Decimal("2.00")) == Decimal("1.70")


def test_gross_profit_is_sell_minus_buy() -> None:
    assert calculate_gross_profit(Decimal("8.50"), Decimal("10.00")) == Decimal("1.50")


def test_net_profit_uses_discrete_seller_proceeds() -> None:
    assert calculate_net_profit(Decimal("1.50"), Decimal("1.99")) == Decimal("0.19")


def test_net_roi_uses_discrete_net_profit_over_buy_price() -> None:
    assert calculate_net_roi(Decimal("1.50"), Decimal("1.99")) == (
        Decimal("0.19") / Decimal("1.50")
    )


def test_discrete_break_even_sell_price_for_known_examples() -> None:
    assert calculate_break_even_sell_price(Decimal("1.69")) == Decimal("1.99")
    assert calculate_sale_proceeds(Decimal("1.99")) == Decimal("1.69")
    assert calculate_sale_proceeds(Decimal("1.98")) == Decimal("1.68")
    assert calculate_break_even_sell_price(Decimal("1.70")) == Decimal("2.00")
    assert calculate_sale_proceeds(Decimal("2.00")) == Decimal("1.70")
    assert calculate_sale_proceeds(Decimal("1.99")) == Decimal("1.69")


def test_break_even_handles_non_quantum_buy_price() -> None:
    result = calculate_break_even_sell_price(Decimal("1.695"))

    assert result == Decimal("2.00")
    assert calculate_sale_proceeds(result) >= Decimal("1.695")
    assert calculate_sale_proceeds(result - GAIJIN_MARKET_FEE_POLICY_V1.currency_quantum) < Decimal(
        "1.695"
    )


@pytest.mark.parametrize(
    ("buy_price", "expected"),
    [
        (Decimal("0.01"), Decimal("0.02")),
        (Decimal("12.34"), Decimal("14.52")),
        (Decimal("1699.99"), Decimal("1999.99")),
        (Decimal("1700.00"), Decimal("2000.00")),
        (Decimal("1700.01"), Decimal("2000.02")),
    ],
)
def test_break_even_uses_bounded_correction_not_quantum_enumeration(
    buy_price: Decimal,
    expected: Decimal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaijin_market_analytics import fees

    calls = 0
    original = fees.calculate_sale_proceeds

    def counting_sale_proceeds(
        sell_price: Decimal,
        policy: FeePolicy = GAIJIN_MARKET_FEE_POLICY_V1,
    ) -> Decimal:
        nonlocal calls
        calls += 1
        return original(sell_price, policy)

    monkeypatch.setattr(fees, "calculate_sale_proceeds", counting_sale_proceeds)

    assert fees.calculate_break_even_sell_price(buy_price) == expected
    assert calls <= 6


def test_same_inputs_are_deterministic() -> None:
    first = calculate_net_profit(Decimal("1.69"), Decimal("1.99"))
    second = calculate_net_profit(Decimal("1.69"), Decimal("1.99"))

    assert first == second


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1")])
def test_non_positive_prices_are_rejected(price: Decimal) -> None:
    with pytest.raises(InvalidPriceError):
        calculate_gross_profit(price, Decimal("10"))


def test_float_money_values_are_rejected() -> None:
    with pytest.raises(InvalidPriceError):
        calculate_sale_proceeds(1.99)  # type: ignore[arg-type]


def test_invalid_policy_is_rejected() -> None:
    with pytest.raises(InvalidFeeRateError):
        calculate_sale_proceeds(
            Decimal("1.99"),
            FeePolicy(
                name="bad",
                version="1",
                nominal_rate=Decimal("1"),
                currency_quantum=Decimal("0.01"),
                proceeds_rounding="seller_proceeds_round_down",
            ),
        )
