from __future__ import annotations

import ast
import json
from dataclasses import replace
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    Decimal,
    localcontext,
)
from pathlib import Path
from typing import Callable

import pytest

from api.importers.manual_order_book_json import (
    JS_SAFE_INTEGER_MAX,
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
    ManualOrderBookPriceInterpretation,
    PriceInterpretationError,
    interpret_current_order_book_document,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "manual-orderbook"
    / "confirmed-orderbook-capture.json"
)
MODULE = (
    Path(__file__).parents[1]
    / "src"
    / "api"
    / "pricing"
    / "manual_order_book_price.py"
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


def fixture_document() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def nested(document: dict[str, object], key: str) -> dict[str, object]:
    value = document[key]
    assert isinstance(value, dict)
    return value


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


def interpret(
    raw: int,
    content: bytes | str | None = None,
    *,
    display_currency: object = "GJN",
    contract_id: object = CONTRACT_ID,
    contract_version: object = CONTRACT_VERSION,
) -> ManualOrderBookPriceInterpretation:
    return interpret_current_order_book_document(
        FIXTURE.read_bytes() if content is None else content,
        raw,
        display_currency=display_currency,  # type: ignore[arg-type]
        contract_id=contract_id,  # type: ignore[arg-type]
        contract_version=contract_version,  # type: ignore[arg-type]
    )


def assert_price_error(code: str, callback: Callable[[], object]) -> None:
    with pytest.raises(PriceInterpretationError) as exc_info:
        callback()
    assert exc_info.value.code == code
    assert str(exc_info.value) == code


def assert_parser_error(code: str, callback: Callable[[], object]) -> None:
    with pytest.raises(ManualOrderBookValidationError) as exc_info:
        callback()
    assert exc_info.value.code == code
    assert str(exc_info.value) == code


@pytest.mark.parametrize(("product", "side", "raw", "display"), P2_OBSERVED_CASES)
def test_all_p2_observed_prices_match_saved_formatter(
    product: str, side: str, raw: int, display: str
) -> None:
    assert product and side in {"BUY", "SELL"}
    result = interpret(raw)
    assert result.canonical_display_text == display
    assert result.display_amount_gjn == Decimal(display)


@pytest.mark.parametrize(
    ("raw", "base", "minor", "display"),
    [
        (1, "0.0001", 1, "0.01"),
        (99, "0.0099", 1, "0.01"),
        (100, "0.01", 1, "0.01"),
        (101, "0.0101", 2, "0.02"),
        (9_999, "0.9999", 100, "1.00"),
        (10_000, "1", 100, "1.00"),
        (1_050_001, "105.0001", 10_501, "105.01"),
        (1_050_099, "105.0099", 10_501, "105.01"),
    ],
)
def test_exact_base_and_upward_cent_display(
    raw: int, base: str, minor: int, display: str
) -> None:
    result = interpret(raw)
    assert result.base_amount_gjn == Decimal(base)
    assert result.display_minor_units == minor
    assert result.display_amount_gjn == Decimal(display)
    assert result.canonical_display_text == display


def test_typed_output_contains_stable_contract_and_evidence() -> None:
    result = interpret(1_050_100)
    assert result == ManualOrderBookPriceInterpretation(
        contract_id=CONTRACT_ID,
        contract_version=1,
        price_raw=1_050_100,
        raw_scale=10_000,
        currency_code="GJN",
        base_amount_gjn=Decimal("105.01"),
        display_minor_units=10_501,
        display_amount_gjn=Decimal("105.01"),
        canonical_display_text="105.01",
        display_decimal_places=2,
        evidence_level=EVIDENCE_LEVEL,
        evidence_package_sha256=EVIDENCE_PACKAGE_SHA256,
    )


def test_contract_version_one_is_accepted() -> None:
    assert interpret(1, contract_version=1).contract_version == 1


def test_int_subclass_contract_version_is_rejected() -> None:
    assert_price_error(
        "price_contract_version_type_invalid",
        lambda: interpret(1, contract_version=IntSubclass(1)),
    )


@pytest.mark.parametrize("invalid", [True, 1.0, Decimal("1"), "1", None])
def test_contract_version_type_is_strict(invalid: object) -> None:
    assert_price_error(
        "price_contract_version_type_invalid",
        lambda: interpret(1, contract_version=invalid),
    )


def test_unsupported_contract_version_is_distinct_from_type_error() -> None:
    assert_price_error(
        "price_contract_version_unsupported",
        lambda: interpret(1, contract_version=2),
    )


def test_contract_id_type_and_value_are_validated() -> None:
    assert_price_error(
        "price_contract_id_type_invalid",
        lambda: interpret(1, contract_id=1),
    )
    assert_price_error(
        "price_contract_id_unsupported",
        lambda: interpret(1, contract_id="default_price_scale"),
    )
    assert_price_error(
        "price_contract_id_type_invalid",
        lambda: interpret(1, contract_id=StrSubclass(CONTRACT_ID)),
    )


def test_display_currency_type_and_value_are_validated() -> None:
    assert_price_error(
        "display_currency_type_invalid",
        lambda: interpret(1, display_currency=1),
    )
    assert_price_error(
        "display_currency_not_applicable",
        lambda: interpret(1, display_currency="USD"),
    )
    assert_price_error(
        "display_currency_type_invalid",
        lambda: interpret(1, display_currency=StrSubclass("GJN")),
    )


@pytest.mark.parametrize(
    "invalid",
    [True, False, "1", 0.1, Decimal("1"), None],
)
def test_non_integer_price_raw_is_rejected(invalid: object) -> None:
    assert_price_error(
        "price_raw_type_invalid",
        lambda: interpret(invalid),  # type: ignore[arg-type]
    )


def test_int_subclass_price_raw_is_rejected() -> None:
    assert_price_error(
        "price_raw_type_invalid",
        lambda: interpret(IntSubclass(1)),
    )


@pytest.mark.parametrize("invalid", [0, -1, JS_SAFE_INTEGER_MAX + 1])
def test_parser_range_violation_is_rejected(invalid: int) -> None:
    assert_price_error("price_raw_out_of_range", lambda: interpret(invalid))


def test_display_safe_maximum_is_accepted_exactly() -> None:
    result = interpret(MAX_EXACT_SAVED_FORMATTER_RAW)
    assert result.price_raw == 9_007_199_254_740_893
    assert result.base_amount_gjn == (
        Decimal(MAX_EXACT_SAVED_FORMATTER_RAW) / Decimal(10_000)
    )
    assert result.display_minor_units == 90_071_992_547_409
    assert result.canonical_display_text == "900719925474.09"


def test_decimal_values_are_exact_under_default_context() -> None:
    result = interpret(1_050_001)
    assert result.base_amount_gjn == Decimal("105.0001")
    assert result.display_amount_gjn == Decimal("105.01")


@pytest.mark.parametrize("precision", [3, 4, 6])
def test_decimal_values_do_not_depend_on_context_precision(
    precision: int,
) -> None:
    with localcontext() as context:
        context.prec = precision
        result = interpret(1_050_001)
        assert result.base_amount_gjn == Decimal("105.0001")
        assert result.display_amount_gjn == Decimal("105.01")


@pytest.mark.parametrize(
    "rounding",
    [ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN],
)
def test_decimal_values_do_not_depend_on_context_rounding(
    rounding: str,
) -> None:
    with localcontext() as context:
        context.prec = 3
        context.rounding = rounding
        result = interpret(1_050_001)
        assert result.base_amount_gjn == Decimal("105.0001")
        assert result.display_amount_gjn == Decimal("105.01")


def test_display_safe_maximum_is_exact_under_low_precision() -> None:
    with localcontext() as context:
        context.prec = 3
        result = interpret(MAX_EXACT_SAVED_FORMATTER_RAW)
        assert result.base_amount_gjn == Decimal("900719925474.0893")
        assert result.display_amount_gjn == Decimal("900719925474.09")


def test_external_decimal_context_is_not_modified() -> None:
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_UP
        before = (context.prec, context.rounding)
        interpret(1_050_001)
        assert (context.prec, context.rounding) == before


@pytest.mark.parametrize(
    "raw",
    [1, 1_050_001, MAX_EXACT_SAVED_FORMATTER_RAW],
)
def test_display_decimal_matches_canonical_text_in_every_context(raw: int) -> None:
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        result = interpret(raw)
        assert result.display_amount_gjn == Decimal(
            result.canonical_display_text
        )


@pytest.mark.parametrize(
    "raw",
    [
        MAX_EXACT_SAVED_FORMATTER_RAW + 1,
        9_007_199_254_740_900,
        JS_SAFE_INTEGER_MAX,
    ],
)
def test_js_safe_values_above_display_contract_are_rejected(raw: int) -> None:
    assert_price_error(
        "price_raw_display_range_unsupported",
        lambda: interpret(raw),
    )


def test_confirmed_fixture_public_entry_succeeds() -> None:
    result = interpret_current_order_book_document(
        FIXTURE.read_bytes(),
        1_710_300,
        display_currency="GJN",
        contract_id=CONTRACT_ID,
        contract_version=CONTRACT_VERSION,
    )
    assert result.canonical_display_text == "171.03"


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
def test_public_entry_rejects_unconfirmed_documents(
    change: Callable[[dict[str, object]], None], code: str
) -> None:
    assert_parser_error(code, lambda: interpret(1, mutated(change)))


def test_public_entry_rejects_crossed_document() -> None:
    def cross(document: dict[str, object]) -> None:
        book = nested(document, "order_book")
        best_sell = nested(book, "best_sell")
        crossed_price = best_sell["price_raw"]
        assert isinstance(crossed_price, int)
        crossed_price += 1
        levels = book["buy_levels"]
        assert isinstance(levels, list)
        best_index = max(
            range(len(levels)),
            key=lambda index: levels[index][0],  # type: ignore[index]
        )
        level = levels[best_index]
        assert isinstance(level, list)
        level[0] = crossed_price
        nested(book, "best_buy")["price_raw"] = crossed_price

    assert_parser_error(
        "crossed_order_book",
        lambda: interpret(1, mutated(cross)),
    )


def test_public_entry_rejects_encoded_slash_external_key() -> None:
    def encode_slash(document: dict[str, object]) -> None:
        page = nested(document, "page")
        page["path"] = "/market/1067/bad%2Fkey"
        page["external_key"] = "bad%2Fkey"

    assert_parser_error(
        "page_decoded_key_invalid",
        lambda: interpret(1, mutated(encode_slash)),
    )


def test_public_entry_rejects_external_key_path_mismatch() -> None:
    def mismatch(document: dict[str, object]) -> None:
        nested(document, "page")["external_key"] = "other"

    assert_parser_error(
        "page_external_key_mismatch",
        lambda: interpret(1, mutated(mismatch)),
    )


def test_replaced_dataclass_cannot_masquerade_as_public_document() -> None:
    replaced_capture = replace(
        parse_manual_order_book_json(FIXTURE.read_bytes()),
        schema_version="round6_manual_order_book_capture_v1",
    )
    assert_parser_error(
        "document_type_invalid",
        lambda: interpret_current_order_book_document(
            replaced_capture,  # type: ignore[arg-type]
            1,
            display_currency="GJN",
            contract_id=CONTRACT_ID,
            contract_version=CONTRACT_VERSION,
        ),
    )


def test_reference_module_has_no_database_network_file_or_environment_imports() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert imports.isdisjoint(
        {
            "sqlalchemy",
            "psycopg",
            "requests",
            "httpx",
            "urllib",
            "socket",
            "pathlib",
            "os",
            "environ",
        }
    )
    source = MODULE.read_text(encoding="utf-8")
    assert "float(" not in source
