from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Callable

import pytest

from api.importers.manual_order_book_json import (
    JS_SAFE_INTEGER_MAX,
    MAX_DOCUMENT_BYTES,
    MAX_JSON_NESTING_DEPTH,
    ManualOrderBookValidationError,
    compute_normalized_capture_fingerprint,
    parse_manual_order_book_json,
    read_manual_order_book_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_SOURCE = PROJECT_ROOT / "apps" / "api" / "src"
FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "manual-orderbook"
    / "confirmed-orderbook-capture.json"
)
EXPECTED_FIXTURE_SHA256 = (
    "be0f23d7eaa7394f2bf666a49f7c409693a4f41e7c6817c64d5dd26ab8276d42"
)
EXPECTED_FINGERPRINT = (
    "ffcc6c7696deb30f52d07982f46e399c38dffa20427d2c1687732dfd2aebc1b4"
)


def fixture_document() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def encoded(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def with_raw_json_external_key(literal: bytes) -> bytes:
    original = b"id50280_f_14a_iriaf_usa"
    content = FIXTURE.read_bytes()
    assert content.count(original) == 2
    return content.replace(original, literal)


def resign(document: dict[str, object]) -> dict[str, object]:
    source = document["source"]
    assert isinstance(source, dict)
    source["normalized_capture_fingerprint"] = (
        compute_normalized_capture_fingerprint(document)
    )
    return document


def assert_code(content: bytes | str, code: str) -> None:
    with pytest.raises(ManualOrderBookValidationError) as exc_info:
        parse_manual_order_book_json(content)
    assert exc_info.value.code == code
    assert str(exc_info.value) == code


def mutate(
    callback: Callable[[dict[str, object]], None],
    *,
    sign: bool = False,
) -> bytes:
    document = fixture_document()
    callback(document)
    if sign:
        resign(document)
    return encoded(document)


def nested(document: dict[str, object], key: str) -> dict[str, object]:
    value = document[key]
    assert isinstance(value, dict)
    return value


def order_book(document: dict[str, object]) -> dict[str, object]:
    return nested(document, "order_book")


def test_golden_extension_fixture_parses_and_preserves_raw_contract() -> None:
    import hashlib

    content = FIXTURE.read_bytes()
    assert hashlib.sha256(content).hexdigest() == EXPECTED_FIXTURE_SHA256
    capture = parse_manual_order_book_json(content)
    assert capture.schema_version == "round6_manual_order_book_capture_v1"
    assert capture.capture_id == "123e4567-e89b-42d3-a456-426614174000"
    assert capture.captured_at == "2026-07-13T12:00:00.000Z"
    assert capture.timezone_offset_minutes == -540
    assert capture.page.literal_external_key == "id50280_f_14a_iriaf_usa"
    assert capture.page.decoded_external_key == "id50280_f_14a_iriaf_usa"
    assert len(capture.order_book.buy_levels) == 41
    assert len(capture.order_book.sell_levels) == 70
    assert capture.order_book.reported_depth.buy == 84
    assert capture.order_book.reported_depth.sell == 170
    assert capture.order_book.best_buy.price_raw == 1_710_300
    assert capture.order_book.best_sell.price_raw == 2_180_000
    assert capture.source.raw_response_hash_verifiable is False


def test_python_fingerprint_matches_extension_golden_fingerprint() -> None:
    document = fixture_document()
    source = nested(document, "source")
    assert source["normalized_capture_fingerprint"] == EXPECTED_FINGERPRINT
    assert compute_normalized_capture_fingerprint(document) == EXPECTED_FINGERPRINT


def test_unsupported_schema_version_is_rejected() -> None:
    assert_code(
        mutate(
            lambda document: document.__setitem__(
                "schema_version",
                "round6_manual_order_book_capture_v2",
            )
        ),
        "schema_version_unsupported",
    )


@pytest.mark.parametrize("field", ["schema_version", "page", "order_book", "source", "review"])
def test_required_top_level_fields_cannot_be_omitted(field: str) -> None:
    assert_code(
        mutate(lambda document: document.pop(field)),
        "top_level_keys_invalid",
    )


@pytest.mark.parametrize(
    "target",
    [
        (),
        ("page",),
        ("request",),
        ("order_book",),
        ("order_book", "reported_depth"),
        ("order_book", "best_buy"),
        ("order_book", "best_sell"),
        ("source",),
        ("review",),
    ],
)
def test_unknown_fields_are_rejected_at_every_object_scope(
    target: tuple[str, ...],
) -> None:
    document = fixture_document()
    selected: dict[str, object] = document
    for part in target:
        value = selected[part]
        assert isinstance(value, dict)
        selected = value
    selected["unexpected"] = "discard"
    expected = "top_level_keys_invalid" if not target else f"{target[-1]}_keys_invalid"
    if target == ("order_book", "reported_depth"):
        expected = "reported_depth_keys_invalid"
    elif target == ("order_book", "best_buy"):
        expected = "best_buy_invalid"
    elif target == ("order_book", "best_sell"):
        expected = "best_sell_invalid"
    assert_code(encoded(document), expected)


@pytest.mark.parametrize("nested_duplicate", [False, True])
def test_duplicate_json_keys_are_rejected(nested_duplicate: bool) -> None:
    text = json.dumps(fixture_document(), separators=(",", ":"))
    if nested_duplicate:
        needle = '"capture_method":"passive_page_response_intercept"'
        replacement = f"{needle},{needle}"
    else:
        needle = '"schema_version":"round6_manual_order_book_capture_v1"'
        replacement = f"{needle},{needle}"
    assert needle in text
    assert_code(text.replace(needle, replacement, 1), "duplicate_object_key")


def test_document_larger_than_two_mib_is_rejected() -> None:
    assert_code(b" " * (MAX_DOCUMENT_BYTES + 1), "file_too_large")


def test_file_reader_reads_limit_plus_one_and_rejects(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 100))
    with pytest.raises(ManualOrderBookValidationError) as exc_info:
        read_manual_order_book_file(path)
    assert exc_info.value.code == "file_too_large"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"\xff", "invalid_utf8"),
        ('{"value":"\ufffd"}', "replacement_character_forbidden"),
        ("{", "invalid_json"),
        ('{"value":NaN}', "invalid_json"),
    ],
)
def test_invalid_text_and_json_are_rejected(content: bytes | str, code: str) -> None:
    assert_code(content, code)


def test_excessive_nesting_is_rejected_before_json_decode() -> None:
    content = "[" * (MAX_JSON_NESTING_DEPTH + 1)
    assert_code(content, "json_nesting_too_deep")


@pytest.mark.parametrize(
    "value",
    [
        "123e4567-e89b-12d3-a456-426614174000",
        "123E4567-E89B-42D3-A456-426614174000",
        "{123e4567-e89b-42d3-a456-426614174000}",
        "not-a-uuid",
    ],
)
def test_capture_id_must_be_canonical_lowercase_uuid4(value: str) -> None:
    assert_code(
        mutate(lambda document: document.__setitem__("capture_id", value)),
        "capture_id_invalid",
    )


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-13T12:00:00Z",
        "2026-07-13T12:00:00.00Z",
        "2026-07-13T12:00:00.000+00:00",
        "2026-02-30T12:00:00.000Z",
    ],
)
def test_captured_at_must_be_canonical_utc_milliseconds(value: str) -> None:
    assert_code(
        mutate(lambda document: document.__setitem__("captured_at", value)),
        "captured_at_invalid",
    )


@pytest.mark.parametrize("value", [-841, 841, 1.5, True])
def test_timezone_offset_is_bounded_integer(value: object) -> None:
    assert_code(
        mutate(
            lambda document: document.__setitem__(
                "timezone_offset_minutes",
                value,
            )
        ),
        "timezone_offset_invalid",
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("origin", "https://example.invalid", "page_origin_invalid"),
        ("path", "/market/9999/item", "page_path_invalid"),
        ("path", "/market/1067/item/extra", "page_path_invalid"),
        ("path", "/market/1067/item?x=1", "page_path_invalid"),
        ("external_key", "other", "page_external_key_mismatch"),
    ],
)
def test_page_identity_is_exact(field: str, value: str, code: str) -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "page")[field] = value

    assert_code(mutate(change), code)


def test_percent_encoded_external_key_is_decoded_once_and_preserved() -> None:
    literal = "AMX%2050%20Surblind%C3%A9%20%28France%29"

    def change(document: dict[str, object]) -> None:
        page = nested(document, "page")
        page["path"] = f"/market/1067/{literal}"
        page["external_key"] = literal

    capture = parse_manual_order_book_json(mutate(change, sign=True))
    assert capture.page.literal_external_key == literal
    assert capture.page.decoded_external_key == "AMX 50 Surblindé (France)"


@pytest.mark.parametrize("literal", ["bad%", "bad%2", "bad%GG", "%C3%28"])
def test_malformed_or_invalid_utf8_percent_encoding_is_rejected(
    literal: str,
) -> None:
    def change(document: dict[str, object]) -> None:
        page = nested(document, "page")
        page["path"] = f"/market/1067/{literal}"
        page["external_key"] = literal

    assert_code(mutate(change), "page_percent_encoding_invalid")


@pytest.mark.parametrize(
    "literal",
    [
        br"\ud800tail",
        br"head\udc00",
        br"mid\ud800dle",
        br"mid\udc00dle",
    ],
)
def test_escaped_lone_surrogate_is_rejected_with_stable_code(
    literal: bytes,
) -> None:
    assert_code(
        with_raw_json_external_key(literal),
        "page_percent_encoding_invalid",
    )


def test_escaped_valid_surrogate_pair_is_accepted() -> None:
    document = fixture_document()
    literal = "vehicle_\U0001f680"
    page = nested(document, "page")
    page["path"] = f"/market/1067/{literal}"
    page["external_key"] = literal
    resign(document)
    content = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    assert br"\ud83d\ude80" in content

    capture = parse_manual_order_book_json(content)
    assert capture.page.literal_external_key == literal
    assert capture.page.decoded_external_key == literal


def test_literal_non_ascii_external_key_is_accepted() -> None:
    document = fixture_document()
    literal = "装甲車"
    page = nested(document, "page")
    page["path"] = f"/market/1067/{literal}"
    page["external_key"] = literal

    capture = parse_manual_order_book_json(encoded(resign(document)))
    assert capture.page.literal_external_key == literal
    assert capture.page.decoded_external_key == literal


@pytest.mark.parametrize(
    "literal",
    ["bad%2Fkey", "bad%5Ckey", "bad%3Fkey", "bad%23key", "bad%00key", "bad%1Fkey"],
)
def test_decoded_separators_and_control_characters_are_rejected(
    literal: str,
) -> None:
    def change(document: dict[str, object]) -> None:
        page = nested(document, "page")
        page["path"] = f"/market/1067/{literal}"
        page["external_key"] = literal

    assert_code(mutate(change), "page_decoded_key_invalid")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "GET"),
        ("origin", "https://trade.gaijin.net"),
        ("path", "/web?x=1"),
    ],
)
def test_request_identity_is_exact(field: str, value: str) -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "request")[field] = value

    assert_code(mutate(change), "request_identity_invalid")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("success", False, "order_book_success_invalid"),
        ("type", "OTHER", "order_book_type_invalid"),
    ],
)
def test_order_book_identity_is_exact(
    field: str,
    value: object,
    code: str,
) -> None:
    def change(document: dict[str, object]) -> None:
        order_book(document)[field] = value

    assert_code(mutate(change), code)


@pytest.mark.parametrize(
    ("field", "levels"),
    [
        ("buy_levels", []),
        ("sell_levels", []),
        ("buy_levels", [[1, 1]] * 501),
        ("sell_levels", [[1, 1]] * 501),
    ],
)
def test_each_side_requires_one_to_five_hundred_levels(
    field: str,
    levels: list[list[int]],
) -> None:
    def change(document: dict[str, object]) -> None:
        order_book(document)[field] = levels

    assert_code(mutate(change), f"{field}_invalid")


@pytest.mark.parametrize("level", [1, [1], [1, 2, 3], {"price": 1}])
def test_level_must_be_two_element_array(level: object) -> None:
    def change(document: dict[str, object]) -> None:
        levels = order_book(document)["buy_levels"]
        assert isinstance(levels, list)
        levels[0] = level

    assert_code(mutate(change), "order_book_level_invalid")


@pytest.mark.parametrize("value", [0, -1, 1.5, True, JS_SAFE_INTEGER_MAX + 1])
@pytest.mark.parametrize("position", [0, 1])
def test_level_values_are_positive_js_safe_integers(
    value: object,
    position: int,
) -> None:
    def change(document: dict[str, object]) -> None:
        levels = order_book(document)["buy_levels"]
        assert isinstance(levels, list)
        level = levels[0]
        assert isinstance(level, list)
        level[position] = value

    assert_code(mutate(change), "order_book_integer_invalid")


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("buy_level_count", "buy_level_count_mismatch"),
        ("sell_level_count", "sell_level_count_mismatch"),
    ],
)
def test_level_counts_are_recomputed(field: str, code: str) -> None:
    def change(document: dict[str, object]) -> None:
        book = order_book(document)
        value = book[field]
        assert isinstance(value, int)
        book[field] = value + 1

    assert_code(mutate(change), code)


@pytest.mark.parametrize(
    ("field", "component", "code"),
    [
        ("best_buy", "price_raw", "best_buy_mismatch"),
        ("best_buy", "quantity", "best_buy_mismatch"),
        ("best_sell", "price_raw", "best_sell_mismatch"),
        ("best_sell", "quantity", "best_sell_mismatch"),
    ],
)
def test_best_levels_are_recomputed(
    field: str,
    component: str,
    code: str,
) -> None:
    def change(document: dict[str, object]) -> None:
        best = order_book(document)[field]
        assert isinstance(best, dict)
        value = best[component]
        assert isinstance(value, int)
        best[component] = value + 1

    assert_code(mutate(change), code)


@pytest.mark.parametrize("value", [-1, 1.5, True, JS_SAFE_INTEGER_MAX + 1])
def test_reported_depth_is_nonnegative_js_safe_integer(value: object) -> None:
    def change(document: dict[str, object]) -> None:
        depth = order_book(document)["reported_depth"]
        assert isinstance(depth, dict)
        depth["BUY"] = value

    assert_code(mutate(change), "reported_depth_invalid")


def test_reported_depth_is_not_derived_from_level_count() -> None:
    capture = parse_manual_order_book_json(FIXTURE.read_bytes())
    assert capture.order_book.reported_depth.buy == 84
    assert capture.order_book.buy_level_count == 41
    assert capture.order_book.reported_depth.sell == 170
    assert capture.order_book.sell_level_count == 70


@pytest.mark.parametrize("field", ["raw_response_sha256", "normalized_capture_fingerprint"])
@pytest.mark.parametrize("value", ["a" * 63, "A" * 64, "not-a-hash"])
def test_source_hashes_must_be_lowercase_sha256(
    field: str,
    value: str,
) -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "source")[field] = value

    assert_code(mutate(change), "source_hash_invalid")


def test_capture_method_is_exact() -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "source")["capture_method"] = "manual_copy"

    assert_code(mutate(change), "capture_method_invalid")


def test_fingerprint_tampering_is_rejected() -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "source")["normalized_capture_fingerprint"] = "0" * 64

    assert_code(mutate(change), "fingerprint_mismatch")


def test_pending_capture_is_not_importable() -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "review")["status"] = "pending_user_confirmation"

    assert_code(mutate(change, sign=True), "review_status_not_importable")


def test_manual_review_capture_is_not_importable() -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "review")["requires_manual_review"] = True

    assert_code(mutate(change, sign=True), "manual_review_required")


def test_fill_claim_must_remain_false() -> None:
    def change(document: dict[str, object]) -> None:
        nested(document, "review")["fill_claim"] = True

    assert_code(mutate(change, sign=True), "fill_claim_not_importable")


def test_crossed_book_is_not_importable() -> None:
    def change(document: dict[str, object]) -> None:
        book = order_book(document)
        levels = book["buy_levels"]
        assert isinstance(levels, list)
        first = levels[0]
        assert isinstance(first, list)
        first[0] = 3_000_000
        best = book["best_buy"]
        assert isinstance(best, dict)
        best["price_raw"] = 3_000_000

    assert_code(mutate(change, sign=True), "crossed_order_book")


def test_equal_best_bid_and_ask_is_allowed() -> None:
    def change(document: dict[str, object]) -> None:
        book = order_book(document)
        levels = book["sell_levels"]
        assert isinstance(levels, list)
        first = levels[0]
        assert isinstance(first, list)
        first[:] = [1_710_300, 9]
        best = book["best_sell"]
        assert isinstance(best, dict)
        best["price_raw"] = 1_710_300
        best["quantity"] = 9

    capture = parse_manual_order_book_json(mutate(change, sign=True))
    assert capture.order_book.best_buy.price_raw == capture.order_book.best_sell.price_raw


def test_duplicate_same_price_levels_are_preserved_without_merge_or_sort() -> None:
    def change(document: dict[str, object]) -> None:
        book = order_book(document)
        levels = book["buy_levels"]
        assert isinstance(levels, list)
        levels.insert(1, [1_710_300, 7])
        book["buy_level_count"] = len(levels)

    capture = parse_manual_order_book_json(mutate(change, sign=True))
    matching = [
        level.quantity
        for level in capture.order_book.buy_levels
        if level.price_raw == 1_710_300
    ]
    assert matching == [1, 7]
    assert capture.order_book.buy_levels[0].price_raw == 1_710_300
    assert capture.order_book.buy_levels[1].price_raw == 1_710_300


def cli_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(API_SOURCE)
    return environment


def test_cli_success_outputs_only_sanitized_validation_summary() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.importers.manual_order_book_json",
            str(FIXTURE),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(),
    )
    assert result.returncode == 0
    assert result.stderr == ""
    summary = json.loads(result.stdout)
    assert summary["validation_status"] == "valid"
    assert summary["normalized_capture_fingerprint"] == EXPECTED_FINGERPRINT
    assert "buy_levels" not in summary
    assert "sell_levels" not in summary
    assert "database" not in result.stdout.lower()
    assert "authorization" not in result.stdout.lower()


def test_cli_failure_is_stable_and_does_not_echo_input(tmp_path: Path) -> None:
    path = tmp_path / "sensitive.json"
    secret = "sensitive-marker-must-not-be-echoed"
    path.write_text(json.dumps({"payload": secret}), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.importers.manual_order_book_json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(),
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error_code": "top_level_keys_invalid",
        "validation_status": "invalid",
    }
    assert secret not in result.stderr
    assert str(path) not in result.stderr


def test_cli_escaped_lone_surrogate_has_narrow_stable_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "escaped-surrogate.json"
    sentinel = "surrogate-sentinel-must-not-be-echoed"
    path.write_bytes(
        with_raw_json_external_key(
            sentinel.encode("ascii") + br"\ud800"
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.importers.manual_order_book_json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(),
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error_code": "page_percent_encoding_invalid",
        "validation_status": "invalid",
    }
    assert "Traceback" not in result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert sentinel not in result.stderr
    assert "manual_order_book_json.py" not in result.stderr
    assert str(path) not in result.stderr


def test_cli_help_says_validator_not_database_importer() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.importers.manual_order_book_json",
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(),
    )
    assert result.returncode == 0
    assert "validator" in result.stdout.lower()
    assert "not a database importer" in result.stdout.lower()


def test_parser_module_has_no_database_router_or_network_imports() -> None:
    module_path = API_SOURCE / "api" / "importers" / "manual_order_book_json.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden_prefixes = (
        "api.db",
        "api.routers",
        "sqlalchemy",
        "requests",
        "httpx",
        "socket",
        "urllib.request",
    )
    assert not any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in imported
        for prefix in forbidden_prefixes
    )
    assert "Decimal" not in module_path.read_text(encoding="utf-8")
