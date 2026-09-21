from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Literal
import unicodedata

from .supply_cycles import CouponMode, PoolFamily, SupplyPoolEvent, SupplyPoolMembership

CatalogCompleteness = Literal["COMPLETE_VEHICLE_LIST", "PARTIAL_VEHICLE_LIST"]
KnownAtBasis = Literal[
    "official_forum_timestamp",
    "official_article_open_time_fallback",
    "official_article_publication_date_conservative",
]


class SupplyPoolCatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SupplyPoolCatalogMember:
    vehicle_name: str
    coupon_mode: CouponMode
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.vehicle_name.strip():
            raise SupplyPoolCatalogError("vehicle_name must be non-empty.")
        if any(not alias.strip() for alias in self.aliases):
            raise SupplyPoolCatalogError("aliases must be non-empty strings.")


@dataclass(frozen=True, slots=True)
class SupplyPoolCatalogEvent:
    event_key: str
    event_name: str
    family: PoolFamily
    known_at: datetime
    opens_at: datetime
    closes_at: datetime
    source_url: str
    known_at_basis: KnownAtBasis
    completeness: CatalogCompleteness
    members: tuple[SupplyPoolCatalogMember, ...]
    declared_vehicle_count: int | None = None
    known_at_source_url: str | None = None

    def __post_init__(self) -> None:
        if not self.event_name.strip():
            raise SupplyPoolCatalogError("event_name must be non-empty.")
        if not self.source_url.startswith("https://"):
            raise SupplyPoolCatalogError("source_url must be https.")
        if self.known_at_source_url is not None and not self.known_at_source_url.startswith("https://"):
            raise SupplyPoolCatalogError("known_at_source_url must be https when supplied.")
        if self.known_at_basis == "official_forum_timestamp" and self.known_at_source_url is None:
            raise SupplyPoolCatalogError(
                "official_forum_timestamp requires an auditable known_at_source_url."
            )
        if not self.members:
            raise SupplyPoolCatalogError("catalog event must contain at least one member.")
        if self.declared_vehicle_count is not None and self.declared_vehicle_count <= 0:
            raise SupplyPoolCatalogError("declared_vehicle_count must be positive.")
        normalized = [_normalize_name(member.vehicle_name) for member in self.members]
        if len(normalized) != len(set(normalized)):
            raise SupplyPoolCatalogError(f"duplicate vehicle name in {self.event_key}.")
        if (
            self.completeness == "COMPLETE_VEHICLE_LIST"
            and self.declared_vehicle_count is not None
            and self.declared_vehicle_count != len(self.members)
        ):
            raise SupplyPoolCatalogError(
                f"declared vehicle count mismatch for {self.event_key}: "
                f"{self.declared_vehicle_count} != {len(self.members)}"
            )
        # Reuse the runtime contract validation.
        self.to_supply_pool_event()

    def to_supply_pool_event(self) -> SupplyPoolEvent:
        return SupplyPoolEvent(
            event_key=self.event_key,
            family=self.family,
            known_at=self.known_at,
            opens_at=self.opens_at,
            closes_at=self.closes_at,
            source_url=self.source_url,
            membership_complete=self.completeness == "COMPLETE_VEHICLE_LIST",
        )


@dataclass(frozen=True, slots=True)
class BoundSupplyPoolFacts:
    item_id: int
    item_name: str
    matched_catalog_name: str | None
    events: tuple[SupplyPoolEvent, ...]
    memberships: tuple[SupplyPoolMembership, ...]
    match_status: Literal["MATCHED", "UNMATCHED", "AMBIGUOUS"]


class SupplyPoolCatalog:
    def __init__(
        self,
        *,
        version: str,
        events: Iterable[SupplyPoolCatalogEvent],
    ) -> None:
        if not version.strip():
            raise SupplyPoolCatalogError("version must be non-empty.")
        self.version = version
        self.catalog_events = tuple(events)
        if not self.catalog_events:
            raise SupplyPoolCatalogError("catalog must contain at least one event.")
        keys = [event.event_key for event in self.catalog_events]
        if len(keys) != len(set(keys)):
            raise SupplyPoolCatalogError("event_key values must be unique.")
        self.events = tuple(event.to_supply_pool_event() for event in self.catalog_events)

        index: dict[str, set[str]] = {}
        canonical_for_norm: dict[str, str] = {}
        for event in self.catalog_events:
            for member in event.members:
                canonical = member.vehicle_name
                for candidate in (canonical, *member.aliases):
                    normalized = _normalize_name(candidate)
                    index.setdefault(normalized, set()).add(canonical)
                    canonical_for_norm.setdefault(_normalize_name(canonical), canonical)
        self._name_index = index
        self._canonical_for_norm = canonical_for_norm

    def resolve_vehicle_name(self, item_name: str) -> tuple[str | None, str]:
        normalized = _normalize_name(item_name)
        candidates = self._name_index.get(normalized, set())
        if len(candidates) == 1:
            return next(iter(candidates)), "MATCHED"
        if len(candidates) > 1:
            return None, "AMBIGUOUS"
        return None, "UNMATCHED"

    def bind_item(self, *, item_id: int, item_name: str) -> BoundSupplyPoolFacts:
        if item_id <= 0:
            raise SupplyPoolCatalogError("item_id must be positive.")
        canonical, status = self.resolve_vehicle_name(item_name)
        if canonical is None:
            return BoundSupplyPoolFacts(
                item_id=item_id,
                item_name=item_name,
                matched_catalog_name=None,
                events=self.events,
                memberships=(),
                match_status=status,  # type: ignore[arg-type]
            )

        memberships: list[SupplyPoolMembership] = []
        for event in self.catalog_events:
            for member in event.members:
                if member.vehicle_name == canonical:
                    memberships.append(
                        SupplyPoolMembership(
                            event_key=event.event_key,
                            item_id=item_id,
                            known_at=event.known_at,
                            coupon_mode=member.coupon_mode,
                        )
                    )
                    break
        return BoundSupplyPoolFacts(
            item_id=item_id,
            item_name=item_name,
            matched_catalog_name=canonical,
            events=self.events,
            memberships=tuple(memberships),
            match_status="MATCHED",
        )

    @property
    def event_count(self) -> int:
        return len(self.catalog_events)

    @property
    def membership_count(self) -> int:
        return sum(len(event.members) for event in self.catalog_events)


def member(
    vehicle_name: str,
    *,
    account_only: bool = False,
    aliases: tuple[str, ...] = (),
) -> SupplyPoolCatalogMember:
    return SupplyPoolCatalogMember(
        vehicle_name=vehicle_name,
        coupon_mode="ACCOUNT_ONLY" if account_only else "DIRECT_OR_COUPON",
        aliases=aliases,
    )


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    for old, new in (
        ("’", "'"),
        ("‘", "'"),
        ("“", '"'),
        ("”", '"'),
        ("–", "-"),
        ("—", "-"),
        ("−", "-"),
    ):
        normalized = normalized.replace(old, new)
    return " ".join(normalized.split()).casefold()
