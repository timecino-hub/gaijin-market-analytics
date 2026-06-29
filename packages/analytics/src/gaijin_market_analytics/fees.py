from decimal import Decimal

from gaijin_market_analytics.exceptions import InvalidFeeRateError, InvalidPriceError


def calculate_sale_proceeds(sell_price: Decimal, fee_rate: Decimal) -> Decimal:
    """Return sell_price * (1 - fee_rate), before any quantization."""

    _validate_positive_price(sell_price, "sell_price")
    _validate_fee_rate(fee_rate)
    return sell_price * (Decimal("1") - fee_rate)


def calculate_gross_profit(buy_price: Decimal, sell_price: Decimal) -> Decimal:
    """Return sell_price - buy_price, before fees."""

    _validate_positive_price(buy_price, "buy_price")
    _validate_positive_price(sell_price, "sell_price")
    return sell_price - buy_price


def calculate_net_profit(buy_price: Decimal, sell_price: Decimal, fee_rate: Decimal) -> Decimal:
    """Return sell_price * (1 - fee_rate) - buy_price."""

    _validate_positive_price(buy_price, "buy_price")
    return calculate_sale_proceeds(sell_price, fee_rate) - buy_price


def calculate_net_roi(buy_price: Decimal, sell_price: Decimal, fee_rate: Decimal) -> Decimal:
    """Return net_profit / buy_price."""

    _validate_positive_price(buy_price, "buy_price")
    return calculate_net_profit(buy_price, sell_price, fee_rate) / buy_price


def calculate_break_even_sell_price(buy_price: Decimal, fee_rate: Decimal) -> Decimal:
    """Return buy_price / (1 - fee_rate), the sell price where net profit is zero."""

    _validate_positive_price(buy_price, "buy_price")
    _validate_fee_rate(fee_rate)
    return buy_price / (Decimal("1") - fee_rate)


def _validate_positive_price(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise InvalidPriceError(f"{field_name} must be a finite Decimal greater than 0.")


def _validate_fee_rate(fee_rate: Decimal) -> None:
    if (
        not isinstance(fee_rate, Decimal)
        or not fee_rate.is_finite()
        or fee_rate < Decimal("0")
        or fee_rate >= Decimal("1")
    ):
        raise InvalidFeeRateError("fee_rate must satisfy 0 <= fee < 1.")
