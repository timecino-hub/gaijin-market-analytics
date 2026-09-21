from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Literal

PoolFamily = Literal["HISTORY_LUNAR", "SPRING_CRAFT", "SUMMER", "WINTER", "UNKNOWN"]
CouponMode = Literal["DIRECT_OR_COUPON", "ACCOUNT_ONLY", "UNKNOWN"]
CycleState = Literal[
    "UNKNOWN",
    "NORMAL_DEPLETION",
    "SEASONAL_WINDOW_APPROACHING",
    "ANNOUNCED_PRE_OPEN",
    "ACTIVE_SUPPLY_INJECTION",
    "POST_EVENT_ABSORPTION",
]


class SupplyCycleContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SupplyPoolEvent:
    event_key: str
    family: PoolFamily
    known_at: datetime
    opens_at: datetime
    closes_at: datetime
    source_url: str | None = None
    membership_complete: bool = False

    def __post_init__(self) -> None:
        if not self.event_key.strip():
            raise SupplyCycleContractError("event_key must be non-empty.")
        object.__setattr__(self, "known_at", _utc(self.known_at, "known_at"))
        object.__setattr__(self, "opens_at", _utc(self.opens_at, "opens_at"))
        object.__setattr__(self, "closes_at", _utc(self.closes_at, "closes_at"))
        if self.closes_at <= self.opens_at:
            raise SupplyCycleContractError("closes_at must be later than opens_at.")
        if self.known_at > self.closes_at:
            raise SupplyCycleContractError("known_at must not be later than closes_at.")


@dataclass(frozen=True, slots=True)
class SupplyPoolMembership:
    event_key: str
    item_id: int
    known_at: datetime
    coupon_mode: CouponMode

    def __post_init__(self) -> None:
        if not self.event_key.strip():
            raise SupplyCycleContractError("event_key must be non-empty.")
        if self.item_id <= 0:
            raise SupplyCycleContractError("item_id must be positive.")
        object.__setattr__(self, "known_at", _utc(self.known_at, "known_at"))


@dataclass(frozen=True, slots=True)
class SupplyCycleFeatures:
    item_id: int
    as_of: datetime
    state: CycleState
    pool_family: PoolFamily
    confirmed_current_membership: bool
    coupon_mode: CouponMode
    observed_return_count: int
    observed_tradeable_return_count: int
    eligible_family_event_count: int
    recurrence_rate: Decimal | None
    current_event_key: str | None
    current_event_opens_at: datetime | None
    current_event_closes_at: datetime | None
    explanation_codes: tuple[str, ...]


def derive_supply_cycle_features(
    *,
    item_id: int,
    as_of: datetime,
    events: Iterable[SupplyPoolEvent] = (),
    memberships: Iterable[SupplyPoolMembership] = (),
) -> SupplyCycleFeatures:
    """Derive point-in-time-safe supply-cycle state.

    Membership/event facts are visible only when their own ``known_at`` is not
    later than ``as_of``. Seasonal prior is intentionally conservative: it is
    emitted only after at least two prior coupon-capable memberships with a
    perfect observed recurrence across known events in the same family.
    """

    if item_id <= 0:
        raise SupplyCycleContractError("item_id must be positive.")
    as_of = _utc(as_of, "as_of")
    all_events = tuple(events)
    all_memberships = tuple(memberships)
    by_key = {event.event_key: event for event in all_events}
    if len(by_key) != len(all_events):
        raise SupplyCycleContractError("event_key values must be unique.")
    if any(member.event_key not in by_key for member in all_memberships):
        raise SupplyCycleContractError("membership references an unknown event_key.")

    visible_events = {
        event.event_key: event for event in all_events if event.known_at <= as_of
    }
    visible_memberships = tuple(
        member
        for member in all_memberships
        if member.item_id == item_id
        and member.known_at <= as_of
        and member.event_key in visible_events
    )
    visible_by_key = {member.event_key: member for member in visible_memberships}
    codes: list[str] = []

    account_only_current: list[tuple[SupplyPoolEvent, SupplyPoolMembership]] = []
    current_candidates: list[tuple[int, SupplyPoolEvent, SupplyPoolMembership, CycleState]] = []
    for event_key, member in visible_by_key.items():
        event = visible_events[event_key]
        inside_current_window = as_of < event.closes_at + (event.closes_at - event.opens_at)
        if member.coupon_mode == "ACCOUNT_ONLY" and inside_current_window:
            account_only_current.append((event, member))
            continue
        if member.coupon_mode == "ACCOUNT_ONLY":
            continue
        if as_of < event.opens_at:
            state: CycleState = "ANNOUNCED_PRE_OPEN"
            priority = 0
        elif event.opens_at <= as_of < event.closes_at:
            state = "ACTIVE_SUPPLY_INJECTION"
            priority = 1
        elif event.closes_at <= as_of < event.closes_at + (event.closes_at - event.opens_at):
            state = "POST_EVENT_ABSORPTION"
            priority = 2
        else:
            continue
        current_candidates.append((priority, event, member, state))

    if current_candidates:
        _, event, member, state = sorted(
            current_candidates,
            key=lambda entry: (entry[0], entry[1].opens_at, entry[1].event_key),
        )[0]
        codes.append(f"supply_cycle_{state.lower()}")
        if member.coupon_mode == "DIRECT_OR_COUPON":
            codes.append("confirmed_tradeable_coupon_supply")
        return _features_for_family(
            item_id=item_id,
            as_of=as_of,
            state=state,
            family=event.family,
            coupon_mode=member.coupon_mode,
            current_event=event,
            visible_events=tuple(visible_events.values()),
            visible_memberships=visible_memberships,
            confirmed=True,
            codes=codes,
        )

    if account_only_current:
        event, member = sorted(
            account_only_current,
            key=lambda entry: (entry[0].opens_at, entry[0].event_key),
        )[0]
        codes.extend((
            "confirmed_account_only_return",
            "confirmed_account_only_return_no_tradeable_supply",
        ))
        return _features_for_family(
            item_id=item_id,
            as_of=as_of,
            state="NORMAL_DEPLETION",
            family=event.family,
            coupon_mode=member.coupon_mode,
            current_event=event,
            visible_events=tuple(visible_events.values()),
            visible_memberships=visible_memberships,
            confirmed=True,
            codes=codes,
        )

    historical = tuple(
        (visible_events[member.event_key], member)
        for member in visible_memberships
        if visible_events[member.event_key].closes_at <= as_of
        and member.coupon_mode == "DIRECT_OR_COUPON"
        and visible_events[member.event_key].family != "UNKNOWN"
    )
    if not historical:
        return SupplyCycleFeatures(
            item_id=item_id,
            as_of=as_of,
            state="UNKNOWN",
            pool_family="UNKNOWN",
            confirmed_current_membership=False,
            coupon_mode="UNKNOWN",
            observed_return_count=0,
            observed_tradeable_return_count=0,
            eligible_family_event_count=0,
            recurrence_rate=None,
            current_event_key=None,
            current_event_opens_at=None,
            current_event_closes_at=None,
            explanation_codes=("supply_cycle_history_unavailable",),
        )

    family_counts: dict[str, int] = {}
    for event, _ in historical:
        family_counts[event.family] = family_counts.get(event.family, 0) + 1
    family = sorted(family_counts, key=lambda value: (-family_counts[value], value))[0]
    family_events = tuple(event for event in visible_events.values() if event.family == family)
    family_all_memberships = tuple(
        member
        for member in visible_memberships
        if visible_events[member.event_key].family == family
    )
    family_memberships = tuple(
        member for member in family_all_memberships
        if member.coupon_mode == "DIRECT_OR_COUPON"
    )
    first_member_open = min(
        visible_events[m.event_key].opens_at for m in family_all_memberships
    )
    eligible_events = tuple(
        event
        for event in family_events
        if event.opens_at >= first_member_open and event.closes_at <= as_of
    )
    recurrence = (
        Decimal(len(family_memberships)) / Decimal(len(eligible_events))
        if eligible_events
        else None
    )

    complete_current_absences = tuple(
        event
        for event in visible_events.values()
        if event.family == family
        and event.membership_complete
        and event.event_key not in visible_by_key
        and event.known_at <= as_of < event.closes_at + (event.closes_at - event.opens_at)
    )
    if complete_current_absences:
        current_event = max(
            complete_current_absences,
            key=lambda event: (event.opens_at, event.event_key),
        )
        codes.extend((
            "complete_supply_pool_published",
            "confirmed_not_in_current_complete_pool",
        ))
        return SupplyCycleFeatures(
            item_id=item_id,
            as_of=as_of,
            state="NORMAL_DEPLETION",
            pool_family=family,  # type: ignore[arg-type]
            confirmed_current_membership=False,
            coupon_mode="UNKNOWN",
            observed_return_count=sum(
                visible_events[member.event_key].family == family
                for member in visible_memberships
            ),
            observed_tradeable_return_count=len(family_memberships),
            eligible_family_event_count=len(eligible_events),
            recurrence_rate=recurrence,
            current_event_key=current_event.event_key,
            current_event_opens_at=current_event.opens_at,
            current_event_closes_at=current_event.closes_at,
            explanation_codes=tuple(codes),
        )

    state: CycleState = "NORMAL_DEPLETION"
    if (
        len(family_memberships) >= 2
        and recurrence == Decimal("1")
        and _inside_historical_calendar_envelope(
            as_of,
            tuple(visible_events[m.event_key] for m in family_memberships),
        )
    ):
        state = "SEASONAL_WINDOW_APPROACHING"
        codes.append("perfect_observed_annual_pool_recurrence")
        codes.append("inside_historical_pool_calendar_envelope")
    else:
        codes.append("outside_confirmed_supply_event")

    return SupplyCycleFeatures(
        item_id=item_id,
        as_of=as_of,
        state=state,
        pool_family=family,  # type: ignore[arg-type]
        confirmed_current_membership=False,
        coupon_mode="UNKNOWN",
        observed_return_count=len(family_all_memberships),
        observed_tradeable_return_count=len(family_memberships),
        eligible_family_event_count=len(eligible_events),
        recurrence_rate=recurrence,
        current_event_key=None,
        current_event_opens_at=None,
        current_event_closes_at=None,
        explanation_codes=tuple(codes),
    )


def _features_for_family(
    *,
    item_id: int,
    as_of: datetime,
    state: CycleState,
    family: PoolFamily,
    coupon_mode: CouponMode,
    current_event: SupplyPoolEvent,
    visible_events: tuple[SupplyPoolEvent, ...],
    visible_memberships: tuple[SupplyPoolMembership, ...],
    confirmed: bool,
    codes: list[str],
) -> SupplyCycleFeatures:
    all_member_events = tuple(
        event
        for member in visible_memberships
        for event in visible_events
        if event.event_key == member.event_key and event.family == family
    )
    member_events = tuple(
        event
        for member in visible_memberships
        if member.coupon_mode == "DIRECT_OR_COUPON"
        for event in visible_events
        if event.event_key == member.event_key and event.family == family
    )
    first_open = min(
        (event.opens_at for event in all_member_events),
        default=current_event.opens_at,
    )
    eligible_events = tuple(
        event
        for event in visible_events
        if event.family == family and event.opens_at >= first_open and event.opens_at <= as_of
    )
    recurrence = (
        Decimal(len(member_events)) / Decimal(len(eligible_events))
        if eligible_events
        else None
    )
    return SupplyCycleFeatures(
        item_id=item_id,
        as_of=as_of,
        state=state,
        pool_family=family,
        confirmed_current_membership=confirmed,
        coupon_mode=coupon_mode,
        observed_return_count=len(all_member_events),
        observed_tradeable_return_count=len(member_events),
        eligible_family_event_count=len(eligible_events),
        recurrence_rate=recurrence,
        current_event_key=current_event.event_key,
        current_event_opens_at=current_event.opens_at,
        current_event_closes_at=current_event.closes_at,
        explanation_codes=tuple(codes),
    )


def _inside_historical_calendar_envelope(
    as_of: datetime,
    events: tuple[SupplyPoolEvent, ...],
) -> bool:
    if len(events) < 2:
        return False
    starts = tuple(_calendar_ordinal(event.opens_at) for event in events)
    ends = tuple(_calendar_ordinal(event.closes_at) for event in events)
    day = _calendar_ordinal(as_of)
    # Handle winter windows crossing New Year by shifting Jan/Feb dates forward.
    if max(starts) - min(starts) > 183 or max(ends) - min(ends) > 183:
        starts = tuple(value + 366 if value < 183 else value for value in starts)
        ends = tuple(value + 366 if value < 183 else value for value in ends)
        if day < 183:
            day += 366
    return min(starts) <= day <= max(ends)


def _calendar_ordinal(value: datetime) -> int:
    normalized = value.astimezone(UTC)
    # 2000 is leap year; this normalizes all years onto a stable 366-day circle.
    return datetime(2000, normalized.month, normalized.day, tzinfo=UTC).timetuple().tm_yday


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SupplyCycleContractError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
