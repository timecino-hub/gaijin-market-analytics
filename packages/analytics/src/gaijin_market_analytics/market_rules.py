from dataclasses import dataclass
from decimal import Decimal

from gaijin_market_analytics.exceptions import InvalidFeeRateError, InvalidPriceError
from gaijin_market_analytics.fees import (
    GAIJIN_MARKET_FEE_POLICY_V1,
    FeePolicy,
    calculate_sale_proceeds,
)


@dataclass(frozen=True, slots=True)
class MarketRules:
    name: str
    version: str
    maximum_listing_price: Decimal
    fee_policy: FeePolicy

    def __post_init__(self) -> None:
        if not isinstance(self.fee_policy, FeePolicy):
            raise InvalidFeeRateError("fee_policy must be a FeePolicy value.")
        if (
            not isinstance(self.maximum_listing_price, Decimal)
            or not self.maximum_listing_price.is_finite()
        ):
            raise InvalidPriceError("maximum_listing_price must be a finite Decimal.")
        if self.maximum_listing_price <= Decimal("0"):
            raise InvalidPriceError("maximum_listing_price must be greater than 0.")
        if self.currency_quantum <= Decimal("0"):
            raise InvalidPriceError("currency_quantum must be greater than 0.")
        units = self.maximum_listing_price / self.currency_quantum
        if units != units.to_integral_value():
            raise InvalidPriceError("maximum_listing_price must align to currency_quantum.")

    @property
    def currency_quantum(self) -> Decimal:
        return self.fee_policy.currency_quantum

    @property
    def maximum_sale_proceeds(self) -> Decimal:
        return calculate_sale_proceeds(self.maximum_listing_price, self.fee_policy)


GAIJIN_MARKET_RULES_V1 = MarketRules(
    name="gaijin_market",
    version="1.0.0",
    maximum_listing_price=Decimal("2000.00"),
    fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
)

if GAIJIN_MARKET_RULES_V1.maximum_listing_price != Decimal("2000.00"):
    raise InvalidPriceError("GAIJIN_MARKET_RULES_V1 maximum listing price changed.")
if GAIJIN_MARKET_RULES_V1.maximum_sale_proceeds != Decimal("1700.00"):
    raise InvalidPriceError("GAIJIN_MARKET_RULES_V1 maximum sale proceeds changed.")


def is_valid_market_price(value: Decimal | None, market_rules: MarketRules) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and Decimal("0") < value <= market_rules.maximum_listing_price
    )
