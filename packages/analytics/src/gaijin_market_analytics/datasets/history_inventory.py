"""Deterministic descriptive inventory for offline historical trade datasets."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

from gaijin_market_analytics.backtesting.trade_evidence_contracts import (
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
)
from gaijin_market_analytics.datasets.gaijin_history_json import OfflineHistoryDataset


INVENTORY_SCHEMA_VERSION = "round6_offline_history_inventory_v1"
_QUANTILES = (
    ("min", Decimal("0")),
    ("p25", Decimal("0.25")),
    ("median", Decimal("0.50")),
    ("p75", Decimal("0.75")),
    ("p90", Decimal("0.90")),
    ("p95", Decimal("0.95")),
    ("p99", Decimal("0.99")),
    ("max", Decimal("1")),
)


def build_history_inventory(
    dataset: OfflineHistoryDataset,
    *,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    samples = []
    granularity_statistics = []
    overlap_consistency = []
    fallback_coverage = []
    heuristic_market_features = []
    for sample in dataset.samples:
        buckets = sample.history.buckets
        hourly = _granularity(buckets, HistoricalTradeGranularity.HOUR_1)
        daily = _granularity(buckets, HistoricalTradeGranularity.DAY_1)
        starts = tuple(bucket.bucket_start_utc for bucket in buckets)
        earliest = min(starts) if starts else None
        latest = max(starts) if starts else None
        samples.append(
            {
                "file_name": sample.spec.file.replace("\\", "/"),
                "file_sha256": sample.source_file_sha256,
                "offline_item_id": sample.spec.offline_item_id,
                "sample_key": sample.spec.sample_key,
                "display_name": sample.spec.display_name,
                "one_hour_bucket_count": len(hourly),
                "one_day_bucket_count": len(daily),
                "total_bucket_count": len(buckets),
                "earliest_timestamp": _iso(earliest),
                "latest_timestamp": _iso(latest),
                "calendar_span_seconds": (
                    int((latest - earliest).total_seconds())
                    if earliest is not None and latest is not None
                    else 0
                ),
                "typed_contract_valid": True,
            }
        )
        for granularity, selected in (
            (HistoricalTradeGranularity.HOUR_1, hourly),
            (HistoricalTradeGranularity.DAY_1, daily),
        ):
            granularity_statistics.append(
                _granularity_statistics(sample.spec.offline_item_id, granularity, selected)
            )
        overlap_consistency.append(
            _overlap_consistency(sample.spec.offline_item_id, hourly, daily)
        )
        fallback_coverage.append(
            _fallback_coverage(sample.spec.offline_item_id, hourly, daily)
        )
        heuristic_market_features.append(
            _heuristic_features(sample.spec.offline_item_id, hourly, daily)
        )
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "source_manifest_sha256": dataset.source_manifest_sha256,
        "typed_trade_dataset_sha256": dataset.typed_trade_dataset_sha256,
        "methodology": {
            "quantiles": (
                "R-7 linear interpolation: index=(n-1)*p; adjacent ordered values are "
                "interpolated with Decimal arithmetic. Volume quantiles may therefore be "
                "fractional and remain reported-volume values of unknown unit."
            ),
            "observed_intervals": (
                "Intervals describe observed bucket spacing and possible no-trade/unreported "
                "periods; they are not automatically classified as missing data."
            ),
            "daily_price_changes": (
                "Daily volatility and price-shock candidates use only 1d bucket pairs exactly "
                "one UTC day apart. Gaijin may omit no-trade dates, so nonconsecutive observed "
                "pairs are excluded."
            ),
            "daily_bucket_density": (
                "Observed 1d bucket count divided by inclusive UTC calendar-span days. Low "
                "density is not automatically data damage because Gaijin may omit no-trade dates."
            ),
            "overlap_consistency": (
                "UTC-day aggregation matching apps/api historical_trades._overlap_consistency: "
                "exclude only a truncated leading hourly day; compare all later overlapping "
                "days even when zero-trade hours are omitted; VWAP mismatch tolerance is "
                "greater than 2 price_raw units."
            ),
            "fallback": "Any hourly bucket makes that UTC date 1h-priority; 1d is fallback only on daily-only dates.",
            "semantics": {
                "price": "bucket_volume_weighted_average_trade_price",
                "volume": "reported_trade_volume_unknown_unit",
            },
        },
        "samples": samples,
        "granularity_statistics": granularity_statistics,
        "overlap_consistency": overlap_consistency,
        "fallback_coverage": fallback_coverage,
        "heuristic_market_features": heuristic_market_features,
        "warnings": [
            "Offline item IDs are dataset-local identifiers, not database Item IDs.",
            "No item_key, source_url, captured_at, or API import request was inferred.",
            "Observed bucket density is descriptive; omitted zero-trade hours may be valid.",
            "Observed daily bucket density is calendar coverage of reported trade buckets, not a data-completeness guarantee.",
            "Heuristic market features are unvalidated descriptive candidates.",
        ],
        "unsupported_conclusions": [
            "These JSON files alone cannot run complete OpportunityScore calibration.",
            "MarketObservation histories, point-in-time splits, and complete future windows are absent.",
            "Reported volume cannot be described as transaction, order, or item count.",
        ],
        "safety_confirmation": {
            "database_access": False,
            "network_access": False,
            "api_import_request_constructed": False,
            "source_json_modified": False,
            "raw_history_sequences_in_output": False,
        },
    }


def render_history_inventory_markdown(inventory: dict[str, object]) -> str:
    samples = inventory["samples"]
    overlaps = {value["offline_item_id"]: value for value in inventory["overlap_consistency"]}
    fallbacks = {value["offline_item_id"]: value for value in inventory["fallback_coverage"]}
    lines = [
        "# Round 6 Offline Historical JSON Inventory",
        "",
        f"- Schema: `{inventory['schema_version']}`",
        f"- Generated at: `{inventory['generated_at']}`",
        f"- Source manifest SHA-256: `{inventory['source_manifest_sha256']}`",
        f"- Typed trade dataset SHA-256: `{inventory['typed_trade_dataset_sha256']}`",
        "",
        "## Contract boundaries",
        "",
        "- Raw files are strict Gaijin response JSON supplied by an offline manifest.",
        "- The manifest supplies dataset-local provenance; it does not invent item_key, source_url, or captured_at.",
        "- Files convert directly to Analytics HistoricalTradeBucket and ItemHistoricalTradeHistory contracts.",
        "- No HistoricalTradeImportRequest was constructed and no database model was invoked.",
        "",
        "## Samples",
        "",
        "| Item | Sample | 1h | 1d | Total | Comparable days | Mismatches | 1h priority | 1d fallback |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sample in samples:
        item_id = sample["offline_item_id"]
        overlap = overlaps[item_id]
        fallback = fallbacks[item_id]
        lines.append(
            f"| {item_id} | {sample['display_name']} | {sample['one_hour_bucket_count']} | "
            f"{sample['one_day_bucket_count']} | {sample['total_bucket_count']} | "
            f"{overlap['comparable_overlap_days']} | {overlap['mismatch_days']} | "
            f"{fallback['one_hour_priority_date_count']} | {fallback['one_day_fallback_date_count']} |"
        )
    lines.extend(
        [
            "",
            "## Methodology",
            "",
            f"- Quantiles: {inventory['methodology']['quantiles']}",
            f"- Overlap: {inventory['methodology']['overlap_consistency']}",
            f"- Fallback: {inventory['methodology']['fallback']}",
            f"- Daily price changes: {inventory['methodology']['daily_price_changes']}",
            f"- Daily bucket density: {inventory['methodology']['daily_bucket_density']}",
            "- Price means bucket_volume_weighted_average_trade_price.",
            "- Volume means reported_trade_volume_unknown_unit.",
            "",
            "## Limitations",
            "",
            "- These files support historical-trade inventory and later trade-evidence research only.",
            "- Complete OpportunityScore calibration still requires MarketObservation histories, configuration, "
            "strategies, fee/rule policies, temporal splits, and complete future windows.",
            "- No database, API, browser, extension, OCR, or network operation was used.",
        ]
    )
    return "\n".join(lines) + "\n"


def _granularity(
    buckets: tuple[HistoricalTradeBucket, ...],
    granularity: HistoricalTradeGranularity,
) -> tuple[HistoricalTradeBucket, ...]:
    return tuple(
        sorted(
            (bucket for bucket in buckets if bucket.granularity is granularity),
            key=lambda value: value.bucket_start_utc,
        )
    )


def _granularity_statistics(
    item_id: int,
    granularity: HistoricalTradeGranularity,
    buckets: tuple[HistoricalTradeBucket, ...],
) -> dict[str, object]:
    starts = tuple(bucket.bucket_start_utc for bucket in buckets)
    intervals = tuple(
        Decimal(int((right - left).total_seconds()))
        for left, right in zip(starts, starts[1:])
    )
    prices = tuple(bucket.reported_vwap_price for bucket in buckets)
    volumes = tuple(Decimal(bucket.reported_trade_volume) for bucket in buckets)
    return {
        "offline_item_id": item_id,
        "granularity": granularity.value,
        "bucket_count": len(buckets),
        "earliest_start": _iso(starts[0]) if starts else None,
        "latest_start": _iso(starts[-1]) if starts else None,
        "observed_utc_date_count": len({value.date() for value in starts}),
        "median_observed_interval_seconds": _decimal_text(_quantile(intervals, Decimal("0.5"))),
        "maximum_observed_interval_seconds": _decimal_text(max(intervals)) if intervals else None,
        "price_quantiles": _quantile_summary(prices),
        "reported_volume_quantiles": _quantile_summary(volumes),
    }


def _overlap_consistency(
    item_id: int,
    hourly: tuple[HistoricalTradeBucket, ...],
    daily: tuple[HistoricalTradeBucket, ...],
) -> dict[str, object]:
    hourly_by_day: dict[datetime.date, list[HistoricalTradeBucket]] = defaultdict(list)
    for bucket in hourly:
        hourly_by_day[bucket.bucket_start_utc.date()].append(bucket)
    daily_by_day = {bucket.bucket_start_utc.date(): bucket for bucket in daily}
    first_timestamp = hourly[0].bucket_start_utc if hourly else None
    first_day = first_timestamp.date() if first_timestamp is not None else None
    leading_partial_excluded = bool(
        first_timestamp is not None
        and (first_timestamp.hour, first_timestamp.minute, first_timestamp.second) != (0, 0, 0)
        and first_day in daily_by_day
    )
    comparable = 0
    mismatch = 0
    volume_mismatch = 0
    vwap_mismatch = 0
    maximum_difference = Decimal("0")
    for day, buckets in sorted(hourly_by_day.items()):
        daily_bucket = daily_by_day.get(day)
        if daily_bucket is None:
            continue
        if leading_partial_excluded and day == first_day:
            continue
        comparable += 1
        volume = sum(bucket.reported_trade_volume for bucket in buckets)
        weighted_raw = sum(
            _price_raw(bucket.reported_vwap_price) * bucket.reported_trade_volume
            for bucket in buckets
        )
        expected_raw = Decimal(weighted_raw) / Decimal(volume)
        daily_raw = Decimal(_price_raw(daily_bucket.reported_vwap_price))
        difference = abs(expected_raw - daily_raw)
        maximum_difference = max(maximum_difference, difference)
        volume_differs = volume != daily_bucket.reported_trade_volume
        vwap_differs = difference > Decimal("2")
        volume_mismatch += volume_differs
        vwap_mismatch += vwap_differs
        mismatch += volume_differs or vwap_differs
    return {
        "offline_item_id": item_id,
        "comparable_overlap_days": comparable,
        "mismatch_days": mismatch,
        "volume_mismatch_days": volume_mismatch,
        "vwap_mismatch_days": vwap_mismatch,
        "maximum_vwap_difference_price_raw_units": _decimal_text(maximum_difference),
        "leading_partial_day_excluded": leading_partial_excluded,
        "vwap_mismatch_tolerance_price_raw_units": 2,
    }


def _fallback_coverage(
    item_id: int,
    hourly: tuple[HistoricalTradeBucket, ...],
    daily: tuple[HistoricalTradeBucket, ...],
) -> dict[str, object]:
    hourly_days = {bucket.bucket_start_utc.date() for bucket in hourly}
    daily_days = {bucket.bucket_start_utc.date() for bucket in daily}
    return {
        "offline_item_id": item_id,
        "both_granularities_date_count": len(hourly_days & daily_days),
        "daily_only_date_count": len(daily_days - hourly_days),
        "hourly_only_date_count": len(hourly_days - daily_days),
        "one_hour_priority_date_count": len(hourly_days),
        "one_day_fallback_date_count": len(daily_days - hourly_days),
    }


def _heuristic_features(
    item_id: int,
    hourly: tuple[HistoricalTradeBucket, ...],
    daily: tuple[HistoricalTradeBucket, ...],
) -> dict[str, object]:
    hourly_density = None
    if hourly:
        observed_slots = int(
            (hourly[-1].bucket_start_utc - hourly[0].bucket_start_utc).total_seconds()
            // 3_600
        ) + 1
        hourly_density = Decimal(len(hourly)) / Decimal(observed_slots)
    daily_volumes = tuple(Decimal(bucket.reported_trade_volume) for bucket in daily)
    volume_median = _quantile(daily_volumes, Decimal("0.5"))
    volume_p90 = _quantile(daily_volumes, Decimal("0.9"))
    concentration = (
        max(daily_volumes) / sum(daily_volumes, Decimal("0"))
        if daily_volumes and sum(daily_volumes, Decimal("0")) > 0
        else None
    )
    observed_pairs = tuple(zip(daily, daily[1:]))
    pair_gaps = tuple(
        int((right.bucket_start_utc - left.bucket_start_utc).total_seconds())
        for left, right in observed_pairs
    )
    consecutive_changes = tuple(
        abs(right.reported_vwap_price - left.reported_vwap_price)
        / left.reported_vwap_price
        for left, right in observed_pairs
        if (right.bucket_start_utc - left.bucket_start_utc).total_seconds() == 86_400
        and left.reported_vwap_price > 0
    )
    observed_changes = tuple(
        abs(right.reported_vwap_price - left.reported_vwap_price)
        / left.reported_vwap_price
        for left, right in observed_pairs
        if left.reported_vwap_price > 0
    )
    consecutive_pair_count = sum(gap == 86_400 for gap in pair_gaps)
    price_volatility = _quantile(consecutive_changes, Decimal("0.5"))
    maximum_consecutive_change = max(consecutive_changes) if consecutive_changes else None
    maximum_observed_change = max(observed_changes) if observed_changes else None
    daily_calendar_span_days = (
        (daily[-1].bucket_start_utc.date() - daily[0].bucket_start_utc.date()).days + 1
        if daily
        else 0
    )
    daily_density = (
        Decimal(len(daily)) / Decimal(daily_calendar_span_days)
        if daily_calendar_span_days
        else None
    )
    median_volume = _median(daily_volumes)
    mad_volume = _median(tuple(abs(value - median_volume) for value in daily_volumes))
    spike_count = 0
    if median_volume is not None and mad_volume is not None:
        for value in daily_volumes:
            if mad_volume > 0:
                robust_z = Decimal("0.6745") * (value - median_volume) / mad_volume
                spike_count += robust_z >= Decimal("3.5")
            elif median_volume > 0:
                spike_count += value >= median_volume * Decimal("3")
    shock_count = sum(change >= Decimal("0.20") for change in consecutive_changes)
    return {
        "offline_item_id": item_id,
        "heuristic": True,
        "observed_hourly_bucket_density": _decimal_text(hourly_density),
        "daily_reported_volume_median": _decimal_text(volume_median),
        "daily_reported_volume_p90": _decimal_text(volume_p90),
        "daily_reported_volume_concentration": _decimal_text(concentration),
        "adjacent_observed_bucket_pair_count": len(observed_pairs),
        "consecutive_daily_pair_count": consecutive_pair_count,
        "nonconsecutive_observed_pair_count": len(observed_pairs) - consecutive_pair_count,
        "maximum_observed_daily_bucket_gap_seconds": max(pair_gaps) if pair_gaps else None,
        "daily_calendar_span_days": daily_calendar_span_days,
        "observed_daily_bucket_count": len(daily),
        "observed_daily_bucket_density": _decimal_text(daily_density),
        "daily_price_volatility_median_absolute_relative_change": _decimal_text(price_volatility),
        "maximum_consecutive_daily_price_relative_change": _decimal_text(maximum_consecutive_change),
        "maximum_adjacent_observed_bucket_relative_change": _decimal_text(maximum_observed_change),
        "reported_volume_spike_candidate_count": spike_count,
        "consecutive_daily_price_shock_candidate_count": shock_count,
        "rules": {
            "volume_spike": "robust z-score >=3.5 using median/MAD; if MAD=0, value >=3x median",
            "price_shock": "absolute consecutive-UTC-day VWAP relative change >=20%",
        },
    }


def _quantile_summary(values: tuple[Decimal, ...]) -> dict[str, str | None]:
    return {name: _decimal_text(_quantile(values, probability)) for name, probability in _QUANTILES}


def _quantile(values: Iterable[Decimal], probability: Decimal) -> Decimal | None:
    ordered = tuple(sorted(values))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    return _quantile(values, Decimal("0.5"))


def _price_raw(value: Decimal) -> int:
    return int(value * Decimal(10_000))


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()
