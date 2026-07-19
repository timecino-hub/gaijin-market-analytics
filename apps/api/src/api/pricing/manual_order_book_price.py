"""Pure reference implementation of the versioned current-book GJN contract.

Raw prices remain authoritative.  Nothing in this module reads files, reads the
environment, accesses a network or database, or mutates a validated capture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from api.importers.manual_order_book_json import (
    JS_SAFE_INTEGER_MAX,
    ManualOrderBookCapture,
    parse_manual_order_book_json,
)


CONTRACT_ID = "gaijin_market_1067_current_book_gjn_v1"
CONTRACT_VERSION = 1
CAPTURE_SCHEMA_VERSION = "round6_manual_order_book_capture_v1"
PAGE_ORIGIN = "https://trade.gaijin.net"
MARKET_ID = "1067"
REQUEST_METHOD = "POST"
REQUEST_ORIGIN = "https://market-proxy.gaijin.net"
REQUEST_PATH = "/web"
ORDER_BOOK_TYPE = "COMMODITY"
CURRENCY_CODE = "GJN"
RAW_SCALE = 10_000
DISPLAY_DECIMAL_PLACES = 2
MAX_EXACT_SAVED_FORMATTER_RAW = 2**53 - 99
EVIDENCE_LEVEL = "CONFIRMED_BY_PAIRED_OBSERVATION"
EVIDENCE_PACKAGE_SHA256 = (
    "f3a5a540fbde5fd0892320486b4b5ac85265f207e5c0abf107c84a7a398db0af"
)

_PAGE_PATH_RE = re.compile(r"^/market/1067/[^/?#]+$")


class PriceInterpretationError(ValueError):
    """Stable contract or value failure without capture contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ManualOrderBookPriceInterpretation:
    """Exact economic and canonical display values under one contract."""

    contract_id: str
    contract_version: int
    price_raw: int
    raw_scale: int
    currency_code: str
    base_amount_gjn: Decimal
    display_minor_units: int
    display_amount_gjn: Decimal
    canonical_display_text: str
    display_decimal_places: int
    evidence_level: str
    evidence_package_sha256: str


def _validate_contract_applicability(
    capture: ManualOrderBookCapture,
    *,
    display_currency: str,
    contract_id: str,
    contract_version: int,
) -> None:
    """Reject any capture or caller context outside the exact v1 contract."""

    _validate_contract(contract_id, contract_version)
    _validate_display_currency(display_currency)
    if capture.schema_version != CAPTURE_SCHEMA_VERSION:
        raise PriceInterpretationError("capture_schema_not_applicable")
    if capture.page.origin != PAGE_ORIGIN:
        raise PriceInterpretationError("page_origin_not_applicable")
    if _PAGE_PATH_RE.fullmatch(capture.page.path) is None:
        raise PriceInterpretationError("page_path_not_applicable")
    if capture.request.method != REQUEST_METHOD:
        raise PriceInterpretationError("request_method_not_applicable")
    if capture.request.origin != REQUEST_ORIGIN:
        raise PriceInterpretationError("request_origin_not_applicable")
    if capture.request.path != REQUEST_PATH:
        raise PriceInterpretationError("request_path_not_applicable")
    if capture.order_book.type != ORDER_BOOK_TYPE:
        raise PriceInterpretationError("order_book_type_not_applicable")


def interpret_current_order_book_document(
    content: bytes | str,
    price_raw: int,
    *,
    display_currency: str,
    contract_id: str,
    contract_version: int,
) -> ManualOrderBookPriceInterpretation:
    """Strictly parse one document before applying the evidence contract."""

    capture = parse_manual_order_book_json(content)
    _validate_contract_applicability(
        capture,
        display_currency=display_currency,
        contract_id=contract_id,
        contract_version=contract_version,
    )
    return _interpret_applicable_price_raw(price_raw)


def _interpret_applicable_price_raw(
    price_raw: int,
) -> ManualOrderBookPriceInterpretation:
    _validate_price_raw(price_raw)
    display_minor_units = (price_raw + 99) // 100
    base_amount = _decimal_from_scaled_positive_integer(price_raw, 4)
    display_amount = _decimal_from_scaled_positive_integer(
        display_minor_units,
        2,
    )
    canonical_display_text = (
        f"{display_minor_units // 100}.{display_minor_units % 100:02d}"
    )
    return ManualOrderBookPriceInterpretation(
        contract_id=CONTRACT_ID,
        contract_version=CONTRACT_VERSION,
        price_raw=price_raw,
        raw_scale=RAW_SCALE,
        currency_code=CURRENCY_CODE,
        base_amount_gjn=base_amount,
        display_minor_units=display_minor_units,
        display_amount_gjn=display_amount,
        canonical_display_text=canonical_display_text,
        display_decimal_places=DISPLAY_DECIMAL_PLACES,
        evidence_level=EVIDENCE_LEVEL,
        evidence_package_sha256=EVIDENCE_PACKAGE_SHA256,
    )


def _decimal_from_scaled_positive_integer(
    value: int,
    decimal_places: int,
) -> Decimal:
    digits = tuple(int(character) for character in str(value))
    return Decimal((0, digits, -decimal_places))


def _validate_contract(contract_id: str, contract_version: int) -> None:
    if type(contract_id) is not str:
        raise PriceInterpretationError("price_contract_id_type_invalid")
    if contract_id != CONTRACT_ID:
        raise PriceInterpretationError("price_contract_id_unsupported")
    if type(contract_version) is not int:
        raise PriceInterpretationError("price_contract_version_type_invalid")
    if contract_version != CONTRACT_VERSION:
        raise PriceInterpretationError("price_contract_version_unsupported")


def _validate_display_currency(display_currency: str) -> None:
    if type(display_currency) is not str:
        raise PriceInterpretationError("display_currency_type_invalid")
    if display_currency != CURRENCY_CODE:
        raise PriceInterpretationError("display_currency_not_applicable")


def _validate_price_raw(price_raw: int) -> None:
    if type(price_raw) is not int:
        raise PriceInterpretationError("price_raw_type_invalid")
    if price_raw <= 0 or price_raw > JS_SAFE_INTEGER_MAX:
        raise PriceInterpretationError("price_raw_out_of_range")
    if price_raw > MAX_EXACT_SAVED_FORMATTER_RAW:
        raise PriceInterpretationError("price_raw_display_range_unsupported")
