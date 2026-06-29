from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN

from gaijin_market_analytics.exceptions import InvalidFeeRateError, InvalidPriceError


@dataclass(frozen=True, slots=True)
class FeePolicy:
    name: str
    version: str
    nominal_rate: Decimal
    currency_quantum: Decimal
    proceeds_rounding: str


GAIJIN_MARKET_FEE_POLICY_V1 = FeePolicy(
    name="gaijin_market",
    version="1.0.0",
    nominal_rate=Decimal("0.15"),
    currency_quantum=Decimal("0.01"),
    proceeds_rounding="seller_proceeds_round_down",
)


def floor_to_quantum(value: Decimal, quantum: Decimal) -> Decimal:
    """Round non-negative Decimal money down to the settlement quantum.

    For Gaijin Market seller settlement, this truncates the seller proceeds to
    the largest valid non-negative currency amount not greater than ``value``.
    It is not used on net profit, which may be negative.
    """

    _validate_non_negative_decimal(value, "value")
    _validate_quantum(quantum)
    units = (value / quantum).to_integral_value(rounding=ROUND_DOWN)
    return (units * quantum).quantize(quantum)


def calculate_sale_proceeds(
    sell_price: Decimal,
    policy: FeePolicy = GAIJIN_MARKET_FEE_POLICY_V1,
) -> Decimal:
    """Return seller proceeds after nominal fee and policy rounding."""

    _validate_positive_price(sell_price, "sell_price")
    _validate_policy(policy)
    raw_sale_proceeds = sell_price * (Decimal("1") - policy.nominal_rate)
    return floor_to_quantum(raw_sale_proceeds, policy.currency_quantum)


def calculate_fee_amount(
    sell_price: Decimal,
    policy: FeePolicy = GAIJIN_MARKET_FEE_POLICY_V1,
) -> Decimal:
    """Return listed sell price minus rounded seller proceeds."""

    _validate_positive_price(sell_price, "sell_price")
    return sell_price - calculate_sale_proceeds(sell_price, policy)


def calculate_gross_profit(buy_price: Decimal, sell_price: Decimal) -> Decimal:
    """Return sell_price - buy_price, before fees."""

    _validate_positive_price(buy_price, "buy_price")
    _validate_positive_price(sell_price, "sell_price")
    return sell_price - buy_price


def calculate_net_profit(
    buy_price: Decimal,
    sell_price: Decimal,
    policy: FeePolicy = GAIJIN_MARKET_FEE_POLICY_V1,
) -> Decimal:
    """Return rounded seller proceeds minus buy_price."""

    _validate_positive_price(buy_price, "buy_price")
    return calculate_sale_proceeds(sell_price, policy) - buy_price


def calculate_net_roi(
    buy_price: Decimal,
    sell_price: Decimal,
    policy: FeePolicy = GAIJIN_MARKET_FEE_POLICY_V1,
) -> Decimal:
    """Return net_profit / buy_price."""

    _validate_positive_price(buy_price, "buy_price")
    return calculate_net_profit(buy_price, sell_price, policy) / buy_price


def calculate_break_even_sell_price(
    buy_price: Decimal,
    policy: FeePolicy = GAIJIN_MARKET_FEE_POLICY_V1,
) -> Decimal:
    """Return the smallest valid listed price whose rounded proceeds cover buy_price."""

    _validate_positive_price(buy_price, "buy_price")
    _validate_policy(policy)
    required_proceeds = ceil_to_quantum(buy_price, policy.currency_quantum)
    candidate = ceil_to_quantum(
        required_proceeds / (Decimal("1") - policy.nominal_rate),
        policy.currency_quantum,
    )
    for _ in range(4):
        previous = candidate - policy.currency_quantum
        if previous <= Decimal("0"):
            break
        if calculate_sale_proceeds(previous, policy) >= buy_price:
            candidate = previous
            continue
        break
    if calculate_sale_proceeds(candidate, policy) < buy_price:
        raise InvalidPriceError("Unable to find a discrete break-even sell price.")
    return candidate


def ceil_to_quantum(value: Decimal, quantum: Decimal) -> Decimal:
    _validate_non_negative_decimal(value, "value")
    _validate_quantum(quantum)
    units = (value / quantum).to_integral_value(rounding=ROUND_CEILING)
    return (units * quantum).quantize(quantum)


def _validate_positive_price(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise InvalidPriceError(f"{field_name} must be a finite Decimal greater than 0.")


def _validate_non_negative_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < Decimal("0"):
        raise InvalidPriceError(f"{field_name} must be a finite non-negative Decimal.")


def _validate_quantum(quantum: Decimal) -> None:
    if not isinstance(quantum, Decimal) or not quantum.is_finite() or quantum <= Decimal("0"):
        raise InvalidPriceError("quantum must be a finite Decimal greater than 0.")


def _validate_policy(policy: FeePolicy) -> None:
    if not isinstance(policy, FeePolicy):
        raise InvalidFeeRateError("fee policy must be a FeePolicy value.")
    _validate_nominal_rate(policy.nominal_rate)
    _validate_quantum(policy.currency_quantum)
    if policy.proceeds_rounding != "seller_proceeds_round_down":
        raise InvalidFeeRateError("unsupported fee policy proceeds rounding.")


def _validate_nominal_rate(nominal_rate: Decimal) -> None:
    if (
        not isinstance(nominal_rate, Decimal)
        or not nominal_rate.is_finite()
        or nominal_rate < Decimal("0")
        or nominal_rate >= Decimal("1")
    ):
        raise InvalidFeeRateError("nominal fee rate must satisfy 0 <= fee < 1.")
