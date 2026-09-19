"""Strict offline validator for confirmed Round 6 manual order-book captures.

This module is deliberately independent of database, API-router, and network
code. It validates an explicitly supplied local document and preserves raw
integer prices without inferring a display-price scale.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote_to_bytes


SCHEMA_VERSION = "round6_manual_order_book_capture_v1"
CAPTURE_METHOD = "passive_page_response_intercept"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_JSON_NESTING_DEPTH = 32
MAX_LEVELS_PER_SIDE = 500
JS_SAFE_INTEGER_MAX = 9_007_199_254_740_991

_CAPTURE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CAPTURED_AT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAGE_PATH_RE = re.compile(r"^/market/1067/([^/]+)$")


class ManualOrderBookValidationError(ValueError):
    """Stable validation failure that never includes the source document."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PageIdentity:
    origin: str
    path: str
    literal_external_key: str
    decoded_external_key: str


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    method: str
    origin: str
    path: str


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price_raw: int
    quantity: int


@dataclass(frozen=True, slots=True)
class ReportedDepth:
    buy: int
    sell: int


@dataclass(frozen=True, slots=True)
class BestLevel:
    price_raw: int
    quantity: int


@dataclass(frozen=True, slots=True)
class ValidatedOrderBook:
    success: bool
    type: str
    buy_levels: tuple[OrderBookLevel, ...]
    sell_levels: tuple[OrderBookLevel, ...]
    reported_depth: ReportedDepth
    buy_level_count: int
    sell_level_count: int
    best_buy: BestLevel
    best_sell: BestLevel


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    capture_method: str
    raw_response_sha256: str
    normalized_capture_fingerprint: str
    raw_response_hash_verifiable: bool = False


@dataclass(frozen=True, slots=True)
class ReviewState:
    status: str
    fill_claim: bool
    requires_manual_review: bool


@dataclass(frozen=True, slots=True)
class ManualOrderBookCapture:
    schema_version: str
    capture_id: str
    captured_at: str
    timezone_offset_minutes: int
    page: PageIdentity
    request: RequestIdentity
    order_book: ValidatedOrderBook
    source: SourceProvenance
    review: ReviewState


def parse_manual_order_book_json(content: bytes | str) -> ManualOrderBookCapture:
    """Validate a confirmed export supplied as bytes or already-read text."""

    text = _decode_document(content)
    _validate_json_nesting_depth(text)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ManualOrderBookValidationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ManualOrderBookValidationError("invalid_json") from exc
    return _parse_document(value)


def confirm_pending_manual_order_book_json(content: bytes | str) -> bytes:
    """Promote one intact pending export through an explicit operator action."""

    text = _decode_document(content)
    _validate_json_nesting_depth(text)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ManualOrderBookValidationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ManualOrderBookValidationError("invalid_json") from exc
    if not isinstance(value, dict):
        raise ManualOrderBookValidationError("top_level_keys_invalid")

    source = value.get("source")
    review = value.get("review")
    if not isinstance(source, dict):
        raise ManualOrderBookValidationError("source_keys_invalid")
    if not isinstance(review, dict):
        raise ManualOrderBookValidationError("review_keys_invalid")
    if source.get("normalized_capture_fingerprint") != (
        compute_normalized_capture_fingerprint(value)
    ):
        raise ManualOrderBookValidationError("fingerprint_mismatch")
    if review.get("status") != "pending_user_confirmation":
        raise ManualOrderBookValidationError("review_status_not_pending")
    if review.get("fill_claim") is not False:
        raise ManualOrderBookValidationError("fill_claim_not_importable")
    if review.get("requires_manual_review") is not False:
        raise ManualOrderBookValidationError("manual_review_required")

    review["status"] = "confirmed_by_user"
    source["normalized_capture_fingerprint"] = (
        compute_normalized_capture_fingerprint(value)
    )
    promoted = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    parse_manual_order_book_json(promoted)
    return promoted


def read_manual_order_book_file(path: Path) -> bytes:
    """Read at most 2 MiB plus one byte from one explicit local path."""

    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_DOCUMENT_BYTES + 1)
    except FileNotFoundError as exc:
        raise ManualOrderBookValidationError("file_not_found") from exc
    except OSError as exc:
        raise ManualOrderBookValidationError("file_read_error") from exc
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ManualOrderBookValidationError("file_too_large")
    return content


def canonical_json(value: object) -> str:
    """Return the canonical JSON form used by the approved extension."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_normalized_capture_fingerprint(document: Mapping[str, object]) -> str:
    """Recompute the extension fingerprint, excluding the fingerprint itself."""

    try:
        source = document["source"]
        if not isinstance(source, Mapping):
            raise TypeError
        fingerprint_input = {
            "schema_version": document["schema_version"],
            "capture_id": document["capture_id"],
            "captured_at": document["captured_at"],
            "timezone_offset_minutes": document["timezone_offset_minutes"],
            "page": document["page"],
            "request": document["request"],
            "order_book": document["order_book"],
            "source": {
                "capture_method": source["capture_method"],
                "raw_response_sha256": source["raw_response_sha256"],
            },
            "review": document["review"],
        }
        encoded = canonical_json(fingerprint_input).encode("utf-8")
    except (KeyError, TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ManualOrderBookValidationError("fingerprint_input_invalid") from exc
    return hashlib.sha256(encoded).hexdigest()


def validation_summary(capture: ManualOrderBookCapture) -> dict[str, object]:
    """Return the intentionally narrow, non-sensitive CLI summary."""

    return {
        "validation_status": "valid",
        "schema_version": capture.schema_version,
        "capture_id": capture.capture_id,
        "captured_at": capture.captured_at,
        "literal_external_key": capture.page.literal_external_key,
        "decoded_external_key": capture.page.decoded_external_key,
        "buy_level_count": capture.order_book.buy_level_count,
        "sell_level_count": capture.order_book.sell_level_count,
        "reported_depth": {
            "BUY": capture.order_book.reported_depth.buy,
            "SELL": capture.order_book.reported_depth.sell,
        },
        "best_buy_price_raw": capture.order_book.best_buy.price_raw,
        "best_sell_price_raw": capture.order_book.best_sell.price_raw,
        "normalized_capture_fingerprint": (
            capture.source.normalized_capture_fingerprint
        ),
    }


def _decode_document(content: bytes | str) -> str:
    if isinstance(content, bytes):
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ManualOrderBookValidationError("file_too_large")
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ManualOrderBookValidationError("invalid_utf8") from exc
    elif isinstance(content, str):
        try:
            encoded = content.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ManualOrderBookValidationError("invalid_utf8") from exc
        if len(encoded) > MAX_DOCUMENT_BYTES:
            raise ManualOrderBookValidationError("file_too_large")
        text = content
    else:
        raise ManualOrderBookValidationError("document_type_invalid")
    if "\ufffd" in text:
        raise ManualOrderBookValidationError("replacement_character_forbidden")
    return text


def _validate_json_nesting_depth(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING_DEPTH:
                raise ManualOrderBookValidationError("json_nesting_too_deep")
        elif character in "]}":
            depth = max(depth - 1, 0)


def _object_without_duplicate_keys(
    pairs: Sequence[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManualOrderBookValidationError("duplicate_object_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise ManualOrderBookValidationError("invalid_json")


def _parse_document(value: object) -> ManualOrderBookCapture:
    document = _exact_object(
        value,
        {
            "schema_version",
            "capture_id",
            "captured_at",
            "timezone_offset_minutes",
            "page",
            "request",
            "order_book",
            "source",
            "review",
        },
        "top_level_keys_invalid",
    )
    schema_version = document["schema_version"]
    if schema_version != SCHEMA_VERSION:
        raise ManualOrderBookValidationError("schema_version_unsupported")
    capture_id = _capture_id(document["capture_id"])
    captured_at = _captured_at(document["captured_at"])
    timezone_offset = _integer(
        document["timezone_offset_minutes"],
        "timezone_offset_invalid",
        minimum=-840,
        maximum=840,
    )
    page = _parse_page(document["page"])
    request = _parse_request(document["request"])
    order_book = _parse_order_book(document["order_book"])
    source = _parse_source(document["source"])
    review = _parse_review(document["review"])

    expected_fingerprint = compute_normalized_capture_fingerprint(document)
    if source.normalized_capture_fingerprint != expected_fingerprint:
        raise ManualOrderBookValidationError("fingerprint_mismatch")
    if review.status != "confirmed_by_user":
        raise ManualOrderBookValidationError("review_status_not_importable")
    if review.fill_claim is not False:
        raise ManualOrderBookValidationError("fill_claim_not_importable")
    if order_book.best_buy.price_raw > order_book.best_sell.price_raw:
        raise ManualOrderBookValidationError("crossed_order_book")
    if review.requires_manual_review is not False:
        raise ManualOrderBookValidationError("manual_review_required")

    return ManualOrderBookCapture(
        schema_version=schema_version,
        capture_id=capture_id,
        captured_at=captured_at,
        timezone_offset_minutes=timezone_offset,
        page=page,
        request=request,
        order_book=order_book,
        source=source,
        review=review,
    )


def _capture_id(value: object) -> str:
    if not isinstance(value, str) or not _CAPTURE_ID_RE.fullmatch(value):
        raise ManualOrderBookValidationError("capture_id_invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ManualOrderBookValidationError("capture_id_invalid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ManualOrderBookValidationError("capture_id_invalid")
    return value


def _captured_at(value: object) -> str:
    if not isinstance(value, str) or not _CAPTURED_AT_RE.fullmatch(value):
        raise ManualOrderBookValidationError("captured_at_invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ManualOrderBookValidationError("captured_at_invalid") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" != value:
        raise ManualOrderBookValidationError("captured_at_invalid")
    return value


def _parse_page(value: object) -> PageIdentity:
    page = _exact_object(
        value,
        {"origin", "path", "external_key"},
        "page_keys_invalid",
    )
    if page["origin"] != "https://trade.gaijin.net":
        raise ManualOrderBookValidationError("page_origin_invalid")
    path = page["path"]
    if not isinstance(path, str) or "?" in path or "#" in path:
        raise ManualOrderBookValidationError("page_path_invalid")
    match = _PAGE_PATH_RE.fullmatch(path)
    if match is None:
        raise ManualOrderBookValidationError("page_path_invalid")
    literal_key = match.group(1)
    if page["external_key"] != literal_key:
        raise ManualOrderBookValidationError("page_external_key_mismatch")
    decoded_key = _strict_percent_decode(literal_key)
    return PageIdentity(
        origin="https://trade.gaijin.net",
        path=path,
        literal_external_key=literal_key,
        decoded_external_key=decoded_key,
    )


def _strict_percent_decode(value: str) -> str:
    if any("\ud800" <= character <= "\udfff" for character in value):
        raise ManualOrderBookValidationError("page_percent_encoding_invalid")
    index = 0
    while index < len(value):
        if value[index] == "%":
            if (
                index + 2 >= len(value)
                or value[index + 1] not in "0123456789abcdefABCDEF"
                or value[index + 2] not in "0123456789abcdefABCDEF"
            ):
                raise ManualOrderBookValidationError(
                    "page_percent_encoding_invalid"
                )
            index += 3
        else:
            index += 1
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise ManualOrderBookValidationError(
            "page_percent_encoding_invalid"
        ) from exc
    if not decoded or any(
        character in {"/", "\\", "?", "#", "\x00"}
        or unicodedata.category(character) == "Cc"
        for character in decoded
    ):
        raise ManualOrderBookValidationError("page_decoded_key_invalid")
    return decoded


def _parse_request(value: object) -> RequestIdentity:
    request = _exact_object(
        value,
        {"method", "origin", "path"},
        "request_keys_invalid",
    )
    if (
        request["method"] != "POST"
        or request["origin"] != "https://market-proxy.gaijin.net"
        or request["path"] != "/web"
    ):
        raise ManualOrderBookValidationError("request_identity_invalid")
    return RequestIdentity(
        method="POST",
        origin="https://market-proxy.gaijin.net",
        path="/web",
    )


def _parse_order_book(value: object) -> ValidatedOrderBook:
    order_book = _exact_object(
        value,
        {
            "success",
            "type",
            "buy_levels",
            "sell_levels",
            "reported_depth",
            "buy_level_count",
            "sell_level_count",
            "best_buy",
            "best_sell",
        },
        "order_book_keys_invalid",
    )
    if order_book["success"] is not True:
        raise ManualOrderBookValidationError("order_book_success_invalid")
    if order_book["type"] != "COMMODITY":
        raise ManualOrderBookValidationError("order_book_type_invalid")
    buy_levels = _levels(order_book["buy_levels"], "buy_levels_invalid")
    sell_levels = _levels(order_book["sell_levels"], "sell_levels_invalid")
    reported_depth = _parse_reported_depth(order_book["reported_depth"])
    buy_count = _integer(
        order_book["buy_level_count"],
        "buy_level_count_invalid",
        minimum=0,
        maximum=MAX_LEVELS_PER_SIDE,
    )
    sell_count = _integer(
        order_book["sell_level_count"],
        "sell_level_count_invalid",
        minimum=0,
        maximum=MAX_LEVELS_PER_SIDE,
    )
    if buy_count != len(buy_levels):
        raise ManualOrderBookValidationError("buy_level_count_mismatch")
    if sell_count != len(sell_levels):
        raise ManualOrderBookValidationError("sell_level_count_mismatch")
    best_buy = _parse_best_level(order_book["best_buy"], "best_buy_invalid")
    best_sell = _parse_best_level(
        order_book["best_sell"],
        "best_sell_invalid",
    )
    calculated_buy = max(buy_levels, key=lambda level: level.price_raw)
    calculated_sell = min(sell_levels, key=lambda level: level.price_raw)
    if best_buy != BestLevel(
        calculated_buy.price_raw,
        calculated_buy.quantity,
    ):
        raise ManualOrderBookValidationError("best_buy_mismatch")
    if best_sell != BestLevel(
        calculated_sell.price_raw,
        calculated_sell.quantity,
    ):
        raise ManualOrderBookValidationError("best_sell_mismatch")
    return ValidatedOrderBook(
        success=True,
        type="COMMODITY",
        buy_levels=buy_levels,
        sell_levels=sell_levels,
        reported_depth=reported_depth,
        buy_level_count=buy_count,
        sell_level_count=sell_count,
        best_buy=best_buy,
        best_sell=best_sell,
    )


def _levels(value: object, code: str) -> tuple[OrderBookLevel, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_LEVELS_PER_SIDE
    ):
        raise ManualOrderBookValidationError(code)
    result: list[OrderBookLevel] = []
    for level in value:
        if not isinstance(level, list) or len(level) != 2:
            raise ManualOrderBookValidationError("order_book_level_invalid")
        price_raw = _positive_js_safe_integer(level[0])
        quantity = _positive_js_safe_integer(level[1])
        result.append(OrderBookLevel(price_raw=price_raw, quantity=quantity))
    return tuple(result)


def _parse_reported_depth(value: object) -> ReportedDepth:
    depth = _exact_object(
        value,
        {"BUY", "SELL"},
        "reported_depth_keys_invalid",
    )
    return ReportedDepth(
        buy=_integer(
            depth["BUY"],
            "reported_depth_invalid",
            minimum=0,
            maximum=JS_SAFE_INTEGER_MAX,
        ),
        sell=_integer(
            depth["SELL"],
            "reported_depth_invalid",
            minimum=0,
            maximum=JS_SAFE_INTEGER_MAX,
        ),
    )


def _parse_best_level(value: object, code: str) -> BestLevel:
    best = _exact_object(value, {"price_raw", "quantity"}, code)
    return BestLevel(
        price_raw=_positive_js_safe_integer(best["price_raw"]),
        quantity=_positive_js_safe_integer(best["quantity"]),
    )


def _positive_js_safe_integer(value: object) -> int:
    return _integer(
        value,
        "order_book_integer_invalid",
        minimum=1,
        maximum=JS_SAFE_INTEGER_MAX,
    )


def _parse_source(value: object) -> SourceProvenance:
    source = _exact_object(
        value,
        {
            "capture_method",
            "raw_response_sha256",
            "normalized_capture_fingerprint",
        },
        "source_keys_invalid",
    )
    if source["capture_method"] != CAPTURE_METHOD:
        raise ManualOrderBookValidationError("capture_method_invalid")
    raw_hash = source["raw_response_sha256"]
    fingerprint = source["normalized_capture_fingerprint"]
    if (
        not isinstance(raw_hash, str)
        or not _SHA256_RE.fullmatch(raw_hash)
        or not isinstance(fingerprint, str)
        or not _SHA256_RE.fullmatch(fingerprint)
    ):
        raise ManualOrderBookValidationError("source_hash_invalid")
    return SourceProvenance(
        capture_method=CAPTURE_METHOD,
        raw_response_sha256=raw_hash,
        normalized_capture_fingerprint=fingerprint,
    )


def _parse_review(value: object) -> ReviewState:
    review = _exact_object(
        value,
        {"status", "fill_claim", "requires_manual_review"},
        "review_keys_invalid",
    )
    status = review["status"]
    if not isinstance(status, str):
        raise ManualOrderBookValidationError("review_status_invalid")
    fill_claim = review["fill_claim"]
    requires_manual_review = review["requires_manual_review"]
    if not isinstance(fill_claim, bool):
        raise ManualOrderBookValidationError("fill_claim_invalid")
    if not isinstance(requires_manual_review, bool):
        raise ManualOrderBookValidationError(
            "requires_manual_review_invalid"
        )
    return ReviewState(
        status=status,
        fill_claim=fill_claim,
        requires_manual_review=requires_manual_review,
    )


def _exact_object(
    value: object,
    expected_keys: set[str],
    code: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ManualOrderBookValidationError(code)
    return value


def _integer(
    value: object,
    code: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ManualOrderBookValidationError(code)
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one local Round 6 manual order-book JSON export offline. "
            "This is a validator, not a database importer or import dry-run."
        )
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Explicit local JSON file to validate; no network access is used.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        capture = parse_manual_order_book_json(
            read_manual_order_book_file(args.file)
        )
    except ManualOrderBookValidationError as exc:
        print(
            json.dumps(
                {
                    "validation_status": "invalid",
                    "error_code": exc.code,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            validation_summary(capture),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
