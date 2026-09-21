from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gaijin_market_analytics.supply_cycles import SupplyPoolEvent
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


REPORT_SCHEMA_VERSION = "named_cohort_candidate_report_v1"
DEFAULT_HORIZONS = (30, 60, 90, 180)


class CandidateReportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    """How much walk-forward evidence one catalogued vehicle could contribute.

    Everything here is derived from the official supply-pool catalog alone. It
    says which named histories are worth collecting next; it cannot say whether
    a collected history will actually pass the per-stage evidence gates, because
    that depends on trade coverage the catalog does not contain.
    """

    vehicle_name: str
    match_status: str
    matched_catalog_name: str | None
    already_in_cohort: bool
    tradeable_event_count: int
    completed_tradeable_event_count: int
    account_only_event_count: int
    distinct_tradeable_family_count: int
    families: tuple[str, ...]
    predictable_event_count: int
    walk_forward_outcome_capacity: int
    capacity_by_horizon: tuple[tuple[int, int], ...]
    family_prior_contribution: int
    first_tradeable_opens_at: datetime | None
    latest_completed_closes_at: datetime | None
    required_history_start_utc: datetime | None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Rank catalogued vehicles by how much walk-forward recovery evidence a "
            "named history could contribute. Reads only the bundled supply-pool "
            "catalog and never accesses Gaijin, the network, or a database."
        )
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="ISO-8601 UTC instant; defaults to the current time.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument(
        "--per-family",
        type=int,
        default=4,
        help="Vehicles per family in the balanced recommended batch.",
    )
    parser.add_argument(
        "--in-cohort",
        action="append",
        default=[],
        help="Vehicle name already covered by an existing named history.",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=None,
        help=(
            "Audit an existing cohort manifest for history coverage instead of "
            "ranking new candidates."
        ),
    )
    args = parser.parse_args()
    as_of = _parse_datetime(args.as_of) if args.as_of else datetime.now(UTC)
    if args.audit is not None:
        payload = json.dumps(
            _normalize(audit_manifest(args.audit, as_of=as_of)),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        if args.output is None:
            print(payload, end="")
        else:
            args.output.write_text(payload, encoding="utf-8")
        return
    report = build_report(
        as_of=as_of,
        already_in_cohort=tuple(args.in_cohort),
        limit=args.limit,
        per_family=args.per_family,
    )
    payload = json.dumps(_normalize(report), ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")


def build_report(
    *,
    as_of: datetime,
    already_in_cohort: tuple[str, ...] = (),
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    limit: int | None = None,
    per_family: int = 4,
) -> dict[str, Any]:
    as_of = _utc(as_of, "as_of")
    if not horizons or any(value <= 0 for value in horizons):
        raise CandidateReportError("horizons must be positive integers.")
    covered = {_key(value) for value in already_in_cohort}
    summaries = [
        summarize_vehicle(
            vehicle_name=name,
            as_of=as_of,
            horizons=horizons,
            already_in_cohort=_key(name) in covered,
        )
        for name in catalog_vehicle_names()
    ]
    ranked = sorted(
        summaries,
        key=lambda value: (
            -value.walk_forward_outcome_capacity,
            -value.distinct_tradeable_family_count,
            -value.completed_tradeable_event_count,
            value.vehicle_name,
        ),
    )
    shortlist = [value for value in ranked if not value.already_in_cohort]
    if per_family <= 0:
        raise CandidateReportError("per_family must be positive.")
    current = [value for value in ranked if value.already_in_cohort]
    batch = _balanced_batch(shortlist, per_family=per_family)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "catalog_version": DEFAULT_SUPPLY_POOL_CATALOG.version,
        "as_of": as_of,
        "horizons_days": list(horizons),
        "methodology": {
            "predictable_event": (
                "a completed tradeable event that has at least one earlier same-family "
                "tradeable event whose checkpoint at that horizon was already observable "
                "before the later event opened"
            ),
            "walk_forward_outcome_capacity": (
                "predictable (event, horizon) pairs whose own outcome is observable by "
                "as_of and before the next same-family event opens"
            ),
            "family_prior_contribution": (
                "completed tradeable events usable as independent cross-item family "
                "evidence for other vehicles"
            ),
            "required_history_start_utc": (
                "earliest UTC instant a collected history must cover: one event duration "
                "before the first tradeable event opens, which is the pre-event baseline "
                "window every stage uses"
            ),
            "recommended_batch": (
                "top vehicles per family rather than globally, because the cross-item "
                "family prior and its leave-one-item-out audit need item breadth inside "
                "each family, not a single deep family"
            ),
            "network_access": False,
        },
        "catalog_vehicle_count": len(summaries),
        "already_covered_count": sum(value.already_in_cohort for value in summaries),
        "candidates_with_walk_forward_capacity": sum(
            value.walk_forward_outcome_capacity > 0 for value in shortlist
        ),
        "current_cohort": {
            "vehicle_count": len(current),
            "walk_forward_outcome_capacity": sum(
                value.walk_forward_outcome_capacity for value in current
            ),
            "vehicles": [asdict(value) for value in current],
        },
        "recommended_batch": {
            "per_family": per_family,
            "vehicle_count": len(batch),
            "walk_forward_outcome_capacity": sum(
                value.walk_forward_outcome_capacity for value in batch
            ),
            "vehicles": [asdict(value) for value in batch],
        },
        "shortlist": [
            asdict(value) for value in (shortlist[:limit] if limit else shortlist)
        ],
        "totals_by_family": _totals_by_family(summaries),
    }


def _balanced_batch(
    shortlist: list[CandidateSummary],
    *,
    per_family: int,
) -> list[CandidateSummary]:
    """Pick the strongest candidates per family, not the strongest overall."""

    taken: dict[str, int] = {}
    chosen: list[CandidateSummary] = []
    for summary in shortlist:
        if summary.walk_forward_outcome_capacity <= 0:
            continue
        primary = summary.families[0] if summary.families else None
        if primary is None:
            continue
        if taken.get(primary, 0) >= per_family:
            continue
        taken[primary] = taken.get(primary, 0) + 1
        chosen.append(summary)
    return chosen


AUDIT_SCHEMA_VERSION = "named_cohort_coverage_audit_v1"


@dataclass(frozen=True, slots=True)
class EventCoverage:
    event_key: str
    family: str
    coupon_mode: str
    opens_at: datetime
    closes_at: datetime
    pre_window_start: datetime
    pre_window_covered: bool
    active_window_covered: bool
    post_window_observed_days: int
    fully_usable: bool


@dataclass(frozen=True, slots=True)
class ItemCoverage:
    item_id: int
    item_name: str
    history_file: str
    match_status: str
    daily_point_count: int
    hourly_point_count: int
    history_start_utc: datetime | None
    history_end_utc: datetime | None
    required_history_start_utc: datetime | None
    covers_required_start: bool
    tradeable_event_count: int
    fully_usable_event_count: int
    events: tuple[EventCoverage, ...]
    explanation_codes: tuple[str, ...]


def audit_manifest(manifest_path: Path, *, as_of: datetime) -> dict[str, Any]:
    """Check whether already-collected histories actually span what the stages need.

    A history that starts after an event's pre-event baseline window cannot
    produce a usable recovery, scarcity or absorption measurement for that
    event, however many rows it contains. Running this before the full cohort
    replay says exactly how far back a re-collection has to reach.
    """

    as_of = _utc(as_of, "as_of")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "named_recovery_cohort_manifest_v1":
        raise CandidateReportError("unsupported manifest schema_version")
    raw_items = manifest.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise CandidateReportError("manifest items must be a non-empty list")

    coverages: list[ItemCoverage] = []
    for entry in raw_items:
        item_id = entry.get("item_id")
        item_name = entry.get("item_name")
        history_path = manifest_path.parent / str(entry.get("history_path"))
        if not isinstance(item_id, int) or not isinstance(item_name, str):
            raise CandidateReportError("manifest item_id/item_name are required")
        coverages.append(
            _audit_item(
                item_id=item_id,
                item_name=item_name,
                history_path=history_path,
                as_of=as_of,
            )
        )
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "catalog_version": DEFAULT_SUPPLY_POOL_CATALOG.version,
        "as_of": as_of,
        "manifest": manifest_path.name,
        "item_count": len(coverages),
        "fully_usable_event_total": sum(
            value.fully_usable_event_count for value in coverages
        ),
        "items_missing_required_start": [
            value.item_name for value in coverages if not value.covers_required_start
        ],
        "items": [asdict(value) for value in coverages],
        "methodology": {
            "pre_window_covered": (
                "the history starts at or before one event duration prior to the event "
                "opening, which is the pre-event baseline window every stage uses"
            ),
            "fully_usable_event": (
                "pre and active windows covered and at least one completed post-event "
                "UTC day observed before as_of"
            ),
            "network_access": False,
        },
    }


def _audit_item(
    *,
    item_id: int,
    item_name: str,
    history_path: Path,
    as_of: datetime,
) -> ItemCoverage:
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "gaijin_trade_history_v1":
        raise CandidateReportError(
            f"history for {item_name} must use gaijin_trade_history_v1"
        )
    series = payload.get("series")
    if not isinstance(series, dict):
        raise CandidateReportError(f"history series invalid for {item_name}")
    daily = series.get("1d") or []
    hourly = series.get("1h") or []
    stamps = [
        datetime.fromtimestamp(point[0], UTC)
        for point in list(daily) + list(hourly)
        if isinstance(point, list) and point
    ]
    start = min(stamps) if stamps else None
    end = max(stamps) if stamps else None

    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=item_id, item_name=item_name)
    event_by_key = {event.event_key: event for event in facts.events}
    observed_days = {stamp.date() for stamp in stamps}
    codes: list[str] = []
    if facts.match_status != "MATCHED":
        codes.append("named_history_not_bound_to_supply_catalog")

    events: list[EventCoverage] = []
    required_start: datetime | None = None
    for member in sorted(facts.memberships, key=lambda value: value.event_key):
        event = event_by_key.get(member.event_key)
        if event is None or member.coupon_mode != "DIRECT_OR_COUPON":
            continue
        duration = event.closes_at - event.opens_at
        pre_start = event.opens_at - duration
        if required_start is None or pre_start < required_start:
            required_start = pre_start
        pre_covered = start is not None and start <= pre_start
        active_covered = start is not None and start <= event.opens_at
        post_days = sum(
            1
            for day in observed_days
            if event.closes_at.date() <= day < min(as_of, end or as_of).date()
        )
        events.append(
            EventCoverage(
                event_key=event.event_key,
                family=event.family,
                coupon_mode=member.coupon_mode,
                opens_at=event.opens_at,
                closes_at=event.closes_at,
                pre_window_start=pre_start,
                pre_window_covered=pre_covered,
                active_window_covered=active_covered,
                post_window_observed_days=post_days,
                fully_usable=(
                    pre_covered
                    and active_covered
                    and post_days > 0
                    and event.closes_at <= as_of
                ),
            )
        )
    events.sort(key=lambda value: (value.opens_at, value.event_key))
    covers_required = (
        required_start is not None and start is not None and start <= required_start
    )
    if required_start is not None and not covers_required:
        codes.append("named_history_starts_after_first_pre_event_baseline")
    if not events:
        codes.append("no_tradeable_supply_event_bound")
    return ItemCoverage(
        item_id=item_id,
        item_name=item_name,
        history_file=history_path.name,
        match_status=facts.match_status,
        daily_point_count=len(daily),
        hourly_point_count=len(hourly),
        history_start_utc=start,
        history_end_utc=end,
        required_history_start_utc=required_start,
        covers_required_start=covers_required,
        tradeable_event_count=len(events),
        fully_usable_event_count=sum(value.fully_usable for value in events),
        events=tuple(events),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def catalog_vehicle_names() -> tuple[str, ...]:
    names: dict[str, None] = {}
    for event in DEFAULT_SUPPLY_POOL_CATALOG.catalog_events:
        for member in event.members:
            names.setdefault(member.vehicle_name, None)
    return tuple(sorted(names))


def summarize_vehicle(
    *,
    vehicle_name: str,
    as_of: datetime,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    already_in_cohort: bool = False,
) -> CandidateSummary:
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=1, item_name=vehicle_name)
    event_by_key = {event.event_key: event for event in facts.events}
    tradeable = tuple(
        sorted(
            (
                event_by_key[member.event_key]
                for member in facts.memberships
                if member.coupon_mode == "DIRECT_OR_COUPON"
                and member.event_key in event_by_key
            ),
            key=lambda value: (value.opens_at, value.event_key),
        )
    )
    account_only = sum(
        member.coupon_mode == "ACCOUNT_ONLY" for member in facts.memberships
    )
    completed = tuple(event for event in tradeable if event.closes_at <= as_of)
    by_family: dict[str, list[SupplyPoolEvent]] = {}
    for event in completed:
        by_family.setdefault(event.family, []).append(event)

    predictable: set[str] = set()
    capacity_by_horizon = {horizon: 0 for horizon in horizons}
    for family, events in by_family.items():
        family_all = tuple(
            sorted(
                (value for value in tradeable if value.family == family),
                key=lambda value: (value.opens_at, value.event_key),
            )
        )
        for index, event in enumerate(events):
            next_open = min(
                (
                    value.opens_at
                    for value in family_all
                    if value.opens_at > event.opens_at
                ),
                default=None,
            )
            cutoff = min(as_of, next_open) if next_open is not None else as_of
            for horizon in horizons:
                delta = timedelta(days=horizon)
                has_prior = any(
                    earlier.closes_at + delta <= event.opens_at
                    for earlier in events[:index]
                )
                if not has_prior:
                    continue
                predictable.add(event.event_key)
                if event.closes_at + delta <= cutoff:
                    capacity_by_horizon[horizon] += 1

    first_open = tradeable[0].opens_at if tradeable else None
    required_start = (
        first_open - (tradeable[0].closes_at - tradeable[0].opens_at)
        if tradeable
        else None
    )
    return CandidateSummary(
        vehicle_name=vehicle_name,
        match_status=facts.match_status,
        matched_catalog_name=facts.matched_catalog_name,
        already_in_cohort=already_in_cohort,
        tradeable_event_count=len(tradeable),
        completed_tradeable_event_count=len(completed),
        account_only_event_count=account_only,
        distinct_tradeable_family_count=len({event.family for event in tradeable}),
        families=tuple(sorted({event.family for event in tradeable})),
        predictable_event_count=len(predictable),
        walk_forward_outcome_capacity=sum(capacity_by_horizon.values()),
        capacity_by_horizon=tuple(
            (horizon, capacity_by_horizon[horizon]) for horizon in horizons
        ),
        family_prior_contribution=len(completed),
        first_tradeable_opens_at=first_open,
        latest_completed_closes_at=completed[-1].closes_at if completed else None,
        required_history_start_utc=required_start,
    )


def _totals_by_family(summaries: list[CandidateSummary]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for summary in summaries:
        for family in summary.families:
            row = totals.setdefault(
                family, {"vehicle_count": 0, "walk_forward_outcome_capacity": 0}
            )
            row["vehicle_count"] += 1
            row["walk_forward_outcome_capacity"] += summary.walk_forward_outcome_capacity
    return dict(sorted(totals.items()))


def _key(value: str) -> str:
    return " ".join(value.split()).casefold()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateReportError("--as-of must be timezone-aware.")
    return parsed.astimezone(UTC)


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CandidateReportError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


if __name__ == "__main__":
    main()
