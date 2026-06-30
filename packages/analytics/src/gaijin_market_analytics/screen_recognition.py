from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from gaijin_market_analytics.exceptions import ContractValidationError, InvalidDecimalError
from gaijin_market_analytics.fees import (
    GAIJIN_MARKET_FEE_POLICY_V1,
    FeePolicy,
    calculate_sale_proceeds,
)


class AggregationType(str, Enum):
    EXACT = "exact"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    UNKNOWN_AGGREGATE = "unknown_aggregate"


class ImmediateTradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ScreenRecognitionError(ContractValidationError):
    code = "screen_recognition_error"


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    quantity: int
    exact_price: Decimal | None
    price_lower_bound: Decimal | None
    price_upper_bound: Decimal | None
    lower_bound_inclusive: bool | None
    upper_bound_inclusive: bool | None
    aggregation_type: AggregationType
    raw_display_price: str

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ScreenRecognitionError("order book level quantity must be greater than 0.")
        if not self.raw_display_price.strip():
            raise ScreenRecognitionError("raw_display_price must not be empty.")
        _require_decimal_or_none(self.exact_price, "exact_price")
        _require_decimal_or_none(self.price_lower_bound, "price_lower_bound")
        _require_decimal_or_none(self.price_upper_bound, "price_upper_bound")
        if self.aggregation_type == AggregationType.EXACT:
            if self.exact_price is None:
                raise ScreenRecognitionError("exact levels must include exact_price.")
            if self.price_lower_bound is not None or self.price_upper_bound is not None:
                raise ScreenRecognitionError("exact levels must not include aggregate bounds.")
            return
        if self.exact_price is not None:
            raise ScreenRecognitionError("aggregate levels must not include exact_price.")


@dataclass(frozen=True, slots=True)
class ScreenOrderBookSnapshot:
    best_bid: Decimal
    best_ask: Decimal
    total_bid_quantity: int
    total_ask_quantity: int
    bid_levels: tuple[OrderBookLevel, ...]
    ask_levels: tuple[OrderBookLevel, ...]
    confirmed_fee_policy: FeePolicy = GAIJIN_MARKET_FEE_POLICY_V1

    def __post_init__(self) -> None:
        _require_positive_decimal(self.best_bid, "best_bid")
        _require_positive_decimal(self.best_ask, "best_ask")
        if self.total_bid_quantity <= 0:
            raise ScreenRecognitionError("total_bid_quantity must be greater than 0.")
        if self.total_ask_quantity <= 0:
            raise ScreenRecognitionError("total_ask_quantity must be greater than 0.")
        if not isinstance(self.confirmed_fee_policy, FeePolicy):
            raise ScreenRecognitionError("confirmed_fee_policy must be a FeePolicy.")
        object.__setattr__(self, "bid_levels", tuple(self.bid_levels))
        object.__setattr__(self, "ask_levels", tuple(self.ask_levels))
        validate_order_book_snapshot(self)


@dataclass(frozen=True, slots=True)
class ImmediateFill:
    quantity: int
    price: Decimal


@dataclass(frozen=True, slots=True)
class ImmediateExecutionEstimate:
    side: ImmediateTradeSide
    requested_quantity: int
    filled_exact_quantity: int
    gross_total: Decimal
    fills: tuple[ImmediateFill, ...]
    incomplete: bool
    incomplete_reason: str | None


_PRICE_PATTERN = re.compile(
    r"^\s*(?P<price>\d+(?:[.,]\d+)?)\s*(?P<suffix>\+|-|>=|>|<=|<)?\s*$"
)
_LEVEL_PATTERN = re.compile(
    r"^\s*(?P<quantity>\d+)\s+(?P<price>\d+(?:[.,]\d+)?\s*(?:\+|-|>=|>|<=|<)?)\s*$"
)
_BID_SUMMARY_PATTERN = re.compile(
    r"正在购买[:：]\s*(?P<quantity>\d+)\D*?(?P<price>\d+(?:[.,]\d+)?)"
)
_ASK_SUMMARY_PATTERN = re.compile(
    r"正在出售[:：]\s*(?P<quantity>\d+)\D*?(?P<price>\d+(?:[.,]\d+)?)"
)


def parse_order_book_level(quantity: int, raw_display_price: str) -> OrderBookLevel:
    raw_display_price = raw_display_price.strip()
    match = _PRICE_PATTERN.match(raw_display_price)
    if match is None:
        return OrderBookLevel(
            quantity=quantity,
            exact_price=None,
            price_lower_bound=None,
            price_upper_bound=None,
            lower_bound_inclusive=None,
            upper_bound_inclusive=None,
            aggregation_type=AggregationType.UNKNOWN_AGGREGATE,
            raw_display_price=raw_display_price,
        )

    price = _parse_decimal(match.group("price"), "raw_display_price")
    suffix = match.group("suffix")
    if suffix is None:
        return OrderBookLevel(
            quantity=quantity,
            exact_price=price,
            price_lower_bound=None,
            price_upper_bound=None,
            lower_bound_inclusive=None,
            upper_bound_inclusive=None,
            aggregation_type=AggregationType.EXACT,
            raw_display_price=raw_display_price,
        )
    if suffix == "+":
        return _lower_bound_level(
            quantity,
            raw_display_price,
            price,
            inclusive=True,
            aggregation_type=AggregationType.GREATER_THAN_OR_EQUAL,
        )
    if suffix == ">=":
        return _lower_bound_level(
            quantity,
            raw_display_price,
            price,
            inclusive=True,
            aggregation_type=AggregationType.GREATER_THAN_OR_EQUAL,
        )
    if suffix == ">":
        return _lower_bound_level(
            quantity,
            raw_display_price,
            price,
            inclusive=False,
            aggregation_type=AggregationType.GREATER_THAN,
        )
    if suffix == "-":
        return _upper_bound_level(
            quantity,
            raw_display_price,
            price,
            inclusive=True,
            aggregation_type=AggregationType.LESS_THAN_OR_EQUAL,
        )
    if suffix == "<=":
        return _upper_bound_level(
            quantity,
            raw_display_price,
            price,
            inclusive=True,
            aggregation_type=AggregationType.LESS_THAN_OR_EQUAL,
        )
    return _upper_bound_level(
        quantity,
        raw_display_price,
        price,
        inclusive=False,
        aggregation_type=AggregationType.LESS_THAN,
    )


def parse_screen_order_book_text(text: str) -> ScreenOrderBookSnapshot:
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    total_bid_quantity: int | None = None
    total_ask_quantity: int | None = None
    bid_levels: list[OrderBookLevel] = []
    ask_levels: list[OrderBookLevel] = []
    side: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or "数量" in stripped or "价格" in stripped:
            continue
        bid_summary = _BID_SUMMARY_PATTERN.search(stripped)
        if bid_summary is not None:
            side = "bid"
            total_bid_quantity = int(bid_summary.group("quantity"))
            best_bid = _parse_decimal(bid_summary.group("price"), "best_bid")
            continue
        ask_summary = _ASK_SUMMARY_PATTERN.search(stripped)
        if ask_summary is not None:
            side = "ask"
            total_ask_quantity = int(ask_summary.group("quantity"))
            best_ask = _parse_decimal(ask_summary.group("price"), "best_ask")
            continue
        level_match = _LEVEL_PATTERN.match(stripped)
        if level_match is None or side is None:
            continue
        level = parse_order_book_level(
            int(level_match.group("quantity")),
            level_match.group("price"),
        )
        if side == "bid":
            bid_levels.append(level)
        else:
            ask_levels.append(level)

    if (
        best_bid is None
        or best_ask is None
        or total_bid_quantity is None
        or total_ask_quantity is None
    ):
        raise ScreenRecognitionError("screen text is missing bid or ask summary values.")

    return ScreenOrderBookSnapshot(
        best_bid=best_bid,
        best_ask=best_ask,
        total_bid_quantity=total_bid_quantity,
        total_ask_quantity=total_ask_quantity,
        bid_levels=tuple(bid_levels),
        ask_levels=tuple(ask_levels),
    )


def validate_order_book_snapshot(snapshot: ScreenOrderBookSnapshot) -> None:
    if _sum_quantities(snapshot.bid_levels) != snapshot.total_bid_quantity:
        raise ScreenRecognitionError("bid level quantities must sum to total_bid_quantity.")
    if _sum_quantities(snapshot.ask_levels) != snapshot.total_ask_quantity:
        raise ScreenRecognitionError("ask level quantities must sum to total_ask_quantity.")

    first_exact_bid = _first_exact(snapshot.bid_levels)
    if first_exact_bid is None or first_exact_bid.exact_price != snapshot.best_bid:
        raise ScreenRecognitionError("first exact bid price must equal best_bid.")
    first_exact_ask = _first_exact(snapshot.ask_levels)
    if first_exact_ask is None or first_exact_ask.exact_price != snapshot.best_ask:
        raise ScreenRecognitionError("first exact ask price must equal best_ask.")

    _validate_exact_order(snapshot.bid_levels, descending=True, side_name="bid")
    _validate_exact_order(snapshot.ask_levels, descending=False, side_name="ask")
    _validate_ask_aggregate_bounds(snapshot.ask_levels)


def single_unit_immediate_buy_reference(snapshot: ScreenOrderBookSnapshot) -> Decimal:
    return snapshot.best_ask


def single_unit_immediate_sell_reference(snapshot: ScreenOrderBookSnapshot) -> Decimal:
    return snapshot.best_bid


def seller_proceeds_after_confirmed_fee(
    sell_price: Decimal,
    fee_policy: FeePolicy | None,
) -> Decimal:
    if fee_policy is None:
        raise ScreenRecognitionError("seller proceeds require a confirmed fee policy.")
    return calculate_sale_proceeds(sell_price, fee_policy)


def simulate_immediate_buy(
    snapshot: ScreenOrderBookSnapshot,
    quantity: int,
) -> ImmediateExecutionEstimate:
    return _simulate_exact_visible_levels(
        side=ImmediateTradeSide.BUY,
        levels=snapshot.ask_levels,
        quantity=quantity,
    )


def simulate_immediate_sell(
    snapshot: ScreenOrderBookSnapshot,
    quantity: int,
) -> ImmediateExecutionEstimate:
    return _simulate_exact_visible_levels(
        side=ImmediateTradeSide.SELL,
        levels=snapshot.bid_levels,
        quantity=quantity,
    )


def _simulate_exact_visible_levels(
    *,
    side: ImmediateTradeSide,
    levels: tuple[OrderBookLevel, ...],
    quantity: int,
) -> ImmediateExecutionEstimate:
    if quantity <= 0:
        raise ScreenRecognitionError("quantity must be greater than 0.")

    remaining = quantity
    fills: list[ImmediateFill] = []
    gross_total = Decimal("0")
    for level in levels:
        if remaining <= 0:
            break
        if level.aggregation_type != AggregationType.EXACT:
            return ImmediateExecutionEstimate(
                side=side,
                requested_quantity=quantity,
                filled_exact_quantity=quantity - remaining,
                gross_total=gross_total,
                fills=tuple(fills),
                incomplete=True,
                incomplete_reason="entered_aggregate_level",
            )
        if level.exact_price is None:
            raise ScreenRecognitionError("exact level is missing exact_price.")
        fill_quantity = min(remaining, level.quantity)
        fills.append(ImmediateFill(quantity=fill_quantity, price=level.exact_price))
        gross_total += level.exact_price * fill_quantity
        remaining -= fill_quantity

    return ImmediateExecutionEstimate(
        side=side,
        requested_quantity=quantity,
        filled_exact_quantity=quantity - remaining,
        gross_total=gross_total,
        fills=tuple(fills),
        incomplete=remaining > 0,
        incomplete_reason="insufficient_visible_exact_depth" if remaining > 0 else None,
    )


def _lower_bound_level(
    quantity: int,
    raw_display_price: str,
    price: Decimal,
    *,
    inclusive: bool,
    aggregation_type: AggregationType,
) -> OrderBookLevel:
    return OrderBookLevel(
        quantity=quantity,
        exact_price=None,
        price_lower_bound=price,
        price_upper_bound=None,
        lower_bound_inclusive=inclusive,
        upper_bound_inclusive=None,
        aggregation_type=aggregation_type,
        raw_display_price=raw_display_price,
    )


def _upper_bound_level(
    quantity: int,
    raw_display_price: str,
    price: Decimal,
    *,
    inclusive: bool,
    aggregation_type: AggregationType,
) -> OrderBookLevel:
    return OrderBookLevel(
        quantity=quantity,
        exact_price=None,
        price_lower_bound=None,
        price_upper_bound=price,
        lower_bound_inclusive=None,
        upper_bound_inclusive=inclusive,
        aggregation_type=aggregation_type,
        raw_display_price=raw_display_price,
    )


def _validate_exact_order(
    levels: tuple[OrderBookLevel, ...],
    *,
    descending: bool,
    side_name: str,
) -> None:
    exact_prices = [level.exact_price for level in levels if level.aggregation_type == AggregationType.EXACT]
    for previous, current in zip(exact_prices, exact_prices[1:]):
        if previous is None or current is None:
            raise ScreenRecognitionError(f"{side_name} exact level is missing exact_price.")
        if descending and current > previous:
            raise ScreenRecognitionError("exact bid levels must be sorted by price descending.")
        if not descending and current < previous:
            raise ScreenRecognitionError("exact ask levels must be sorted by price ascending.")


def _validate_ask_aggregate_bounds(levels: tuple[OrderBookLevel, ...]) -> None:
    previous_exact_price: Decimal | None = None
    for level in levels:
        if level.aggregation_type == AggregationType.EXACT:
            previous_exact_price = level.exact_price
            continue
        if previous_exact_price is None or level.price_lower_bound is None:
            continue
        if level.price_lower_bound < previous_exact_price:
            raise ScreenRecognitionError(
                "aggregate ask lower bound must not be below previous exact ask price."
            )


def _first_exact(levels: tuple[OrderBookLevel, ...]) -> OrderBookLevel | None:
    return next(
        (level for level in levels if level.aggregation_type == AggregationType.EXACT),
        None,
    )


def _sum_quantities(levels: tuple[OrderBookLevel, ...]) -> int:
    return sum(level.quantity for level in levels)


def _parse_decimal(value: str, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(value.replace(",", "."))
    except Exception as exc:
        raise InvalidDecimalError(f"{field_name} must be a Decimal.") from exc
    _require_positive_decimal(decimal_value, field_name)
    return decimal_value


def _require_decimal_or_none(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    _require_positive_decimal(value, field_name)


def _require_positive_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise InvalidDecimalError(f"{field_name} must be a finite Decimal greater than 0.")
