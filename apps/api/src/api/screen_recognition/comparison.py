from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Any

from api.screen_recognition.contracts import (
    CutStatus,
    FieldComparison,
    GroundTruthEntry,
    HARD_ERROR_CODES,
    PriceLevel,
    SampleResult,
    ScreenContract,
    stable_issue_codes,
)
from api.screen_recognition.parser import normalize_item_name


DEFAULT_THRESHOLDS = {
    "processed_images": 20,
    "structured_results": 20,
    "unexpected_errors": 0,
    "best_bid_exact_match_count": 19,
    "best_ask_exact_match_count": 19,
    "item_name_exact_or_normalized_match_count": 18,
    "total_quantity_match_count": 18,
    "aggregate_case_accuracy": Decimal("1"),
    "bid_ask_swapped": 0,
    "price_above_market_cap": 0,
    "unreadable_forged_normal_result": 0,
    "hard_error_count": 0,
    "passed": 18,
}


def compare_contracts(
    expected_entry: GroundTruthEntry, recognized: ScreenContract
) -> tuple[list[FieldComparison], list[str], list[str]]:
    expected = expected_entry.expected
    comparisons: list[FieldComparison] = []
    errors: list[str] = []
    warnings: list[str] = []
    _compare_item_name(expected, recognized, comparisons, errors, warnings)
    _compare_decimal("best_bid", expected.best_bid, recognized.best_bid, "best_bid_mismatch", comparisons, errors)
    _compare_decimal("best_ask", expected.best_ask, recognized.best_ask, "best_ask_mismatch", comparisons, errors)
    _compare_int(
        "total_bid_quantity",
        expected.total_bid_quantity,
        recognized.total_bid_quantity,
        "total_bid_quantity_mismatch",
        comparisons,
        errors,
    )
    _compare_int(
        "total_ask_quantity",
        expected.total_ask_quantity,
        recognized.total_ask_quantity,
        "total_ask_quantity_mismatch",
        comparisons,
        errors,
    )
    _compare_levels("bid_levels", expected.bid_levels, recognized.bid_levels, comparisons, errors)
    _compare_levels("ask_levels", expected.ask_levels, recognized.ask_levels, comparisons, errors)
    return comparisons, warnings, stable_issue_codes(errors)


def determine_sample_status(
    errors: list[str], warnings: list[str], *, expected_status: str
) -> CutStatus:
    if expected_status == CutStatus.UNREADABLE.value and "image_unreadable" in errors:
        return CutStatus.PASSED_WITH_WARNING if warnings else CutStatus.PASSED
    if "unexpected_exception" in errors:
        return CutStatus.UNEXPECTED_ERROR
    if "image_unreadable" in errors:
        return CutStatus.UNREADABLE
    if errors:
        return CutStatus.FAILED
    if warnings:
        return CutStatus.PASSED_WITH_WARNING
    return CutStatus.PASSED


def summarize_results(
    *,
    results: list[SampleResult],
    files_found: int,
    ground_truth_entries: int,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_thresholds = thresholds or DEFAULT_THRESHOLDS
    durations = [result.processing_duration_ms for result in results]
    best_bid_eval = _evaluable_count(results, "best_bid")
    best_ask_eval = _evaluable_count(results, "best_ask")
    item_eval = _evaluable_count(results, "item_name")
    quantity_eval = _quantity_evaluable_count(results)
    aggregate_cases = _aggregate_cases(results)
    summary = {
        "total_images": ground_truth_entries,
        "files_found": files_found,
        "ground_truth_entries": ground_truth_entries,
        "processed_images": len(results),
        "passed": _status_count(results, CutStatus.PASSED),
        "passed_with_warning": _status_count(results, CutStatus.PASSED_WITH_WARNING),
        "failed": _status_count(results, CutStatus.FAILED),
        "unreadable": _status_count(results, CutStatus.UNREADABLE),
        "unexpected_errors": _status_count(results, CutStatus.UNEXPECTED_ERROR),
        "full_sample_pass_rate": _rate(_status_count(results, CutStatus.PASSED), len(results)),
        "best_bid_evaluable_count": best_bid_eval,
        "best_bid_exact_match_count": _passed_field_count(results, "best_bid"),
        "best_bid_exact_match_rate": _rate(_passed_field_count(results, "best_bid"), best_bid_eval),
        "best_ask_evaluable_count": best_ask_eval,
        "best_ask_exact_match_count": _passed_field_count(results, "best_ask"),
        "best_ask_exact_match_rate": _rate(_passed_field_count(results, "best_ask"), best_ask_eval),
        "item_name_evaluable_count": item_eval,
        "item_name_exact_match_count": _item_exact_count(results),
        "item_name_normalized_match_count": _item_normalized_count(results),
        "total_quantity_evaluable_count": quantity_eval,
        "total_quantity_match_count": _total_quantity_match_count(results),
        "aggregate_case_count": len(aggregate_cases),
        "aggregate_case_correct_count": sum(1 for comparison in aggregate_cases if comparison.passed),
        "aggregate_case_accuracy": _rate(
            sum(1 for comparison in aggregate_cases if comparison.passed), len(aggregate_cases)
        ),
        "hard_error_count": sum(1 for result in results for code in result.errors if code in HARD_ERROR_CODES),
        "mean_processing_duration_ms": int(sum(durations) / len(durations)) if durations else None,
        "median_processing_duration_ms": int(median(durations)) if durations else None,
        "bid_ask_swapped_count": _error_count(results, "bid_ask_swapped"),
        "price_above_market_cap_count": _error_count(results, "price_above_market_cap"),
        "unreadable_forged_normal_result_count": _unreadable_forged_count(results),
    }
    summary["overall_status"] = _overall_status(summary, effective_thresholds)
    return summary


def _overall_status(summary: dict[str, Any], thresholds: dict[str, Any]) -> str:
    failures: list[str] = []
    if summary["processed_images"] < min(thresholds["processed_images"], summary["ground_truth_entries"]):
        failures.append("processed_images")
    for key in ("unexpected_errors", "bid_ask_swapped_count", "price_above_market_cap_count", "hard_error_count"):
        threshold_key = key.removesuffix("_count") if key.endswith("_count") else key
        expected = thresholds.get(threshold_key, thresholds.get(key))
        if expected is not None and summary[key] > expected:
            failures.append(key)
    threshold_pairs = (
        ("best_bid_exact_match_count", "best_bid_evaluable_count"),
        ("best_ask_exact_match_count", "best_ask_evaluable_count"),
        ("total_quantity_match_count", "total_quantity_evaluable_count"),
        ("passed", None),
    )
    for key, denominator_key in threshold_pairs:
        denominator = summary[denominator_key] if denominator_key else thresholds[key]
        effective_threshold = min(thresholds[key], denominator)
        if summary[key] < effective_threshold:
            failures.append(key)
    name_matches = max(summary["item_name_exact_match_count"], summary["item_name_normalized_match_count"])
    if name_matches < min(thresholds["item_name_exact_or_normalized_match_count"], summary["item_name_evaluable_count"]):
        failures.append("item_name_match_count")
    if summary["aggregate_case_accuracy"] is not None:
        if Decimal(summary["aggregate_case_accuracy"]) < thresholds["aggregate_case_accuracy"]:
            failures.append("aggregate_case_accuracy")
    if failures:
        return "failed"
    if summary["passed_with_warning"] > 0 or summary["failed"] > 0 or summary["unreadable"] > 0:
        return "passed_with_warnings"
    return "passed"


def _compare_item_name(
    expected: ScreenContract,
    recognized: ScreenContract,
    comparisons: list[FieldComparison],
    errors: list[str],
    warnings: list[str],
) -> None:
    if expected.item_name is None:
        comparisons.append(FieldComparison("item_name", False, None, None, recognized.item_name))
        return
    exact = expected.item_name == recognized.item_name
    normalized = normalize_item_name(expected.item_name) == normalize_item_name(recognized.item_name or "")
    passed = exact or normalized
    if not passed:
        errors.append("item_name_missing" if not recognized.item_name else "item_name_mismatch")
    elif not exact:
        warnings.append("item_name_normalized_match")
    comparisons.append(
        FieldComparison(
            "item_name",
            True,
            passed,
            expected.item_name,
            recognized.item_name,
            None if passed else "item_name_missing",
            {"exact_match": exact, "normalized_match": normalized},
        )
    )


def _compare_decimal(
    field: str,
    expected: Decimal | None,
    actual: Decimal | None,
    error_code: str,
    comparisons: list[FieldComparison],
    errors: list[str],
) -> None:
    if expected is None:
        comparisons.append(FieldComparison(field, False, None, None, None if actual is None else str(actual)))
        return
    passed = actual == expected
    if not passed:
        errors.append(error_code)
    comparisons.append(
        FieldComparison(
            field,
            True,
            passed,
            str(expected),
            None if actual is None else str(actual),
            None if passed else error_code,
        )
    )


def _compare_int(
    field: str,
    expected: int | None,
    actual: int | None,
    error_code: str,
    comparisons: list[FieldComparison],
    errors: list[str],
) -> None:
    if expected is None:
        comparisons.append(FieldComparison(field, False, None, None, actual))
        return
    passed = actual == expected
    if not passed:
        errors.append(error_code)
    comparisons.append(FieldComparison(field, True, passed, expected, actual, None if passed else error_code))


def _compare_levels(
    field: str,
    expected: tuple[PriceLevel, ...],
    actual: tuple[PriceLevel, ...],
    comparisons: list[FieldComparison],
    errors: list[str],
) -> None:
    if not expected:
        comparisons.append(FieldComparison(field, False, None, [], [level.to_json() for level in actual]))
        return
    passed = len(expected) == len(actual)
    details: dict[str, Any] = {"level_count_match": passed}
    if passed:
        for index, expected_level in enumerate(expected):
            actual_level = actual[index]
            current = _level_equal(expected_level, actual_level)
            if expected_level.is_aggregate and not actual_level.is_aggregate:
                errors.append("aggregate_price_misclassified")
            passed = passed and current
    if not passed and "aggregate_price_misclassified" not in errors:
        errors.append(f"{field}_mismatch")
    comparisons.append(
        FieldComparison(
            field,
            True,
            passed,
            [level.to_json() for level in expected],
            [level.to_json() for level in actual],
            None if passed else f"{field}_mismatch",
            details,
        )
    )


def _level_equal(expected: PriceLevel, actual: PriceLevel) -> bool:
    return (
        expected.exact_price == actual.exact_price
        and expected.price_lower_bound == actual.price_lower_bound
        and expected.lower_bound_inclusive == actual.lower_bound_inclusive
        and expected.aggregation_type == actual.aggregation_type
        and expected.quantity == actual.quantity
    )


def _status_count(results: list[SampleResult], status: CutStatus) -> int:
    return sum(1 for result in results if result.status == status)


def _error_count(results: list[SampleResult], code: str) -> int:
    return sum(1 for result in results if code in result.errors)


def _passed_field_count(results: list[SampleResult], field: str) -> int:
    return sum(
        1
        for result in results
        for comparison in result.field_comparisons
        if comparison.field_name == field and comparison.evaluable and comparison.passed
    )


def _evaluable_count(results: list[SampleResult], field: str) -> int:
    return sum(
        1
        for result in results
        for comparison in result.field_comparisons
        if comparison.field_name == field and comparison.evaluable
    )


def _quantity_evaluable_count(results: list[SampleResult]) -> int:
    fields = {"total_bid_quantity", "total_ask_quantity"}
    return sum(
        1
        for result in results
        for comparison in result.field_comparisons
        if comparison.field_name in fields and comparison.evaluable
    )


def _total_quantity_match_count(results: list[SampleResult]) -> int:
    fields = {"total_bid_quantity", "total_ask_quantity"}
    return sum(
        1
        for result in results
        for comparison in result.field_comparisons
        if comparison.field_name in fields and comparison.evaluable and comparison.passed
    )


def _item_exact_count(results: list[SampleResult]) -> int:
    return sum(
        1
        for result in results
        for comparison in result.field_comparisons
        if comparison.field_name == "item_name"
        and comparison.evaluable
        and comparison.details.get("exact_match")
    )


def _item_normalized_count(results: list[SampleResult]) -> int:
    return sum(
        1
        for result in results
        for comparison in result.field_comparisons
        if comparison.field_name == "item_name"
        and comparison.evaluable
        and comparison.details.get("normalized_match")
    )


def _aggregate_cases(results: list[SampleResult]) -> list[FieldComparison]:
    cases: list[FieldComparison] = []
    for result in results:
        for comparison in result.field_comparisons:
            if comparison.field_name in {"bid_levels", "ask_levels"}:
                expected_levels = comparison.expected or []
                if any(level.get("aggregation_type") for level in expected_levels):
                    cases.append(comparison)
    return cases


def _unreadable_forged_count(results: list[SampleResult]) -> int:
    count = 0
    for result in results:
        if result.status == CutStatus.UNREADABLE:
            if result.recognized.best_bid is not None or result.recognized.best_ask is not None:
                count += 1
    return count


def _rate(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str(Decimal(numerator) / Decimal(denominator))
