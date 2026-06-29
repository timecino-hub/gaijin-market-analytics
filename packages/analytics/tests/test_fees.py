from decimal import Decimal

import pytest

from gaijin_market_analytics.exceptions import InvalidFeeRateError, InvalidPriceError
from gaijin_market_analytics.fees import (
    calculate_break_even_sell_price,
    calculate_gross_profit,
    calculate_net_profit,
    calculate_net_roi,
    calculate_sale_proceeds,
)


def test_sale_proceeds_preserve_decimal_precision_without_quantizing() -> None:
    assert calculate_sale_proceeds(Decimal("10.005"), Decimal("0.075")) == Decimal("9.254625")


def test_fee_zero_returns_full_sale_price() -> None:
    assert calculate_sale_proceeds(Decimal("12.34"), Decimal("0")) == Decimal("12.34")


@pytest.mark.parametrize("fee_rate", [Decimal("-0.01"), Decimal("1"), Decimal("1.1")])
def test_invalid_fee_rate_is_rejected(fee_rate: Decimal) -> None:
    with pytest.raises(InvalidFeeRateError):
        calculate_sale_proceeds(Decimal("10"), fee_rate)


def test_gross_profit_is_sell_minus_buy() -> None:
    assert calculate_gross_profit(Decimal("8.50"), Decimal("10.00")) == Decimal("1.50")


def test_net_profit_subtracts_marketplace_fee() -> None:
    assert calculate_net_profit(Decimal("8.50"), Decimal("10.00"), Decimal("0.10")) == Decimal("0.50")


def test_net_roi_uses_net_profit_over_buy_price() -> None:
    assert calculate_net_roi(Decimal("8.50"), Decimal("10.00"), Decimal("0.10")) == (
        Decimal("0.50") / Decimal("8.50")
    )


def test_break_even_sell_price_uses_fee_adjusted_denominator() -> None:
    assert calculate_break_even_sell_price(Decimal("9"), Decimal("0.10")) == Decimal("1E+1")


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1")])
def test_non_positive_prices_are_rejected(price: Decimal) -> None:
    with pytest.raises(InvalidPriceError):
        calculate_gross_profit(price, Decimal("10"))
