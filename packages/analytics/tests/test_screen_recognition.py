from decimal import Decimal

import pytest

from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.fees import GAIJIN_MARKET_FEE_POLICY_V1
from gaijin_market_analytics.screen_recognition import (
    AggregationType,
    ScreenOrderBookSnapshot,
    ScreenRecognitionError,
    parse_order_book_level,
    parse_screen_order_book_text,
    seller_proceeds_after_confirmed_fee,
    simulate_immediate_buy,
    simulate_immediate_sell,
    single_unit_immediate_buy_reference,
    single_unit_immediate_sell_reference,
)


REFERENCE_SCREEN_TEXT = """
正在购买: 108 从 65.00 中 更低
数量 价格 (GJN)
1 65.00
1 61.00
1 60.32
1 1.44
104 0.11-

正在出售: 96 为 78.00 中 更高
数量 价格 (GJN)
1 78.00
1 78.99
2 80.00
1 85.80
91 89.00+
"""


def test_reference_screen_text_parses_bid_ask_totals_and_aggregate_ask() -> None:
    snapshot = parse_screen_order_book_text(REFERENCE_SCREEN_TEXT)

    assert snapshot.best_bid == Decimal("65.00")
    assert snapshot.best_ask == Decimal("78.00")
    assert snapshot.total_bid_quantity == 108
    assert snapshot.total_ask_quantity == 96

    assert [level.exact_price for level in snapshot.ask_levels[:4]] == [
        Decimal("78.00"),
        Decimal("78.99"),
        Decimal("80.00"),
        Decimal("85.80"),
    ]
    aggregate_ask = snapshot.ask_levels[4]
    assert aggregate_ask.quantity == 91
    assert aggregate_ask.exact_price is None
    assert aggregate_ask.price_lower_bound == Decimal("89.00")
    assert aggregate_ask.price_upper_bound is None
    assert aggregate_ask.lower_bound_inclusive is True
    assert aggregate_ask.upper_bound_inclusive is None
    assert aggregate_ask.aggregation_type == AggregationType.GREATER_THAN_OR_EQUAL
    assert aggregate_ask.raw_display_price == "89.00+"


def test_summary_lines_allow_quantity_classifiers_between_total_and_price() -> None:
    snapshot = parse_screen_order_book_text(
        """
正在购买: 1 件 65.00 或更低
数量 价格 (GJN)
1 65.00

正在出售: 1 个 78.00 或更高
数量 价格 (GJN)
1 78.00
"""
    )

    assert snapshot.best_bid == Decimal("65.00")
    assert snapshot.best_ask == Decimal("78.00")
    assert snapshot.total_bid_quantity == 1
    assert snapshot.total_ask_quantity == 1


def test_parse_price_plus_as_lower_bound_not_exact_price() -> None:
    level = parse_order_book_level(91, "89.00+")

    assert level.quantity == 91
    assert level.exact_price is None
    assert level.price_lower_bound == Decimal("89.00")
    assert level.price_upper_bound is None
    assert level.aggregation_type == AggregationType.GREATER_THAN_OR_EQUAL


def test_parse_price_minus_as_upper_bound_aggregate() -> None:
    level = parse_order_book_level(104, "0.11 -")

    assert level.quantity == 104
    assert level.exact_price is None
    assert level.price_lower_bound is None
    assert level.price_upper_bound == Decimal("0.11")
    assert level.upper_bound_inclusive is True
    assert level.aggregation_type == AggregationType.LESS_THAN_OR_EQUAL


def test_first_exact_bid_and_ask_must_match_best_prices() -> None:
    with pytest.raises(ScreenRecognitionError, match="first exact ask price"):
        ScreenOrderBookSnapshot(
            best_bid=Decimal("65.00"),
            best_ask=Decimal("78.00"),
            total_bid_quantity=1,
            total_ask_quantity=1,
            bid_levels=(parse_order_book_level(1, "65.00"),),
            ask_levels=(parse_order_book_level(1, "78.99"),),
        )


def test_exact_bid_and_ask_level_sorting_is_validated() -> None:
    with pytest.raises(ScreenRecognitionError, match="exact bid levels"):
        ScreenOrderBookSnapshot(
            best_bid=Decimal("65.00"),
            best_ask=Decimal("78.00"),
            total_bid_quantity=2,
            total_ask_quantity=1,
            bid_levels=(
                parse_order_book_level(1, "65.00"),
                parse_order_book_level(1, "66.00"),
            ),
            ask_levels=(parse_order_book_level(1, "78.00"),),
        )
    with pytest.raises(ScreenRecognitionError, match="exact ask levels"):
        ScreenOrderBookSnapshot(
            best_bid=Decimal("65.00"),
            best_ask=Decimal("78.00"),
            total_bid_quantity=1,
            total_ask_quantity=2,
            bid_levels=(parse_order_book_level(1, "65.00"),),
            ask_levels=(
                parse_order_book_level(1, "78.00"),
                parse_order_book_level(1, "77.00"),
            ),
        )


def test_aggregate_ask_lower_bound_must_not_drop_below_previous_exact_ask() -> None:
    with pytest.raises(ScreenRecognitionError, match="aggregate ask lower bound"):
        ScreenOrderBookSnapshot(
            best_bid=Decimal("65.00"),
            best_ask=Decimal("78.00"),
            total_bid_quantity=1,
            total_ask_quantity=2,
            bid_levels=(parse_order_book_level(1, "65.00"),),
            ask_levels=(
                parse_order_book_level(1, "78.00"),
                parse_order_book_level(1, "77.99+"),
            ),
        )


def test_level_quantities_must_sum_to_display_totals() -> None:
    with pytest.raises(ScreenRecognitionError, match="total_ask_quantity"):
        ScreenOrderBookSnapshot(
            best_bid=Decimal("65.00"),
            best_ask=Decimal("78.00"),
            total_bid_quantity=1,
            total_ask_quantity=3,
            bid_levels=(parse_order_book_level(1, "65.00"),),
            ask_levels=(parse_order_book_level(1, "78.00"),),
        )


def test_single_unit_immediate_trade_references_are_best_ask_and_best_bid() -> None:
    snapshot = parse_screen_order_book_text(REFERENCE_SCREEN_TEXT)

    assert single_unit_immediate_buy_reference(snapshot) == Decimal("78.00")
    assert single_unit_immediate_sell_reference(snapshot) == Decimal("65.00")


def test_seller_net_proceeds_require_confirmed_fee_policy() -> None:
    assert seller_proceeds_after_confirmed_fee(
        Decimal("78.00"),
        GAIJIN_MARKET_FEE_POLICY_V1,
    ) == Decimal("66.30")
    with pytest.raises(ScreenRecognitionError, match="confirmed fee policy"):
        seller_proceeds_after_confirmed_fee(Decimal("78.00"), None)


def test_multi_quantity_buy_entering_aggregate_ask_is_marked_incomplete() -> None:
    snapshot = parse_screen_order_book_text(REFERENCE_SCREEN_TEXT)

    estimate = simulate_immediate_buy(snapshot, 6)

    assert estimate.filled_exact_quantity == 5
    assert estimate.gross_total == Decimal("402.79")
    assert estimate.incomplete is True
    assert estimate.incomplete_reason == "entered_aggregate_level"


def test_multi_quantity_sell_uses_exact_bids_until_aggregate_bid() -> None:
    snapshot = parse_screen_order_book_text(REFERENCE_SCREEN_TEXT)

    estimate = simulate_immediate_sell(snapshot, 5)

    assert estimate.filled_exact_quantity == 4
    assert estimate.gross_total == Decimal("187.76")
    assert estimate.incomplete is True
    assert estimate.incomplete_reason == "entered_aggregate_level"


def test_float_money_values_are_rejected() -> None:
    with pytest.raises(ContractValidationError):
        ScreenOrderBookSnapshot(
            best_bid=65.00,  # type: ignore[arg-type]
            best_ask=Decimal("78.00"),
            total_bid_quantity=1,
            total_ask_quantity=1,
            bid_levels=(parse_order_book_level(1, "65.00"),),
            ask_levels=(parse_order_book_level(1, "78.00"),),
        )
