from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from gaijin_market_analytics.exceptions import InvalidPriceError
from gaijin_market_analytics.fees import calculate_sale_proceeds
from gaijin_market_analytics.market_rules import (
    GAIJIN_MARKET_RULES_V1,
    MarketRules,
    is_valid_market_price,
)


def test_gaijin_market_rules_v1_metadata_and_derived_values() -> None:
    rules = GAIJIN_MARKET_RULES_V1

    assert rules.name == "gaijin_market"
    assert rules.version == "1.0.0"
    assert rules.maximum_listing_price == Decimal("2000.00")
    assert rules.currency_quantum == Decimal("0.01")
    assert rules.maximum_sale_proceeds == Decimal("1700.00")
    assert calculate_sale_proceeds(rules.maximum_listing_price, rules.fee_policy) == Decimal(
        "1700.00"
    )
    with pytest.raises(FrozenInstanceError):
        rules.maximum_listing_price = Decimal("1999.99")  # type: ignore[misc]


@pytest.mark.parametrize("price", [Decimal("2000.00"), Decimal("0.01")])
def test_market_price_at_or_below_cap_is_valid(price: Decimal) -> None:
    assert is_valid_market_price(price, GAIJIN_MARKET_RULES_V1)


@pytest.mark.parametrize(
    "price",
    [Decimal("2000.01"), Decimal("0"), Decimal("-0.01"), 1.0],
)
def test_market_price_outside_rules_is_invalid(price: object) -> None:
    assert not is_valid_market_price(price, GAIJIN_MARKET_RULES_V1)  # type: ignore[arg-type]


def test_market_rules_reject_invalid_maximum_listing_price() -> None:
    with pytest.raises(InvalidPriceError):
        MarketRules(
            name="bad",
            version="1",
            maximum_listing_price=2000.00,  # type: ignore[arg-type]
            fee_policy=GAIJIN_MARKET_RULES_V1.fee_policy,
        )
    with pytest.raises(InvalidPriceError):
        MarketRules(
            name="bad",
            version="1",
            maximum_listing_price=Decimal("0"),
            fee_policy=GAIJIN_MARKET_RULES_V1.fee_policy,
        )
    with pytest.raises(InvalidPriceError):
        MarketRules(
            name="bad",
            version="1",
            maximum_listing_price=Decimal("2000.001"),
            fee_policy=GAIJIN_MARKET_RULES_V1.fee_policy,
        )
