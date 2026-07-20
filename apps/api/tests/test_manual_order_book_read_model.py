from __future__ import annotations

import ast
import hashlib
import json
import sys
from dataclasses import replace
from decimal import ROUND_DOWN, localcontext
from pathlib import Path
from typing import Callable

import pytest

import api.pricing as pricing_api
import api.pricing.manual_order_book_read_model as read_model_module
from api import manual_order_book_price_cli as cli
from api.importers.manual_order_book_json import (
    MAX_DOCUMENT_BYTES,
    ManualOrderBookValidationError,
    compute_normalized_capture_fingerprint,
    parse_manual_order_book_json,
)
from api.pricing.manual_order_book_price import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    EVIDENCE_LEVEL,
    EVIDENCE_PACKAGE_SHA256,
    MAX_EXACT_SAVED_FORMATTER_RAW,
    PriceInterpretationError,
)
from api.pricing.manual_order_book_read_model import (
    INTEGRATION_AUDIT_PACKAGE_SHA256,
    READ_MODEL_IMPLEMENTATION_VERSION,
    READ_MODEL_SCHEMA_VERSION,
    ManualOrderBookReadModelError,
    _ManualOrderBookPriceReadModel,
    _build_manual_order_book_price_read_model,
    _serialize_manual_order_book_price_read_model,
    render_manual_order_book_price_read_model,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "manual-orderbook"
    / "confirmed-orderbook-capture.json"
)
READ_MODEL_MODULE = (
    Path(__file__).parents[1]
    / "src"
    / "api"
    / "pricing"
    / "manual_order_book_read_model.py"
)
CLI_MODULE = (
    Path(__file__).parents[1]
    / "src"
    / "api"
    / "manual_order_book_price_cli.py"
)

P2_OBSERVED_CASES = [
    ("Excelsior", "BUY", 1_050_100, "105.01"),
    ("Excelsior", "BUY", 1_050_000, "105.00"),
    ("Excelsior", "BUY", 1_031_000, "103.10"),
    ("Excelsior", "BUY", 1_030_000, "103.00"),
    ("Excelsior", "BUY", 900_000, "90.00"),
    ("Excelsior", "SELL", 1_390_000, "139.00"),
    ("Excelsior", "SELL", 1_395_000, "139.50"),
    ("Excelsior", "SELL", 1_399_800, "139.98"),
    ("Excelsior", "SELL", 1_400_000, "140.00"),
    ("Excelsior", "SELL", 1_439_900, "143.99"),
    ("Merkava Mk.3D", "BUY", 818_600, "81.86"),
    ("Merkava Mk.3D", "BUY", 818_500, "81.85"),
    ("Merkava Mk.3D", "BUY", 817_500, "81.75"),
    ("Merkava Mk.3D", "BUY", 816_400, "81.64"),
    ("Merkava Mk.3D", "BUY", 805_000, "80.50"),
    ("Merkava Mk.3D", "SELL", 1_050_000, "105.00"),
    ("Merkava Mk.3D", "SELL", 1_069_000, "106.90"),
    ("Merkava Mk.3D", "SELL", 1_070_000, "107.00"),
    ("Merkava Mk.3D", "SELL", 1_096_200, "109.62"),
    ("Merkava Mk.3D", "SELL", 1_100_000, "110.00"),
]


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


class BytesSubclass(bytes):
    pass


def fixture_document() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def nested(document: dict[str, object], key: str) -> dict[str, object]:
    value = document[key]
    assert isinstance(value, dict)
    return value


def order_book(document: dict[str, object]) -> dict[str, object]:
    return nested(document, "order_book")


def resign(document: dict[str, object]) -> bytes:
    source = nested(document, "source")
    source["normalized_capture_fingerprint"] = (
        compute_normalized_capture_fingerprint(document)
    )
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def mutated(callback: Callable[[dict[str, object]], None]) -> bytes:
    document = fixture_document()
    callback(document)
    return resign(document)


def build(
    content: bytes | str | None = None,
    *,
    contract_id: object = CONTRACT_ID,
    contract_version: object = CONTRACT_VERSION,
    display_currency: object = "GJN",
    side: object = None,
    level_index: object = None,
):
    return _build_manual_order_book_price_read_model(
        FIXTURE.read_bytes() if content is None else content,
        contract_id=contract_id,  # type: ignore[arg-type]
        contract_version=contract_version,  # type: ignore[arg-type]
        display_currency=display_currency,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        level_index=level_index,  # type: ignore[arg-type]
    )


def render(
    content: object | None = None,
    *,
    contract_id: object = CONTRACT_ID,
    contract_version: object = CONTRACT_VERSION,
    display_currency: object = "GJN",
    side: object = None,
    level_index: object = None,
) -> bytes:
    return render_manual_order_book_price_read_model(
        FIXTURE.read_bytes() if content is None else content,  # type: ignore[arg-type]
        contract_id=contract_id,  # type: ignore[arg-type]
        contract_version=contract_version,  # type: ignore[arg-type]
        display_currency=display_currency,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        level_index=level_index,  # type: ignore[arg-type]
    )


def assert_read_model_error(code: str, callback: Callable[[], object]) -> None:
    with pytest.raises(ManualOrderBookReadModelError) as exc_info:
        callback()
    assert exc_info.value.code == code
    assert str(exc_info.value) == code


def assert_parser_error(code: str, callback: Callable[[], object]) -> None:
    with pytest.raises(ManualOrderBookValidationError) as exc_info:
        callback()
    assert exc_info.value.code == code


def test_golden_fixture_all_levels_is_complete_and_ordered() -> None:
    model = build()
    assert model.schema_version == READ_MODEL_SCHEMA_VERSION
    assert model.validation_status == "valid"
    assert model.selection.mode == "all_levels"
    assert model.selection.selected_level_count == 111
    assert len(model.levels) == 111
    assert [level.side for level in model.levels[:41]] == ["BUY"] * 41
    assert [level.side for level in model.levels[41:]] == ["SELL"] * 70
    assert [level.level_index for level in model.levels[:41]] == list(
        range(41)
    )
    assert [level.level_index for level in model.levels[41:]] == list(
        range(70)
    )


def test_all_levels_preserve_raw_prices_and_quantities() -> None:
    capture = parse_manual_order_book_json(FIXTURE.read_bytes())
    expected = [
        (side, index, level.price_raw, level.quantity)
        for side, levels in (
            ("BUY", capture.order_book.buy_levels),
            ("SELL", capture.order_book.sell_levels),
        )
        for index, level in enumerate(levels)
    ]
    actual = [
        (level.side, level.level_index, level.price_raw, level.quantity)
        for level in build().levels
    ]
    assert actual == expected


def test_duplicate_raw_prices_are_not_deduplicated_or_merged() -> None:
    def duplicate(document: dict[str, object]) -> None:
        levels = order_book(document)["buy_levels"]
        assert isinstance(levels, list)
        levels[10][0] = 500_000  # type: ignore[index]
        levels[11][0] = 500_000  # type: ignore[index]

    model = build(mutated(duplicate))
    duplicates = [
        level
        for level in model.levels
        if level.side == "BUY" and level.price_raw == 500_000
    ]
    assert [level.level_index for level in duplicates] == [10, 11]
    assert len(model.levels) == 111


@pytest.mark.parametrize(("side", "index"), [("BUY", 0), ("SELL", 69)])
def test_single_level_selector_is_side_local(side: str, index: int) -> None:
    model = build(side=side, level_index=index)
    assert model.selection.mode == "single_level"
    assert model.selection.side == side
    assert model.selection.level_index == index
    assert model.selection.selected_level_count == 1
    assert len(model.levels) == 1
    assert model.levels[0].side == side
    assert model.levels[0].level_index == index


@pytest.mark.parametrize(
    ("side", "index"),
    [("BUY", None), (None, 0)],
)
def test_selector_requires_side_and_index_together(
    side: object, index: object
) -> None:
    assert_read_model_error(
        "price_level_selector_incomplete",
        lambda: build(side=side, level_index=index),
    )


@pytest.mark.parametrize("side", [1, True, StrSubclass("BUY")])
def test_selector_side_requires_exact_builtin_string(side: object) -> None:
    assert_read_model_error(
        "price_level_selector_side_type_invalid",
        lambda: build(side=side, level_index=0),
    )


def test_selector_side_value_is_exact() -> None:
    assert_read_model_error(
        "price_level_selector_side_unsupported",
        lambda: build(side="buy", level_index=0),
    )


@pytest.mark.parametrize(
    "index",
    [True, 1.0, "1", IntSubclass(1)],
)
def test_selector_index_requires_exact_builtin_integer(index: object) -> None:
    assert_read_model_error(
        "price_level_selector_index_type_invalid",
        lambda: build(side="BUY", level_index=index),
    )


@pytest.mark.parametrize(("side", "index"), [("BUY", -1), ("SELL", 70)])
def test_selector_index_never_falls_back(side: str, index: int) -> None:
    assert_read_model_error(
        "price_level_selector_index_out_of_range",
        lambda: build(side=side, level_index=index),
    )


def test_render_parses_and_checks_applicability_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parse_calls = 0
    applicability_calls = 0
    real_parse = read_model_module.parse_manual_order_book_json
    real_applicability = read_model_module._validate_contract_applicability

    def counted_parse(content: bytes | str):
        nonlocal parse_calls
        parse_calls += 1
        assert type(content) is bytes
        return real_parse(content)

    def counted_applicability(*args, **kwargs) -> None:
        nonlocal applicability_calls
        applicability_calls += 1
        real_applicability(*args, **kwargs)

    monkeypatch.setattr(
        read_model_module,
        "parse_manual_order_book_json",
        counted_parse,
    )
    monkeypatch.setattr(
        read_model_module,
        "_validate_contract_applicability",
        counted_applicability,
    )
    payload = json.loads(render())
    assert len(payload["levels"]) == 111
    assert parse_calls == 1
    assert applicability_calls == 1


def test_constructed_capture_cannot_enter_public_boundary() -> None:
    capture = parse_manual_order_book_json(FIXTURE.read_bytes())
    assert_parser_error(
        "document_type_invalid",
        lambda: render_manual_order_book_price_read_model(
            capture,  # type: ignore[arg-type]
            contract_id=CONTRACT_ID,
            contract_version=CONTRACT_VERSION,
            display_currency="GJN",
        ),
    )


@pytest.mark.parametrize(
    "excluded",
    [
        "ManualOrderBookPriceReadModel",
        "build_manual_order_book_price_read_model",
        "serialize_manual_order_book_price_read_model",
    ],
)
def test_package_does_not_export_forgeable_read_model_api(
    excluded: str,
) -> None:
    assert excluded not in pricing_api.__all__
    assert not hasattr(pricing_api, excluded)


def test_package_exports_one_shot_render() -> None:
    assert "render_manual_order_book_price_read_model" in pricing_api.__all__
    assert (
        pricing_api.render_manual_order_book_price_read_model
        is render_manual_order_book_price_read_model
    )


def test_manually_constructed_model_cannot_enter_public_render() -> None:
    model = build()
    constructed = _ManualOrderBookPriceReadModel(
        schema_version=model.schema_version,
        validation_status=model.validation_status,
        contract=model.contract,
        evidence=model.evidence,
        capture=model.capture,
        selection=model.selection,
        levels=model.levels,
    )
    assert_parser_error(
        "document_type_invalid",
        lambda: render(constructed),
    )


@pytest.mark.parametrize(
    "forgery",
    [
        "capture_id",
        "source_file_sha256",
        "normalized_capture_fingerprint",
        "validation_status",
    ],
)
def test_replaced_model_cannot_enter_public_render(forgery: str) -> None:
    model = build()
    if forgery == "validation_status":
        forged = replace(model, validation_status="forged")
    else:
        forged = replace(
            model,
            capture=replace(model.capture, **{forgery: "0" * 64}),
        )
    assert_parser_error(
        "document_type_invalid",
        lambda: render(forged),
    )


@pytest.mark.parametrize(
    ("side", "level_index", "expected_sha256"),
    [
        (
            None,
            None,
            "0e30bb0ea431825fe761a4bea0e4e4939d2f26f25cbf2acd9cbe855275b88d3e",
        ),
        (
            "BUY",
            0,
            "b954ec93c9eaec8e49923329ad0632527f42611572a59d19e2baeff4360ad462",
        ),
    ],
)
def test_one_shot_render_preserves_sample_bytes(
    side: str | None,
    level_index: int | None,
    expected_sha256: str,
) -> None:
    output = render(side=side, level_index=level_index)
    assert hashlib.sha256(output).hexdigest() == expected_sha256


@pytest.mark.parametrize(
    "content",
    [
        BytesSubclass(FIXTURE.read_bytes()),
        StrSubclass(FIXTURE.read_text(encoding="utf-8")),
        bytearray(FIXTURE.read_bytes()),
        memoryview(FIXTURE.read_bytes()),
    ],
)
def test_document_requires_exact_builtin_bytes_or_str(content: object) -> None:
    assert_parser_error(
        "document_type_invalid",
        lambda: render(content),
    )


def test_rewriting_bytes_decode_cannot_split_capture_and_hash() -> None:
    class RewritingBytes(bytes):
        def decode(self, *args, **kwargs) -> str:
            raise AssertionError("subclass decode must not be called")

    assert_parser_error(
        "document_type_invalid",
        lambda: render(RewritingBytes(FIXTURE.read_bytes())),
    )


def test_rewriting_str_encode_cannot_split_capture_and_hash() -> None:
    class RewritingStr(str):
        def encode(self, *args, **kwargs) -> bytes:
            raise AssertionError("subclass encode must not be called")

    assert_parser_error(
        "document_type_invalid",
        lambda: render(RewritingStr(FIXTURE.read_text(encoding="utf-8"))),
    )


@pytest.mark.parametrize("kind", ["bytes", "str"])
def test_stateful_subclass_conversion_never_enters_public_boundary(
    kind: str,
) -> None:
    calls = 0

    class StatefulBytes(bytes):
        def decode(self, *args, **kwargs) -> str:
            nonlocal calls
            calls += 1
            return super().decode(*args, **kwargs)

    class StatefulStr(str):
        def encode(self, *args, **kwargs) -> bytes:
            nonlocal calls
            calls += 1
            return super().encode(*args, **kwargs)

    content: object = (
        StatefulBytes(FIXTURE.read_bytes())
        if kind == "bytes"
        else StatefulStr(FIXTURE.read_text(encoding="utf-8"))
    )
    assert_parser_error(
        "document_type_invalid",
        lambda: render(content),
    )
    assert calls == 0


def test_exact_str_with_unencodable_surrogate_is_invalid_utf8() -> None:
    assert_parser_error("invalid_utf8", lambda: render("\ud800"))


def _p2_document(side: str, raw: int) -> bytes:
    document = fixture_document()
    book = order_book(document)
    if side == "BUY":
        levels = book["buy_levels"]
        assert isinstance(levels, list)
        levels[1][0] = raw  # type: ignore[index]
    else:
        buy_levels = book["buy_levels"]
        sell_levels = book["sell_levels"]
        assert isinstance(buy_levels, list)
        assert isinstance(sell_levels, list)
        for level in buy_levels:
            level[0] = 1  # type: ignore[index]
        best_buy = nested(book, "best_buy")
        best_buy["price_raw"] = 1
        best_buy["quantity"] = buy_levels[0][1]  # type: ignore[index]
        sell_levels[0][0] = raw  # type: ignore[index]
        best_sell = nested(book, "best_sell")
        best_sell["price_raw"] = raw
        best_sell["quantity"] = sell_levels[0][1]  # type: ignore[index]
    return resign(document)


@pytest.mark.parametrize(
    ("product", "side", "raw", "display"),
    P2_OBSERVED_CASES,
)
def test_all_p2_observations_remain_exact(
    product: str,
    side: str,
    raw: int,
    display: str,
) -> None:
    assert product
    index = 1 if side == "BUY" else 0
    level = build(
        _p2_document(side, raw),
        side=side,
        level_index=index,
    ).levels[0]
    assert level.price_raw == raw
    assert level.display_amount_gjn == display
    assert level.canonical_display_text == display


def test_bytes_hash_binds_exact_supplied_bytes() -> None:
    content = FIXTURE.read_bytes()
    payload = json.loads(render(content))
    assert payload["capture"]["source_file_sha256"] == hashlib.sha256(
        content
    ).hexdigest()
    assert payload["capture"]["source_file_sha256_representation"] == (
        "supplied_bytes"
    )


def test_text_hash_binds_supplied_utf8_representation() -> None:
    content = FIXTURE.read_text(encoding="utf-8")
    payload = json.loads(render(content))
    assert payload["capture"]["source_file_sha256"] == hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()
    assert payload["capture"]["source_file_sha256_representation"] == (
        "supplied_utf8"
    )


def test_capture_and_evidence_metadata_are_complete() -> None:
    model = build()
    parsed = parse_manual_order_book_json(FIXTURE.read_bytes())
    assert model.contract.contract_id == CONTRACT_ID
    assert model.contract.contract_version == CONTRACT_VERSION
    assert model.contract.currency_code == "GJN"
    assert model.contract.raw_scale == 10_000
    assert model.contract.display_decimal_places == 2
    assert (
        model.contract.display_safe_maximum_raw
        == MAX_EXACT_SAVED_FORMATTER_RAW
    )
    assert model.contract.evidence_level == EVIDENCE_LEVEL
    assert (
        model.evidence.price_contract_evidence_package_sha256
        == EVIDENCE_PACKAGE_SHA256
    )
    assert (
        model.evidence.integration_audit_package_sha256
        == INTEGRATION_AUDIT_PACKAGE_SHA256
    )
    assert (
        model.evidence.read_model_implementation_version
        == READ_MODEL_IMPLEMENTATION_VERSION
    )
    assert (
        model.capture.normalized_capture_fingerprint
        == parsed.source.normalized_capture_fingerprint
    )
    assert model.capture.request_action == "UNKNOWN"
    assert (
        model.capture.raw_response_sha256_claim
        == parsed.source.raw_response_sha256
    )
    assert model.capture.raw_response_hash_verifiable is False


def test_serialized_output_excludes_database_and_request_secrets() -> None:
    payload = json.loads(_serialize_manual_order_book_price_read_model(build()))
    text = json.dumps(payload, sort_keys=True).lower()
    for forbidden in (
        "database_item_id",
        "import_job_id",
        "authorization",
        "cookie",
        "request_payload",
        "account_id",
        "user_id",
    ):
        assert forbidden not in text


@pytest.mark.parametrize(
    ("field", "invalid", "code"),
    [
        ("contract_id", StrSubclass(CONTRACT_ID), "price_contract_id_type_invalid"),
        ("contract_id", "other", "price_contract_id_unsupported"),
        ("contract_version", IntSubclass(1), "price_contract_version_type_invalid"),
        ("contract_version", 2, "price_contract_version_unsupported"),
        ("display_currency", StrSubclass("GJN"), "display_currency_type_invalid"),
        ("display_currency", "USD", "display_currency_not_applicable"),
    ],
)
def test_contract_inputs_fail_closed(
    field: str, invalid: object, code: str
) -> None:
    kwargs = {field: invalid}
    with pytest.raises(PriceInterpretationError) as exc_info:
        build(**kwargs)
    assert exc_info.value.code == code


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (
            lambda document: nested(document, "review").__setitem__(
                "status", "pending"
            ),
            "review_status_not_importable",
        ),
        (
            lambda document: nested(document, "review").__setitem__(
                "requires_manual_review", True
            ),
            "manual_review_required",
        ),
    ],
)
def test_unconfirmed_documents_are_rejected(
    change: Callable[[dict[str, object]], None], code: str
) -> None:
    assert_parser_error(code, lambda: build(mutated(change)))


def test_crossed_document_is_rejected() -> None:
    def cross(document: dict[str, object]) -> None:
        book = order_book(document)
        best_sell = nested(book, "best_sell")
        sell_price = best_sell["price_raw"]
        assert isinstance(sell_price, int)
        crossed = sell_price + 1
        buy_levels = book["buy_levels"]
        assert isinstance(buy_levels, list)
        buy_levels[0][0] = crossed  # type: ignore[index]
        nested(book, "best_buy")["price_raw"] = crossed

    assert_parser_error(
        "crossed_order_book",
        lambda: build(mutated(cross)),
    )


@pytest.mark.parametrize("literal", ["bad%2Fkey", "bad%GG"])
def test_encoded_or_malformed_external_key_is_rejected(literal: str) -> None:
    def change(document: dict[str, object]) -> None:
        page = nested(document, "page")
        page["path"] = f"/market/1067/{literal}"
        page["external_key"] = literal

    expected = (
        "page_decoded_key_invalid"
        if literal == "bad%2Fkey"
        else "page_percent_encoding_invalid"
    )
    assert_parser_error(expected, lambda: build(mutated(change)))


def _document_with_selected_sell_raw(raw: int) -> bytes:
    def change(document: dict[str, object]) -> None:
        levels = order_book(document)["sell_levels"]
        assert isinstance(levels, list)
        levels[-1][0] = raw  # type: ignore[index]

    return mutated(change)


def test_display_safe_maximum_is_exact() -> None:
    level = build(
        _document_with_selected_sell_raw(MAX_EXACT_SAVED_FORMATTER_RAW),
        side="SELL",
        level_index=69,
    ).levels[0]
    assert level.price_raw == MAX_EXACT_SAVED_FORMATTER_RAW
    assert level.base_amount_gjn == "900719925474.0893"
    assert level.display_amount_gjn == "900719925474.09"


def test_display_safe_maximum_plus_one_fails_single_selection() -> None:
    with pytest.raises(PriceInterpretationError) as exc_info:
        build(
            _document_with_selected_sell_raw(
                MAX_EXACT_SAVED_FORMATTER_RAW + 1
            ),
            side="SELL",
            level_index=69,
        )
    assert exc_info.value.code == "price_raw_display_range_unsupported"


def test_all_levels_fail_as_one_when_any_level_exceeds_display_range() -> None:
    with pytest.raises(PriceInterpretationError) as exc_info:
        build(
            _document_with_selected_sell_raw(
                MAX_EXACT_SAVED_FORMATTER_RAW + 1
            )
        )
    assert exc_info.value.code == "price_raw_display_range_unsupported"


def test_single_selection_does_not_interpret_unselected_unsupported_level() -> None:
    model = build(
        _document_with_selected_sell_raw(
            MAX_EXACT_SAVED_FORMATTER_RAW + 1
        ),
        side="BUY",
        level_index=0,
    )
    assert len(model.levels) == 1
    assert model.levels[0].side == "BUY"


def test_serializer_is_byte_deterministic_and_has_one_lf() -> None:
    first = _serialize_manual_order_book_price_read_model(build())
    second = _serialize_manual_order_book_price_read_model(build())
    assert first == second
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")
    assert not first.startswith(b"\xef\xbb\xbf")


def test_decimal_context_does_not_change_model_or_serialization() -> None:
    expected = _serialize_manual_order_book_price_read_model(
        build(side="BUY", level_index=0)
    )
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        actual = _serialize_manual_order_book_price_read_model(
            build(side="BUY", level_index=0)
        )
    assert actual == expected


def test_input_bytes_and_fingerprint_are_not_modified() -> None:
    content = FIXTURE.read_bytes()
    before_bytes = bytes(content)
    before_fingerprint = parse_manual_order_book_json(
        content
    ).source.normalized_capture_fingerprint
    build(content)
    after_fingerprint = parse_manual_order_book_json(
        content
    ).source.normalized_capture_fingerprint
    assert content == before_bytes
    assert after_fingerprint == before_fingerprint


def _cli_args(path: Path) -> list[str]:
    return [
        str(path),
        "--contract-id",
        CONTRACT_ID,
        "--contract-version",
        "1",
        "--currency",
        "GJN",
    ]


def test_cli_all_levels_success(
    capfd: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(_cli_args(FIXTURE)) == 0
    captured = capfd.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["selection"] == {
        "mode": "all_levels",
        "selected_level_count": 111,
    }
    assert len(payload["levels"]) == 111


def test_cli_single_selector_success(
    capfd: pytest.CaptureFixture[str],
) -> None:
    args = _cli_args(FIXTURE) + ["--side", "SELL", "--level-index", "1"]
    assert cli.main(args) == 0
    captured = capfd.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["selection"] == {
        "level_index": 1,
        "mode": "single_level",
        "selected_level_count": 1,
        "side": "SELL",
    }


def test_cli_calls_only_one_shot_render(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    calls = 0
    real_render = cli.render_manual_order_book_price_read_model

    def counted_render(*args, **kwargs) -> bytes:
        nonlocal calls
        calls += 1
        return real_render(*args, **kwargs)

    monkeypatch.setattr(
        cli,
        "render_manual_order_book_price_read_model",
        counted_render,
    )
    assert cli.main(_cli_args(FIXTURE)) == 0
    captured = capfd.readouterr()
    assert captured.err == ""
    assert calls == 1
    source = CLI_MODULE.read_text(encoding="utf-8")
    assert "_build_manual_order_book_price_read_model" not in source
    assert "_serialize_manual_order_book_price_read_model" not in source


def test_cli_oversized_file_is_narrow_invalid_input(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "oversized-private.json"
    path.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 1))
    assert cli.main(_cli_args(path)) == cli.EXIT_INVALID_INPUT
    captured = capfd.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "file_too_large",
        "error_type": "invalid_input",
    }
    assert str(path) not in captured.err


def test_cli_missing_file_is_narrow_operational_error(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "missing-sensitive.json"
    assert cli.main(_cli_args(path)) == cli.EXIT_OPERATIONAL_ERROR
    captured = capfd.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "file_not_found",
        "error_type": "operational_error",
    }
    assert str(path) not in captured.err
    assert "Traceback" not in captured.err


def test_cli_invalid_document_does_not_leak_path_body_or_traceback(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "private-capture.json"
    sentinel = "must-not-be-echoed"
    path.write_text(json.dumps({"payload": sentinel}), encoding="utf-8")
    assert cli.main(_cli_args(path)) == cli.EXIT_INVALID_INPUT
    captured = capfd.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "top_level_keys_invalid",
        "error_type": "invalid_input",
    }
    assert str(path) not in captured.err
    assert sentinel not in captured.err
    assert "Traceback" not in captured.err


def test_cli_requires_explicit_contract_inputs(
    capfd: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([str(FIXTURE)]) == cli.EXIT_INVALID_INPUT
    captured = capfd.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "cli_arguments_invalid",
        "error_type": "invalid_input",
    }


@pytest.mark.parametrize(
    ("argument", "value", "error_code"),
    [
        (
            "--contract-version",
            "9" * 5_000,
            "price_contract_version_type_invalid",
        ),
        (
            "--level-index",
            "9" * 5_000,
            "price_level_selector_index_type_invalid",
        ),
    ],
)
def test_cli_excessively_long_integer_is_narrow_invalid_input(
    argument: str,
    value: str,
    error_code: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    before_limit = sys.get_int_max_str_digits()
    args = _cli_args(FIXTURE)
    if argument == "--contract-version":
        args[args.index(argument) + 1] = value
    else:
        args += ["--side", "BUY", argument, value]
    assert cli.main(args) == cli.EXIT_INVALID_INPUT
    captured = capfd.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": error_code,
        "error_type": "invalid_input",
    }
    assert "Traceback" not in captured.err
    assert "digit" not in captured.err.lower()
    assert str(CLI_MODULE) not in captured.err
    assert sys.get_int_max_str_digits() == before_limit


def test_cli_unsupported_contract_version_remains_contract_error(
    capfd: pytest.CaptureFixture[str],
) -> None:
    args = _cli_args(FIXTURE)
    args[args.index("--contract-version") + 1] = "2"
    assert cli.main(args) == cli.EXIT_INVALID_INPUT
    captured = capfd.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "price_contract_version_unsupported",
        "error_type": "invalid_input",
    }


def test_cli_out_of_range_index_remains_selector_error(
    capfd: pytest.CaptureFixture[str],
) -> None:
    args = _cli_args(FIXTURE) + [
        "--side",
        "BUY",
        "--level-index",
        "41",
    ]
    assert cli.main(args) == cli.EXIT_INVALID_INPUT
    captured = capfd.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "price_level_selector_index_out_of_range",
        "error_type": "invalid_input",
    }


def test_integer_argument_maps_overflow_to_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def overflow(_value: str) -> int:
        raise OverflowError

    monkeypatch.setattr(cli, "int", overflow, raising=False)
    with pytest.raises(cli._CliArgumentError) as exc_info:
        cli._integer_argument("1", "stable_integer_error")
    assert exc_info.value.code == "stable_integer_error"


def test_read_model_and_cli_have_no_database_network_or_environment_imports() -> None:
    forbidden = {
        "httpx",
        "os",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "urllib",
    }
    for path in (READ_MODEL_MODULE, CLI_MODULE):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        assert imports.isdisjoint(forbidden)
        assert "api.db" not in source
        assert "environ" not in source
        assert "getenv" not in source
