"""Database-free bulk read model for the approved current-book contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from api.importers.manual_order_book_json import (
    ManualOrderBookCapture,
    ManualOrderBookValidationError,
    OrderBookLevel,
    parse_manual_order_book_json,
)
from api.pricing.manual_order_book_price import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    CURRENCY_CODE,
    DISPLAY_DECIMAL_PLACES,
    EVIDENCE_LEVEL,
    EVIDENCE_PACKAGE_SHA256,
    MAX_EXACT_SAVED_FORMATTER_RAW,
    RAW_SCALE,
    _interpret_applicable_price_raw,
    _validate_contract_applicability,
)


READ_MODEL_SCHEMA_VERSION = "round6_manual_order_book_price_read_model_v1"
READ_MODEL_IMPLEMENTATION_VERSION = "round6d_p3c_v1"
INTEGRATION_AUDIT_PACKAGE_SHA256 = (
    "302a9194d07c11575a0be8f844c4cb8d4ce4d0cd5396e738ecc3e586f6ea78d5"
)
REQUEST_ACTION = "UNKNOWN"


class ManualOrderBookReadModelError(ValueError):
    """Stable selector failure without document contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PriceReadModelContract:
    contract_id: str
    contract_version: int
    currency_code: str
    raw_scale: int
    display_decimal_places: int
    display_safe_maximum_raw: int
    evidence_level: str


@dataclass(frozen=True, slots=True)
class PriceReadModelEvidence:
    price_contract_evidence_package_sha256: str
    integration_audit_package_sha256: str
    read_model_implementation_version: str


@dataclass(frozen=True, slots=True)
class PriceReadModelCapture:
    source_capture_schema_version: str
    capture_id: str
    captured_at: str
    timezone_offset_minutes: int
    page_origin: str
    page_path: str
    literal_external_key: str
    decoded_external_key: str
    request_method: str
    request_origin: str
    request_path: str
    request_action: str
    capture_method: str
    normalized_capture_fingerprint: str
    source_file_sha256: str
    source_file_sha256_representation: str
    raw_response_sha256_claim: str
    raw_response_hash_verifiable: bool
    review_status: str


@dataclass(frozen=True, slots=True)
class PriceReadModelSelection:
    mode: str
    selected_level_count: int
    side: str | None = None
    level_index: int | None = None


@dataclass(frozen=True, slots=True)
class PriceReadModelLevel:
    side: str
    level_index: int
    quantity: int
    price_raw: int
    base_amount_gjn: str
    display_amount_gjn: str
    canonical_display_text: str


@dataclass(frozen=True, slots=True)
class _ManualOrderBookPriceReadModel:
    schema_version: str
    validation_status: str
    contract: PriceReadModelContract
    evidence: PriceReadModelEvidence
    capture: PriceReadModelCapture
    selection: PriceReadModelSelection
    levels: tuple[PriceReadModelLevel, ...]


def render_manual_order_book_price_read_model(
    content: bytes | str,
    *,
    contract_id: str,
    contract_version: int,
    display_currency: str,
    side: str | None = None,
    level_index: int | None = None,
) -> bytes:
    """Render canonical evidence JSON from one raw strict document."""

    model = _build_manual_order_book_price_read_model(
        content,
        contract_id=contract_id,
        contract_version=contract_version,
        display_currency=display_currency,
        side=side,
        level_index=level_index,
    )
    return _serialize_manual_order_book_price_read_model(model)


def _build_manual_order_book_price_read_model(
    content: bytes | str,
    *,
    contract_id: str,
    contract_version: int,
    display_currency: str,
    side: str | None = None,
    level_index: int | None = None,
) -> _ManualOrderBookPriceReadModel:
    """Parse and validate once, then interpret explicitly selected levels."""

    source_bytes, representation = _normalize_document_input(content)
    capture = parse_manual_order_book_json(source_bytes)
    _validate_contract_applicability(
        capture,
        display_currency=display_currency,
        contract_id=contract_id,
        contract_version=contract_version,
    )
    selected, selection = _select_levels(
        capture,
        side=side,
        level_index=level_index,
    )
    interpreted_levels = tuple(
        _interpret_level(selected_side, selected_index, level)
        for selected_side, selected_index, level in selected
    )
    return _ManualOrderBookPriceReadModel(
        schema_version=READ_MODEL_SCHEMA_VERSION,
        validation_status="valid",
        contract=PriceReadModelContract(
            contract_id=CONTRACT_ID,
            contract_version=CONTRACT_VERSION,
            currency_code=CURRENCY_CODE,
            raw_scale=RAW_SCALE,
            display_decimal_places=DISPLAY_DECIMAL_PLACES,
            display_safe_maximum_raw=MAX_EXACT_SAVED_FORMATTER_RAW,
            evidence_level=EVIDENCE_LEVEL,
        ),
        evidence=PriceReadModelEvidence(
            price_contract_evidence_package_sha256=(
                EVIDENCE_PACKAGE_SHA256
            ),
            integration_audit_package_sha256=(
                INTEGRATION_AUDIT_PACKAGE_SHA256
            ),
            read_model_implementation_version=(
                READ_MODEL_IMPLEMENTATION_VERSION
            ),
        ),
        capture=_capture_metadata(
            capture,
            source_file_sha256=hashlib.sha256(source_bytes).hexdigest(),
            source_file_sha256_representation=representation,
        ),
        selection=selection,
        levels=interpreted_levels,
    )


def _serialize_manual_order_book_price_read_model(
    model: _ManualOrderBookPriceReadModel,
) -> bytes:
    """Return the one canonical UTF-8 JSON representation with one final LF."""

    payload = {
        "capture": {
            "capture_id": model.capture.capture_id,
            "capture_method": model.capture.capture_method,
            "captured_at": model.capture.captured_at,
            "decoded_external_key": model.capture.decoded_external_key,
            "literal_external_key": model.capture.literal_external_key,
            "normalized_capture_fingerprint": (
                model.capture.normalized_capture_fingerprint
            ),
            "page_origin": model.capture.page_origin,
            "page_path": model.capture.page_path,
            "raw_response_hash_verifiable": (
                model.capture.raw_response_hash_verifiable
            ),
            "raw_response_sha256_claim": (
                model.capture.raw_response_sha256_claim
            ),
            "request_action": model.capture.request_action,
            "request_method": model.capture.request_method,
            "request_origin": model.capture.request_origin,
            "request_path": model.capture.request_path,
            "review_status": model.capture.review_status,
            "source_capture_schema_version": (
                model.capture.source_capture_schema_version
            ),
            "source_file_sha256": model.capture.source_file_sha256,
            "source_file_sha256_representation": (
                model.capture.source_file_sha256_representation
            ),
            "timezone_offset_minutes": (
                model.capture.timezone_offset_minutes
            ),
        },
        "contract": {
            "contract_id": model.contract.contract_id,
            "contract_version": model.contract.contract_version,
            "currency_code": model.contract.currency_code,
            "display_decimal_places": (
                model.contract.display_decimal_places
            ),
            "display_safe_maximum_raw": (
                model.contract.display_safe_maximum_raw
            ),
            "evidence_level": model.contract.evidence_level,
            "raw_scale": model.contract.raw_scale,
        },
        "evidence": {
            "integration_audit_package_sha256": (
                model.evidence.integration_audit_package_sha256
            ),
            "price_contract_evidence_package_sha256": (
                model.evidence.price_contract_evidence_package_sha256
            ),
            "read_model_implementation_version": (
                model.evidence.read_model_implementation_version
            ),
        },
        "levels": [
            {
                "base_amount_gjn": level.base_amount_gjn,
                "canonical_display_text": level.canonical_display_text,
                "display_amount_gjn": level.display_amount_gjn,
                "level_index": level.level_index,
                "price_raw": level.price_raw,
                "quantity": level.quantity,
                "side": level.side,
            }
            for level in model.levels
        ],
        "schema_version": model.schema_version,
        "selection": _selection_payload(model.selection),
        "validation_status": model.validation_status,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _select_levels(
    capture: ManualOrderBookCapture,
    *,
    side: str | None,
    level_index: int | None,
) -> tuple[
    tuple[tuple[str, int, OrderBookLevel], ...],
    PriceReadModelSelection,
]:
    if side is None and level_index is None:
        selected = tuple(
            (selected_side, index, level)
            for selected_side, levels in (
                ("BUY", capture.order_book.buy_levels),
                ("SELL", capture.order_book.sell_levels),
            )
            for index, level in enumerate(levels)
        )
        return selected, PriceReadModelSelection(
            mode="all_levels",
            selected_level_count=len(selected),
        )
    if side is None or level_index is None:
        raise ManualOrderBookReadModelError(
            "price_level_selector_incomplete"
        )
    if type(side) is not str:
        raise ManualOrderBookReadModelError(
            "price_level_selector_side_type_invalid"
        )
    if side not in {"BUY", "SELL"}:
        raise ManualOrderBookReadModelError(
            "price_level_selector_side_unsupported"
        )
    if type(level_index) is not int:
        raise ManualOrderBookReadModelError(
            "price_level_selector_index_type_invalid"
        )
    levels = (
        capture.order_book.buy_levels
        if side == "BUY"
        else capture.order_book.sell_levels
    )
    if level_index < 0 or level_index >= len(levels):
        raise ManualOrderBookReadModelError(
            "price_level_selector_index_out_of_range"
        )
    selected = ((side, level_index, levels[level_index]),)
    return selected, PriceReadModelSelection(
        mode="single_level",
        side=side,
        level_index=level_index,
        selected_level_count=1,
    )


def _interpret_level(
    side: str,
    level_index: int,
    level: OrderBookLevel,
) -> PriceReadModelLevel:
    interpreted = _interpret_applicable_price_raw(level.price_raw)
    return PriceReadModelLevel(
        side=side,
        level_index=level_index,
        quantity=level.quantity,
        price_raw=level.price_raw,
        base_amount_gjn=str(interpreted.base_amount_gjn),
        display_amount_gjn=str(interpreted.display_amount_gjn),
        canonical_display_text=interpreted.canonical_display_text,
    )


def _normalize_document_input(content: bytes | str) -> tuple[bytes, str]:
    if type(content) is bytes:
        return content, "supplied_bytes"
    if type(content) is str:
        try:
            return content.encode("utf-8", errors="strict"), "supplied_utf8"
        except UnicodeEncodeError as exc:
            raise ManualOrderBookValidationError("invalid_utf8") from exc
    raise ManualOrderBookValidationError("document_type_invalid")


def _capture_metadata(
    capture: ManualOrderBookCapture,
    *,
    source_file_sha256: str,
    source_file_sha256_representation: str,
) -> PriceReadModelCapture:
    return PriceReadModelCapture(
        source_capture_schema_version=capture.schema_version,
        capture_id=capture.capture_id,
        captured_at=capture.captured_at,
        timezone_offset_minutes=capture.timezone_offset_minutes,
        page_origin=capture.page.origin,
        page_path=capture.page.path,
        literal_external_key=capture.page.literal_external_key,
        decoded_external_key=capture.page.decoded_external_key,
        request_method=capture.request.method,
        request_origin=capture.request.origin,
        request_path=capture.request.path,
        request_action=REQUEST_ACTION,
        capture_method=capture.source.capture_method,
        normalized_capture_fingerprint=(
            capture.source.normalized_capture_fingerprint
        ),
        source_file_sha256=source_file_sha256,
        source_file_sha256_representation=(
            source_file_sha256_representation
        ),
        raw_response_sha256_claim=capture.source.raw_response_sha256,
        raw_response_hash_verifiable=False,
        review_status=capture.review.status,
    )


def _selection_payload(
    selection: PriceReadModelSelection,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": selection.mode,
        "selected_level_count": selection.selected_level_count,
    }
    if selection.mode == "single_level":
        payload["level_index"] = selection.level_index
        payload["side"] = selection.side
    return payload
