from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

from .supply_pool_catalog import SupplyPoolCatalog
from .supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


COHORT_COVERAGE_VERSION = "1.0.0"


class CohortCoverageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EventCoverage:
    event_key: str
    family: str
    coupon_mode: str
    opens_at: datetime
    closes_at: datetime
    pre_window_start: datetime
    event_completed: bool
    pre_window_covered: bool
    active_window_covered: bool
    post_window_observed_days: int
    fully_usable: bool


@dataclass(frozen=True, slots=True)
class HistoryCoverage:
    """Whether one collected history spans what the supply-cycle stages need.

    A history that begins after an event's pre-event baseline window cannot
    produce a usable recovery, scarcity or absorption measurement for that
    event, however many rows it contains. This is a data-availability check
    only: it says an event *can* be measured, never that the measurement will
    pass an evidence gate.
    """

    vehicle_name: str
    as_of: datetime
    match_status: str
    matched_catalog_name: str | None
    observed_day_count: int
    history_start_utc: datetime | None
    history_end_utc: datetime | None
    required_history_start_utc: datetime | None
    covers_required_start: bool
    tradeable_event_count: int
    completed_tradeable_event_count: int
    fully_usable_event_count: int
    events: tuple[EventCoverage, ...]
    explanation_codes: tuple[str, ...]
    version: str = COHORT_COVERAGE_VERSION


def assess_history_coverage(
    *,
    vehicle_name: str,
    bucket_start_times: Iterable[datetime],
    as_of: datetime,
    catalog: SupplyPoolCatalog | None = None,
    item_id: int = 1,
) -> HistoryCoverage:
    """Assess one vehicle's collected history against its catalogued events.

    ``bucket_start_times`` are raw bucket starts at any granularity; they are
    collapsed to observed UTC days internally, so passing 1h and 1d buckets
    together does not double count. Only whole UTC days completed by ``as_of``
    count toward post-event coverage, matching the phase rules the measurement
    stages use.
    """

    if not vehicle_name.strip():
        raise CohortCoverageError("vehicle_name must be non-empty.")
    if item_id <= 0:
        raise CohortCoverageError("item_id must be positive.")
    as_of = _utc(as_of, "as_of")
    resolved = catalog or DEFAULT_SUPPLY_POOL_CATALOG

    starts = tuple(_utc(value, "bucket_start_times") for value in bucket_start_times)
    day_starts = sorted({value.replace(hour=0, minute=0, second=0, microsecond=0) for value in starts})
    history_start = min(starts) if starts else None
    history_end = max(starts) if starts else None

    facts = resolved.bind_item(item_id=item_id, item_name=vehicle_name)
    event_by_key = {event.event_key: event for event in facts.events}
    codes: list[str] = []
    if facts.match_status != "MATCHED":
        codes.append("named_history_not_bound_to_supply_catalog")

    events: list[EventCoverage] = []
    required_start: datetime | None = None
    for member in facts.memberships:
        event = event_by_key.get(member.event_key)
        if event is None or member.coupon_mode != "DIRECT_OR_COUPON":
            continue
        pre_start = event.opens_at - (event.closes_at - event.opens_at)
        if required_start is None or pre_start < required_start:
            required_start = pre_start
        pre_covered = history_start is not None and history_start <= pre_start
        active_covered = history_start is not None and history_start <= event.opens_at
        completed = event.closes_at <= as_of
        post_days = sum(
            1
            for day in day_starts
            if day >= event.closes_at and day + timedelta(days=1) <= as_of
        )
        events.append(
            EventCoverage(
                event_key=event.event_key,
                family=event.family,
                coupon_mode=member.coupon_mode,
                opens_at=event.opens_at,
                closes_at=event.closes_at,
                pre_window_start=pre_start,
                event_completed=completed,
                pre_window_covered=pre_covered,
                active_window_covered=active_covered,
                post_window_observed_days=post_days,
                fully_usable=(
                    completed and pre_covered and active_covered and post_days > 0
                ),
            )
        )
    events.sort(key=lambda value: (value.opens_at, value.event_key))

    covers_required = (
        required_start is not None
        and history_start is not None
        and history_start <= required_start
    )
    if required_start is not None and not covers_required:
        codes.append("named_history_starts_after_first_pre_event_baseline")
    if not events:
        codes.append("no_tradeable_supply_event_bound")
    if not starts:
        codes.append("named_history_contains_no_observations")

    return HistoryCoverage(
        vehicle_name=vehicle_name,
        as_of=as_of,
        match_status=facts.match_status,
        matched_catalog_name=facts.matched_catalog_name,
        observed_day_count=len(day_starts),
        history_start_utc=history_start,
        history_end_utc=history_end,
        required_history_start_utc=required_start,
        covers_required_start=covers_required,
        tradeable_event_count=len(events),
        completed_tradeable_event_count=sum(value.event_completed for value in events),
        fully_usable_event_count=sum(value.fully_usable for value in events),
        events=tuple(events),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def suggest_catalog_vehicle_names(
    *,
    item_key: str,
    catalog: SupplyPoolCatalog | None = None,
    limit: int = 3,
) -> tuple[tuple[str, float], ...]:
    """Rank catalogued vehicle names against a marketplace item-key slug.

    These are suggestions for an operator to confirm, never an automatic
    binding: a wrong binding would silently attach one vehicle's history to
    another vehicle's supply events.

    The catalog's own name resolution is consulted first, because it knows
    about spellings token overlap cannot see. ``T-34E`` resolves through the
    catalog's alias table to ``Т-34E``, whose name is written with a Cyrillic
    Te; sharing no ASCII tokens with it, the overlap score buries the correct
    answer under unrelated vehicles. An exact resolution is therefore offered
    first, at a score of one.

    Token overlap remains what it is: a fuzzy match on a designation. It
    cannot tell two aircraft that share a designation apart, and a fragment
    matching across nations or manufacturers is not identity. That is why
    these stay suggestions.
    """

    if limit <= 0:
        raise CohortCoverageError("limit must be positive.")
    resolved = catalog or DEFAULT_SUPPLY_POOL_CATALOG
    tokens = _slug_tokens(item_key)
    if not tokens:
        return ()
    exact: list[str] = []
    for probe in _resolution_probes(item_key):
        name, status = resolved.resolve_vehicle_name(probe)
        if status == "MATCHED" and name is not None and name not in exact:
            exact.append(name)
    scored: list[tuple[float, str]] = []
    for name in _catalog_vehicle_names(resolved):
        candidate = _slug_tokens(name)
        if not candidate:
            continue
        shared = tokens & candidate
        if not shared:
            continue
        score = len(shared) / len(tokens | candidate)
        scored.append((score, name))
    scored.sort(key=lambda value: (-value[0], value[1]))
    ranked = [(name, 1.0) for name in exact]
    ranked.extend(
        (name, score) for score, name in scored if name not in set(exact)
    )
    return tuple(ranked[:limit])


def _resolution_probes(item_key: str) -> tuple[str, ...]:
    """Spellings of an item key worth asking the catalog to resolve exactly.

    Marketplace keys come in two shapes: an operator label that is already the
    vehicle name, and a slug like ``id50361_f_6c_china``. Both are tried as
    written, and the slug also without its numeric prefix, because the prefix
    is a marketplace id and never part of a name.
    """

    probes = [item_key, item_key.strip()]
    stripped = re.sub(r"^id\d+_", "", item_key.strip())
    if stripped != item_key.strip():
        probes.append(stripped)
        probes.append(stripped.replace("_", " "))
    probes.append(re.sub(r"^items_", "", item_key.strip()))
    seen: list[str] = []
    for probe in probes:
        value = probe.strip()
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def _catalog_vehicle_names(catalog: SupplyPoolCatalog) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for event in catalog.catalog_events:
        for member in event.members:
            names.setdefault(member.vehicle_name, None)
    return tuple(names)


def _slug_tokens(value: str) -> frozenset[str]:
    cleaned = "".join(
        character.lower() if character.isalnum() else " " for character in value
    )
    tokens = {
        token
        for token in cleaned.split()
        # Marketplace slugs carry a numeric item id prefix that matches nothing.
        if token and not token.isdigit() and not (token.startswith("id") and token[2:].isdigit())
    }
    return frozenset(tokens)


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise CohortCoverageError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CohortCoverageError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
