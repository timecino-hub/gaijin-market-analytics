from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Literal

from .supply_cycles import SupplyPoolEvent, SupplyPoolMembership

ReissueEffect = Literal["TRADEABLE_SUPPLY", "ACCOUNT_ONLY_DEMAND_SUBSTITUTION"]
REISSUE_EXPOSURE_VERSION = "1.0.0"


class ReissueExposureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ItemReissueExposureEvent:
    item_id: int
    event_key: str
    family: str
    known_at: datetime
    opens_at: datetime
    closes_at: datetime
    effect: ReissueEffect
    source_url: str | None = None

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise ReissueExposureError("item_id must be positive.")
        if not self.event_key.strip():
            raise ReissueExposureError("event_key must be non-empty.")
        object.__setattr__(self, "known_at", _utc(self.known_at, "known_at"))
        object.__setattr__(self, "opens_at", _utc(self.opens_at, "opens_at"))
        object.__setattr__(self, "closes_at", _utc(self.closes_at, "closes_at"))
        if self.closes_at <= self.opens_at:
            raise ReissueExposureError("closes_at must be later than opens_at.")
        if self.known_at > self.closes_at:
            raise ReissueExposureError("known_at must not be later than closes_at.")


@dataclass(frozen=True, slots=True)
class ReissueExposureFeatures:
    item_id: int
    as_of: datetime
    known_return_count: int
    completed_return_count: int
    known_tradeable_supply_count: int
    completed_tradeable_supply_count: int
    known_account_only_return_count: int
    completed_account_only_return_count: int
    distinct_family_count: int
    trailing_365d_tradeable_supply_count: int
    trailing_730d_tradeable_supply_count: int
    trailing_1095d_tradeable_supply_count: int
    trailing_1095d_account_only_return_count: int
    days_since_last_tradeable_supply_close: Decimal | None
    days_since_last_any_return_close: Decimal | None
    first_known_return_open: datetime | None
    last_known_return_open: datetime | None
    explanation_codes: tuple[str, ...]
    version: str = REISSUE_EXPOSURE_VERSION


def reissue_events_from_supply_pool(
    *,
    item_id: int,
    events: Iterable[SupplyPoolEvent],
    memberships: Iterable[SupplyPoolMembership],
) -> tuple[ItemReissueExposureEvent, ...]:
    """Translate point-in-time supply memberships into cumulative reissue events.

    Coupon-capable returns are represented as direct Marketplace supply exposure.
    Account-only returns are retained separately because they can substitute for
    Marketplace demand even though they do not create a tradable coupon.
    """

    if item_id <= 0:
        raise ReissueExposureError("item_id must be positive.")
    event_values = tuple(events)
    event_by_key = {event.event_key: event for event in event_values}
    if len(event_by_key) != len(event_values):
        raise ReissueExposureError("event_key values must be unique.")

    result: list[ItemReissueExposureEvent] = []
    seen: set[str] = set()
    for membership in memberships:
        if membership.item_id != item_id:
            raise ReissueExposureError("membership item_id does not match requested item_id.")
        event = event_by_key.get(membership.event_key)
        if event is None:
            raise ReissueExposureError("membership references an unknown event_key.")
        if membership.event_key in seen:
            raise ReissueExposureError("duplicate membership event_key for item.")
        seen.add(membership.event_key)
        if membership.coupon_mode == "UNKNOWN":
            continue
        result.append(
            ItemReissueExposureEvent(
                item_id=item_id,
                event_key=event.event_key,
                family=event.family,
                known_at=max(event.known_at, membership.known_at),
                opens_at=event.opens_at,
                closes_at=event.closes_at,
                effect=(
                    "TRADEABLE_SUPPLY"
                    if membership.coupon_mode == "DIRECT_OR_COUPON"
                    else "ACCOUNT_ONLY_DEMAND_SUBSTITUTION"
                ),
                source_url=event.source_url,
            )
        )
    return tuple(sorted(result, key=lambda value: (value.opens_at, value.event_key)))


def derive_reissue_exposure_features(
    *,
    item_id: int,
    as_of: datetime,
    events: Iterable[ItemReissueExposureEvent],
) -> ReissueExposureFeatures:
    """Describe cumulative known reissue exposure without inventing a penalty score.

    This layer is diagnostic.  It deliberately exposes raw counts and time-since
    features instead of converting them into a scarcity or price multiplier.  A
    later calibration stage can decide whether any of these observables actually
    generalize across items.
    """

    if item_id <= 0:
        raise ReissueExposureError("item_id must be positive.")
    as_of = _utc(as_of, "as_of")
    values = tuple(events)
    if any(value.item_id != item_id for value in values):
        raise ReissueExposureError("reissue event item_id does not match requested item_id.")
    keys = [value.event_key for value in values]
    if len(keys) != len(set(keys)):
        raise ReissueExposureError("reissue event keys must be unique for an item.")

    visible = tuple(
        sorted(
            (
                value
                for value in values
                if value.known_at <= as_of and value.opens_at <= as_of
            ),
            key=lambda value: (value.opens_at, value.event_key),
        )
    )
    completed = tuple(value for value in visible if value.closes_at <= as_of)
    tradeable = tuple(value for value in visible if value.effect == "TRADEABLE_SUPPLY")
    completed_tradeable = tuple(
        value for value in completed if value.effect == "TRADEABLE_SUPPLY"
    )
    account_only = tuple(
        value for value in visible if value.effect == "ACCOUNT_ONLY_DEMAND_SUBSTITUTION"
    )
    completed_account_only = tuple(
        value
        for value in completed
        if value.effect == "ACCOUNT_ONLY_DEMAND_SUBSTITUTION"
    )

    window_365 = as_of - timedelta(days=365)
    window_730 = as_of - timedelta(days=730)
    window_1095 = as_of - timedelta(days=1095)
    trailing_365_tradeable = _count_opened_since(tradeable, window_365)
    trailing_730_tradeable = _count_opened_since(tradeable, window_730)
    trailing_1095_tradeable = _count_opened_since(tradeable, window_1095)
    trailing_1095_account_only = _count_opened_since(account_only, window_1095)

    families = {value.family for value in visible if value.family != "UNKNOWN"}
    last_tradeable = max(
        (value.closes_at for value in completed_tradeable),
        default=None,
    )
    last_any = max((value.closes_at for value in completed), default=None)

    codes: list[str] = []
    if not visible:
        codes.append("no_known_reissue_exposure")
    else:
        codes.append("known_reissue_exposure_present")
    if tradeable:
        codes.append("tradeable_supply_reissue_history_present")
    if account_only:
        codes.append("account_only_demand_substitution_history_present")
    if len(families) > 1:
        codes.append("cross_family_reissue_history_present")
    if trailing_1095_tradeable >= 2:
        codes.append("repeated_tradeable_supply_within_three_years")

    return ReissueExposureFeatures(
        item_id=item_id,
        as_of=as_of,
        known_return_count=len(visible),
        completed_return_count=len(completed),
        known_tradeable_supply_count=len(tradeable),
        completed_tradeable_supply_count=len(completed_tradeable),
        known_account_only_return_count=len(account_only),
        completed_account_only_return_count=len(completed_account_only),
        distinct_family_count=len(families),
        trailing_365d_tradeable_supply_count=trailing_365_tradeable,
        trailing_730d_tradeable_supply_count=trailing_730_tradeable,
        trailing_1095d_tradeable_supply_count=trailing_1095_tradeable,
        trailing_1095d_account_only_return_count=trailing_1095_account_only,
        days_since_last_tradeable_supply_close=_days_since(last_tradeable, as_of),
        days_since_last_any_return_close=_days_since(last_any, as_of),
        first_known_return_open=visible[0].opens_at if visible else None,
        last_known_return_open=visible[-1].opens_at if visible else None,
        explanation_codes=tuple(codes),
    )


def merge_reissue_exposure_events(
    *groups: Iterable[ItemReissueExposureEvent],
) -> tuple[ItemReissueExposureEvent, ...]:
    """Merge independent catalogs while rejecting double-counted event keys."""

    values = tuple(value for group in groups for value in group)
    by_key: dict[str, ItemReissueExposureEvent] = {}
    for value in values:
        if value.event_key in by_key:
            raise ReissueExposureError(
                f"duplicate reissue event_key across exposure sources: {value.event_key}"
            )
        by_key[value.event_key] = value
    return tuple(sorted(values, key=lambda value: (value.opens_at, value.event_key)))


def _count_opened_since(
    values: Iterable[ItemReissueExposureEvent],
    start: datetime,
) -> int:
    return sum(value.opens_at >= start for value in values)


def _days_since(value: datetime | None, as_of: datetime) -> Decimal | None:
    if value is None:
        return None
    seconds = Decimal(str((as_of - value).total_seconds()))
    return seconds / Decimal("86400")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReissueExposureError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
