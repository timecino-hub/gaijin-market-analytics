from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Literal

from .supply_pool_catalog import SupplyPoolCatalog
from .supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


SUPPLY_CHANNELS_VERSION = "1.0.0"

SupplyChannel = Literal[
    "CRAFT_EVENT",
    "BATTLE_PASS_COUPON",
    "BATTLE_TROPHY",
    "SEASONAL_CRATE",
]
# The only channel the supply-pool catalog and every downstream supply-cycle
# model describe today. Everything else is known to exist and is not modelled.
MODELLED_CHANNELS: tuple[SupplyChannel, ...] = ("CRAFT_EVENT",)
ALL_CHANNELS: tuple[SupplyChannel, ...] = (
    "CRAFT_EVENT",
    "BATTLE_PASS_COUPON",
    "BATTLE_TROPHY",
    "SEASONAL_CRATE",
)
ChannelCoverage = Literal[
    "UNCATALOGUED",
    "PARTIALLY_CATALOGUED",
    "FULLY_CATALOGUED",
]
ChannelCoveragePolicy = Literal[
    "IGNORE",
    "WITHHOLD_UNCATALOGUED",
    "WITHHOLD_UNREVIEWED",
]
CHANNEL_COVERAGE_POLICIES: tuple[ChannelCoveragePolicy, ...] = (
    "IGNORE",
    "WITHHOLD_UNCATALOGUED",
    "WITHHOLD_UNREVIEWED",
)


class SupplyChannelError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SupplyChannelDeclaration:
    """An operator-reviewed statement of how one vehicle receives supply.

    A declaration records what an official article says, not what the model can
    measure. It exists so the difference between "this vehicle has no supply
    events" and "this vehicle's supply channel is not modelled" stops being
    invisible.
    """

    vehicle_name: str
    channels: tuple[SupplyChannel, ...]
    source_url: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        if not self.vehicle_name.strip():
            raise SupplyChannelError("vehicle_name must be non-empty.")
        if not self.channels:
            raise SupplyChannelError("a declaration must name at least one channel.")
        if any(channel not in ALL_CHANNELS for channel in self.channels):
            raise SupplyChannelError("unsupported supply channel.")
        if tuple(sorted(set(self.channels))) != tuple(sorted(self.channels)):
            raise SupplyChannelError("channels must be unique.")
        if not self.source_url.startswith("https://"):
            raise SupplyChannelError("source_url must be https.")
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise SupplyChannelError("reviewed_at must be timezone-aware.")
        object.__setattr__(self, "reviewed_at", self.reviewed_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class SupplyChannelCoverage:
    vehicle_name: str
    coverage: ChannelCoverage
    reviewed: bool
    catalogued_tradeable_event_count: int
    catalogued_account_only_event_count: int
    declared_channels: tuple[SupplyChannel, ...]
    modelled_channels: tuple[SupplyChannel, ...]
    unmodelled_channels: tuple[SupplyChannel, ...]
    source_url: str | None
    reviewed_at: datetime | None
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_CHANNELS_VERSION


@dataclass(frozen=True, slots=True)
class ChannelCoverageDecision:
    policy: ChannelCoveragePolicy
    coverage: ChannelCoverage | None
    withhold: bool
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_CHANNELS_VERSION


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


# Transcribed from official War Thunder articles. This list is deliberately
# short: an undeclared vehicle is reported as unreviewed rather than assumed
# clean, because assuming clean is what makes the current model confidently
# wrong about vehicles whose supply arrives outside the craft-event calendar.
DEFAULT_SUPPLY_CHANNEL_DECLARATIONS: tuple[SupplyChannelDeclaration, ...] = (
    # Battle Pass season 17 "Guided Fury" introduced a trophy containing past
    # Battle Pass and event vehicles that were not available on the marketplace.
    SupplyChannelDeclaration(
        vehicle_name="T55E1",
        channels=("BATTLE_TROPHY",),
        source_url="https://warthunder.com/en/news/9142-event-battlepass-meet-guided-fury-the-17th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2024, 10, 21),
    ),
    SupplyChannelDeclaration(
        vehicle_name="Toldi IIA",
        channels=("BATTLE_TROPHY", "CRAFT_EVENT"),
        source_url="https://warthunder.com/en/news/9142-event-battlepass-meet-guided-fury-the-17th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2024, 10, 21),
    ),
    SupplyChannelDeclaration(
        vehicle_name="Sd.Kfz.251/22",
        channels=("BATTLE_TROPHY", "CRAFT_EVENT"),
        source_url="https://warthunder.com/en/news/9142-event-battlepass-meet-guided-fury-the-17th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2024, 10, 21),
    ),
    SupplyChannelDeclaration(
        vehicle_name="Ka-Chi",
        channels=("BATTLE_TROPHY", "CRAFT_EVENT"),
        source_url="https://warthunder.com/en/news/9142-event-battlepass-meet-guided-fury-the-17th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2024, 10, 21),
    ),
    # Battle Pass season 18 "The Last Legionnaire" repeated the trophy.
    SupplyChannelDeclaration(
        vehicle_name="Type T-51a",
        channels=("BATTLE_TROPHY", "CRAFT_EVENT"),
        source_url="https://warthunder.com/en/news/9334-event-meet-the-last-legionnaire-the-18th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2025, 1, 20),
    ),
    SupplyChannelDeclaration(
        vehicle_name="Voroshilov",
        channels=("BATTLE_TROPHY", "CRAFT_EVENT"),
        source_url="https://warthunder.com/en/news/9334-event-meet-the-last-legionnaire-the-18th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2025, 1, 20),
    ),
    SupplyChannelDeclaration(
        vehicle_name="LVT-4/40",
        channels=("BATTLE_TROPHY",),
        source_url="https://warthunder.com/en/news/9334-event-meet-the-last-legionnaire-the-18th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2025, 1, 20),
    ),
    SupplyChannelDeclaration(
        vehicle_name="Type K-8 No.13i",
        channels=("BATTLE_TROPHY",),
        source_url="https://warthunder.com/en/news/9334-event-meet-the-last-legionnaire-the-18th-season-of-the-battle-pass-en",
        reviewed_at=_utc(2025, 1, 20),
    ),
    # Recurring seasonal crates award vehicles by random draw, repeatedly, on no
    # published calendar. These two are named in the Summertime Madness article.
    SupplyChannelDeclaration(
        vehicle_name="KV-IB (Germany)",
        channels=("CRAFT_EVENT", "SEASONAL_CRATE"),
        source_url="https://warthunder.com/en/news/3249/current/",
        reviewed_at=_utc(2015, 8, 1),
    ),
    SupplyChannelDeclaration(
        vehicle_name="P-63A-10 (USSR)",
        channels=("CRAFT_EVENT", "SEASONAL_CRATE"),
        source_url="https://warthunder.com/en/news/3249/current/",
        reviewed_at=_utc(2015, 8, 1),
    ),
)


def assess_supply_channel_coverage(
    *,
    vehicle_name: str,
    catalog: SupplyPoolCatalog | None = None,
    declarations: Iterable[SupplyChannelDeclaration] | None = None,
) -> SupplyChannelCoverage:
    """Report how much of a vehicle's known supply the model actually describes.

    The supply-pool catalog only describes craft-event boxes. A vehicle can also
    receive tradeable supply from a Battle Pass upgrade coupon, from a Battle
    Trophy that draws past vehicles at random, or from a recurring seasonal
    crate. None of those are modelled, so a vehicle with no catalogued event is
    not a vehicle with stable scarcity — it is a vehicle the model knows nothing
    about.

    Note also that a catalogued ``ACCOUNT_ONLY`` return means that *event*
    injected no tradeable supply. It is not a statement that the vehicle never
    receives tradeable supply, because another channel may.
    """

    if not vehicle_name.strip():
        raise SupplyChannelError("vehicle_name must be non-empty.")
    resolved_catalog = catalog or DEFAULT_SUPPLY_POOL_CATALOG
    resolved_declarations = (
        tuple(declarations)
        if declarations is not None
        else DEFAULT_SUPPLY_CHANNEL_DECLARATIONS
    )

    facts = resolved_catalog.bind_item(item_id=1, item_name=vehicle_name)
    tradeable = sum(
        member.coupon_mode == "DIRECT_OR_COUPON" for member in facts.memberships
    )
    account_only = sum(
        member.coupon_mode == "ACCOUNT_ONLY" for member in facts.memberships
    )

    declaration = next(
        (
            value
            for value in resolved_declarations
            if _key(value.vehicle_name) == _key(vehicle_name)
        ),
        None,
    )
    declared = tuple(sorted(declaration.channels)) if declaration is not None else ()
    modelled: tuple[SupplyChannel, ...] = ("CRAFT_EVENT",) if tradeable else ()
    unmodelled = tuple(
        channel for channel in declared if channel not in MODELLED_CHANNELS
    )

    codes: list[str] = []
    if declaration is None:
        codes.append("supply_channels_not_reviewed")
    if account_only and not tradeable:
        codes.append("catalogued_returns_are_account_only_not_a_tradeable_supply_claim")
    for channel in unmodelled:
        codes.append(f"unmodelled_supply_channel_{channel.lower()}")

    if not modelled:
        coverage: ChannelCoverage = "UNCATALOGUED"
        codes.append("no_modelled_tradeable_supply_channel")
    elif unmodelled or declaration is None:
        coverage = "PARTIALLY_CATALOGUED"
    else:
        coverage = "FULLY_CATALOGUED"

    return SupplyChannelCoverage(
        vehicle_name=vehicle_name,
        coverage=coverage,
        reviewed=declaration is not None,
        catalogued_tradeable_event_count=tradeable,
        catalogued_account_only_event_count=account_only,
        declared_channels=declared,
        modelled_channels=modelled,
        unmodelled_channels=unmodelled,
        source_url=declaration.source_url if declaration is not None else None,
        reviewed_at=declaration.reviewed_at if declaration is not None else None,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def resolve_channel_coverage_withholding(
    *,
    coverage: SupplyChannelCoverage | None = None,
    policy: ChannelCoveragePolicy = "IGNORE",
) -> ChannelCoverageDecision:
    """Decide whether unmodelled supply should keep a score unpublished.

    ``IGNORE`` is the default and reproduces current behaviour exactly.
    ``WITHHOLD_UNCATALOGUED`` acts only on the clear-cut case: a vehicle with no
    modelled tradeable supply channel at all, which today publishes as if its
    scarcity were stable. ``WITHHOLD_UNREVIEWED`` additionally withholds every
    vehicle whose channels nobody has checked, which is the honest position and
    a very large withholding set.
    """

    if policy not in CHANNEL_COVERAGE_POLICIES:
        raise SupplyChannelError("unsupported channel coverage policy.")
    codes: list[str] = [f"channel_coverage_policy_{policy.lower()}"]
    if coverage is None:
        if policy != "IGNORE":
            codes.append("channel_coverage_unavailable_not_withheld")
        return ChannelCoverageDecision(
            policy=policy,
            coverage=None,
            withhold=False,
            explanation_codes=tuple(codes),
        )
    codes.append(f"supply_channel_coverage_{coverage.coverage.lower()}")
    if policy == "IGNORE":
        withhold = False
    elif policy == "WITHHOLD_UNCATALOGUED":
        withhold = coverage.coverage == "UNCATALOGUED"
    else:
        withhold = coverage.coverage != "FULLY_CATALOGUED"
    if withhold:
        codes.append("withheld_for_unmodelled_supply_channel")
    return ChannelCoverageDecision(
        policy=policy,
        coverage=coverage.coverage,
        withhold=withhold,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def unbound_declarations(
    *,
    catalog: SupplyPoolCatalog | None = None,
    declarations: Iterable[SupplyChannelDeclaration] | None = None,
) -> tuple[str, ...]:
    """Declarations naming a vehicle the supply-pool catalog does not contain.

    These are not errors: a Battle Pass vehicle legitimately has no craft-event
    membership. Surfacing them keeps a spelling drift between an official
    article and the catalog from silently disabling a declaration.
    """

    resolved_catalog = catalog or DEFAULT_SUPPLY_POOL_CATALOG
    resolved_declarations = (
        tuple(declarations)
        if declarations is not None
        else DEFAULT_SUPPLY_CHANNEL_DECLARATIONS
    )
    return tuple(
        value.vehicle_name
        for value in resolved_declarations
        if resolved_catalog.bind_item(
            item_id=1, item_name=value.vehicle_name
        ).match_status
        != "MATCHED"
    )


def _key(value: str) -> str:
    return " ".join(value.split()).casefold()
