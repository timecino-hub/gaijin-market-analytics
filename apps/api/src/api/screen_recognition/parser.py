from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from api.screen_recognition.contracts import MARKET_PRICE_CAP, OcrFieldEvidence, PriceLevel, ScreenContract


PRICE_RE = re.compile(r"(?P<price>\d+(?:[\.,]\d{1,2})?)(?P<plus>\+)?")
LINE_RE = re.compile(r"(?P<price>\d+(?:[\.,]\d{1,2})?\+?)\D+(?P<quantity>\d+)")


class ParseIssue(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_item_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return re.sub(r"\s+", " ", normalized)


def parse_ocr_contract(
    fields: dict[str, OcrFieldEvidence], *, item_key: str | None
) -> tuple[ScreenContract, list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    raw = {name: evidence.raw_text for name, evidence in fields.items()}
    item_name = normalize_item_name(raw.get("item_name", ""))
    if not item_name:
        errors.append("item_name_missing")
        item_name = None

    best_bid = _parse_price_field(raw.get("best_bid", ""), "best_bid", errors, warnings)
    best_ask = _parse_price_field(raw.get("best_ask", ""), "best_ask", errors, warnings)
    total_bid_quantity = _parse_quantity_field(
        raw.get("total_bid_quantity", ""), "total_bid_quantity", errors
    )
    total_ask_quantity = _parse_quantity_field(
        raw.get("total_ask_quantity", ""), "total_ask_quantity", errors
    )
    bid_levels = _parse_levels(raw.get("bid_levels", ""), errors, warnings)
    ask_levels = _parse_levels(raw.get("ask_levels", ""), errors, warnings)

    contract = ScreenContract(
        item_key=item_key,
        item_key_source="ground_truth_manifest" if item_key else None,
        item_name=item_name,
        best_bid=best_bid,
        best_ask=best_ask,
        total_bid_quantity=total_bid_quantity,
        total_ask_quantity=total_ask_quantity,
        bid_levels=tuple(bid_levels),
        ask_levels=tuple(ask_levels),
        raw_fields=raw,
    )
    errors.extend(validate_screen_contract(contract))
    return contract, warnings, errors


def validate_screen_contract(contract: ScreenContract) -> list[str]:
    errors: list[str] = []
    if contract.best_bid is None:
        errors.append("best_bid_missing")
    if contract.best_ask is None:
        errors.append("best_ask_missing")
    if contract.best_bid is not None and contract.best_ask is not None and contract.best_bid > contract.best_ask:
        errors.append("bid_ask_swapped")
    errors.extend(_validate_prices([contract.best_bid, contract.best_ask]))
    errors.extend(_validate_levels(contract.bid_levels, side="bid", best_price=contract.best_bid))
    errors.extend(_validate_levels(contract.ask_levels, side="ask", best_price=contract.best_ask))
    if _quantity_sum(contract.bid_levels) is not None and contract.total_bid_quantity is not None:
        if _quantity_sum(contract.bid_levels) != contract.total_bid_quantity:
            errors.append("displayed_quantity_sum_mismatch")
    if _quantity_sum(contract.ask_levels) is not None and contract.total_ask_quantity is not None:
        if _quantity_sum(contract.ask_levels) != contract.total_ask_quantity:
            errors.append("displayed_quantity_sum_mismatch")
    return errors


def _parse_price_field(
    text: str, field: str, errors: list[str], warnings: list[str]
) -> Decimal | None:
    match = PRICE_RE.search(text or "")
    if not match:
        errors.append(f"{field}_missing")
        return None
    level = _parse_price_token(match.group("price") + (match.group("plus") or ""), warnings)
    if level is None or level.exact_price is None:
        return None
    _validate_price(level.exact_price, errors)
    return level.exact_price


def _parse_quantity_field(text: str, field: str, errors: list[str]) -> int | None:
    match = re.search(r"\d+", text or "")
    if not match:
        errors.append(f"{field}_mismatch")
        return None
    return int(match.group(0))


def _parse_levels(text: str, errors: list[str], warnings: list[str]) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = LINE_RE.search(line)
        if match:
            level = _parse_price_token(match.group("price"), warnings)
            if level is None:
                continue
            levels.append(
                PriceLevel(
                    exact_price=level.exact_price,
                    price_lower_bound=level.price_lower_bound,
                    lower_bound_inclusive=level.lower_bound_inclusive,
                    aggregation_type=level.aggregation_type,
                    quantity=int(match.group("quantity")),
                    raw_display_price=level.raw_display_price,
                    raw_quantity=match.group("quantity"),
                )
            )
            continue
        price_match = PRICE_RE.search(line)
        if price_match:
            level = _parse_price_token(
                price_match.group("price") + (price_match.group("plus") or ""), warnings
            )
            if level is not None:
                levels.append(level)
    for level in levels:
        if level.exact_price is not None:
            _validate_price(level.exact_price, errors)
        if level.price_lower_bound is not None:
            _validate_price(level.price_lower_bound, errors)
    return levels


def _parse_price_token(token: str, warnings: list[str]) -> PriceLevel | None:
    raw = token.strip()
    aggregate = raw.endswith("+")
    number = raw[:-1] if aggregate else raw
    if "," in number and "." not in number:
        number = number.replace(",", ".")
        warnings.append("decimal_separator_repaired")
    elif "," in number:
        warnings.append("decimal_separator_ambiguous")
        return None
    try:
        parsed = Decimal(number)
    except InvalidOperation:
        return None
    if aggregate:
        return PriceLevel(
            exact_price=None,
            price_lower_bound=parsed,
            lower_bound_inclusive=True,
            aggregation_type="greater_than_or_equal",
            quantity=None,
            raw_display_price=raw,
        )
    return PriceLevel(exact_price=parsed, quantity=None, raw_display_price=raw)


def _validate_prices(values: list[Decimal | None]) -> list[str]:
    errors: list[str] = []
    for value in values:
        _validate_price(value, errors)
    return errors


def _validate_price(value: Decimal | None, errors: list[str]) -> None:
    if value is None:
        return
    if value <= 0:
        errors.append("non_positive_price")
    if value > MARKET_PRICE_CAP:
        errors.append("price_above_market_cap")


def _validate_levels(
    levels: tuple[PriceLevel, ...], *, side: str, best_price: Decimal | None
) -> list[str]:
    errors: list[str] = []
    exact_prices = [level.exact_price for level in levels if level.exact_price is not None]
    if len(exact_prices) >= 2:
        if side == "bid" and exact_prices != sorted(exact_prices, reverse=True):
            errors.append("bid_levels_not_descending")
        if side == "ask" and exact_prices != sorted(exact_prices):
            errors.append("ask_levels_not_ascending")
    if best_price is not None and exact_prices:
        if side == "bid" and exact_prices[0] != best_price:
            errors.append("first_bid_not_equal_best_bid")
        if side == "ask" and exact_prices[0] != best_price:
            errors.append("first_ask_not_equal_best_ask")
    return errors


def _quantity_sum(levels: tuple[PriceLevel, ...]) -> int | None:
    if not levels or any(level.quantity is None for level in levels):
        return None
    return sum(level.quantity or 0 for level in levels)
