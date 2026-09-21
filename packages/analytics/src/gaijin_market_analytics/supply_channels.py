from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Literal

from .supply_pool_catalog import SupplyPoolCatalog
from .supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


SUPPLY_CHANNELS_VERSION = "2.1.0"

# Where the vehicle came from. This says nothing about whether the Marketplace
# received tradeable supply; that is ``DeliveryMode``'s job. Keeping the two
# apart is the whole point: one family can deliver differently on different
# occasions, and the Warbond Shop historically has.
ChannelFamily = Literal[
    "LIMITED_EVENT",
    "CRAFTING_EVENT",
    "BATTLE_PASS",
    "BATTLE_TROPHY",
    "WARBOND_SHOP",
    "SEASONAL_CRATE",
    "SPECIAL_GRANT",
]
DeliveryMode = Literal[
    "ACCOUNT_GRANT",
    "RANDOM_ACCOUNT_GRANT",
    "NONTRADABLE_COUPON",
    "UPGRADEABLE_COUPON",
    "TRADEABLE_COUPON",
    "RANDOM_TRADEABLE_COUPON",
]
# Whether this vehicle's entry in the channel is the vehicle itself or only
# cosmetics for it. A camouflage coupon never becomes a tradeable vehicle, so a
# cosmetic entry is invisible to the vehicle supply model however much it shares
# the words "crate", "coupon" and "market".
AssetScope = Literal["VEHICLE", "COSMETIC", "MIXED"]
MarketEffect = Literal[
    "DIRECT_TRADEABLE_SUPPLY",
    "CONDITIONAL_TRADEABLE_SUPPLY",
    "DEMAND_SUBSTITUTION",
    "NO_VEHICLE_EFFECT",
]
Provenance = Literal["OFFICIAL_ARTICLE", "OPERATOR_REVIEW"]
AliasBasis = Literal["OFFICIAL_ARTICLE_TYPO"]

CHANNEL_FAMILIES: tuple[ChannelFamily, ...] = (
    "LIMITED_EVENT",
    "CRAFTING_EVENT",
    "BATTLE_PASS",
    "BATTLE_TROPHY",
    "WARBOND_SHOP",
    "SEASONAL_CRATE",
    "SPECIAL_GRANT",
)
DELIVERY_MODES: tuple[DeliveryMode, ...] = (
    "ACCOUNT_GRANT",
    "RANDOM_ACCOUNT_GRANT",
    "NONTRADABLE_COUPON",
    "UPGRADEABLE_COUPON",
    "TRADEABLE_COUPON",
    "RANDOM_TRADEABLE_COUPON",
)
ASSET_SCOPES: tuple[AssetScope, ...] = ("VEHICLE", "COSMETIC", "MIXED")
PROVENANCES: tuple[Provenance, ...] = ("OFFICIAL_ARTICLE", "OPERATOR_REVIEW")
ALIAS_BASES: tuple[AliasBasis, ...] = ("OFFICIAL_ARTICLE_TYPO",)
# The only family the supply-pool catalog and every supply-cycle model describe.
MODELLED_CHANNEL_FAMILIES: tuple[ChannelFamily, ...] = ("CRAFTING_EVENT",)
TRADEABLE_SUPPLY_EFFECTS: tuple[MarketEffect, ...] = (
    "DIRECT_TRADEABLE_SUPPLY",
    "CONDITIONAL_TRADEABLE_SUPPLY",
)

ChannelCoverage = Literal[
    "UNCATALOGUED",
    "PARTIALLY_CATALOGUED",
    "FULLY_CATALOGUED",
]
ChannelCoveragePolicy = Literal[
    "IGNORE",
    "WITHHOLD_UNCATALOGUED",
    "WITHHOLD_UNMODELLED_SUPPLY",
    "WITHHOLD_UNREVIEWED",
]
CHANNEL_COVERAGE_POLICIES: tuple[ChannelCoveragePolicy, ...] = (
    "IGNORE",
    "WITHHOLD_UNCATALOGUED",
    "WITHHOLD_UNMODELLED_SUPPLY",
    "WITHHOLD_UNREVIEWED",
)


class SupplyChannelError(ValueError):
    pass


def _families(
    values: Iterable[SupplyChannelObservation],
) -> tuple[ChannelFamily, ...]:
    seen: dict[ChannelFamily, None] = {}
    for value in values:
        seen.setdefault(value.channel_family, None)
    return tuple(sorted(seen))


def _key(value: str) -> str:
    return " ".join(value.split()).casefold()


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise SupplyChannelError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise SupplyChannelError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class VehicleNameAlias:
    """One hand-confirmed spelling that resolves to a canonical vehicle name.

    Curated one entry at a time and never fuzzy-matched: an automatic
    near-miss rule would eventually bind one vehicle's history to another
    vehicle's supply events, which nothing downstream would notice.
    """

    alias: str
    canonical_name: str
    basis: AliasBasis
    source_url: str
    source_label: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        if not self.alias.strip() or not self.canonical_name.strip():
            raise SupplyChannelError("alias and canonical_name must be non-empty.")
        if _key(self.alias) == _key(self.canonical_name):
            raise SupplyChannelError("an alias must differ from its canonical name.")
        if self.basis not in ALIAS_BASES:
            raise SupplyChannelError("unsupported alias basis.")
        if not self.source_url.startswith("https://"):
            raise SupplyChannelError("alias source_url must be https.")
        if not self.source_label.strip():
            raise SupplyChannelError("alias source_label must be non-empty.")
        object.__setattr__(self, "reviewed_at", _utc(self.reviewed_at, "reviewed_at"))


def canonical_vehicle_name(
    vehicle_name: str,
    *,
    aliases: Iterable[VehicleNameAlias] | None = None,
) -> str:
    """Resolve a curated alias to its canonical name, or return it unchanged."""

    resolved = (
        tuple(aliases) if aliases is not None else CURATED_VEHICLE_NAME_ALIASES
    )
    key = _key(vehicle_name)
    for value in resolved:
        if _key(value.alias) == key:
            return value.canonical_name
    return vehicle_name


def derive_market_effect(
    *,
    delivery_mode: DeliveryMode,
    asset_scope: AssetScope,
) -> MarketEffect:
    """Translate how a vehicle was delivered into what the Marketplace saw.

    A cosmetic-only entry has no vehicle effect at all. Otherwise a tradeable
    coupon is supply the moment it lands, an upgradeable coupon is supply only
    if players pay to convert it, and anything granted straight to an account
    removes a buyer without adding a seller.
    """

    if delivery_mode not in DELIVERY_MODES:
        raise SupplyChannelError("unsupported delivery mode.")
    if asset_scope not in ASSET_SCOPES:
        raise SupplyChannelError("unsupported asset scope.")
    if asset_scope == "COSMETIC":
        return "NO_VEHICLE_EFFECT"
    if delivery_mode in {"TRADEABLE_COUPON", "RANDOM_TRADEABLE_COUPON"}:
        return "DIRECT_TRADEABLE_SUPPLY"
    if delivery_mode == "UPGRADEABLE_COUPON":
        return "CONDITIONAL_TRADEABLE_SUPPLY"
    return "DEMAND_SUBSTITUTION"


@dataclass(frozen=True, slots=True)
class SupplyChannelObservation:
    """One reviewed occasion on which a vehicle was distributed.

    Recorded per occasion rather than per vehicle, because the same family can
    deliver differently at different times. ``occurred_at`` and
    ``earliest_tradeable_from`` are optional: the load-bearing claim is the
    family, delivery mode and asset scope, and a date nobody has checked should
    be absent rather than guessed.

    ``earliest_tradeable_from`` is a *lower bound* that the official article
    proves, not an observed listing date. When a player finishes the qualifying
    stage, and when they choose to convert the coupon, is player behaviour; the
    article only pins the earliest instant a coupon could legally be sold.
    """

    vehicle_name: str
    channel_family: ChannelFamily
    delivery_mode: DeliveryMode
    asset_scope: AssetScope
    provenance: Provenance
    reviewed_at: datetime
    source_url: str | None = None
    event_label: str | None = None
    occurred_at: datetime | None = None
    earliest_tradeable_from: datetime | None = None

    def __post_init__(self) -> None:
        if not self.vehicle_name.strip():
            raise SupplyChannelError("vehicle_name must be non-empty.")
        if self.channel_family not in CHANNEL_FAMILIES:
            raise SupplyChannelError("unsupported channel family.")
        if self.delivery_mode not in DELIVERY_MODES:
            raise SupplyChannelError("unsupported delivery mode.")
        if self.asset_scope not in ASSET_SCOPES:
            raise SupplyChannelError("unsupported asset scope.")
        if self.provenance not in PROVENANCES:
            raise SupplyChannelError("unsupported provenance.")
        if self.provenance == "OFFICIAL_ARTICLE":
            if self.source_url is None or not self.source_url.startswith("https://"):
                raise SupplyChannelError(
                    "an OFFICIAL_ARTICLE observation requires an https source_url."
                )
        if self.source_url is not None and not self.source_url.startswith("https://"):
            raise SupplyChannelError("source_url must be https when supplied.")
        object.__setattr__(
            self, "reviewed_at", _utc(self.reviewed_at, "reviewed_at")
        )
        if self.occurred_at is not None:
            object.__setattr__(
                self, "occurred_at", _utc(self.occurred_at, "occurred_at")
            )
        if self.earliest_tradeable_from is not None:
            object.__setattr__(
                self,
                "earliest_tradeable_from",
                _utc(self.earliest_tradeable_from, "earliest_tradeable_from"),
            )
            if (
                self.occurred_at is not None
                and self.earliest_tradeable_from < self.occurred_at
            ):
                raise SupplyChannelError(
                    "earliest_tradeable_from must not precede occurred_at."
                )
            if self.market_effect == "NO_VEHICLE_EFFECT":
                raise SupplyChannelError(
                    "a cosmetic entry cannot carry a vehicle coupon date."
                )

    @property
    def market_effect(self) -> MarketEffect:
        return derive_market_effect(
            delivery_mode=self.delivery_mode, asset_scope=self.asset_scope
        )

    @property
    def canonical_name(self) -> str:
        return canonical_vehicle_name(self.vehicle_name)

    @property
    def modelled(self) -> bool:
        return self.channel_family in MODELLED_CHANNEL_FAMILIES


@dataclass(frozen=True, slots=True)
class SupplyChannelCoverage:
    vehicle_name: str
    coverage: ChannelCoverage
    reviewed: bool
    modelled_tradeable_event_count: int
    catalogued_account_only_event_count: int
    observation_count: int
    observed_channel_families: tuple[ChannelFamily, ...]
    unmodelled_supply_channels: tuple[ChannelFamily, ...]
    unmodelled_demand_channels: tuple[ChannelFamily, ...]
    ignored_cosmetic_channels: tuple[ChannelFamily, ...]
    earliest_unmodelled_tradeable_from: datetime | None
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_CHANNELS_VERSION


@dataclass(frozen=True, slots=True)
class ChannelCoverageDecision:
    policy: ChannelCoveragePolicy
    coverage: ChannelCoverage | None
    withhold: bool
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_CHANNELS_VERSION


def _day(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _hour(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


WINTER_CONCERT_2025 = (
    "https://warthunder.com/en/news/"
    "9855-event-christmas-and-new-years-gifts-await-you-in-the-winter-concert-event-en"
)
BATTLE_PASS_17 = (
    "https://warthunder.com/en/news/"
    "9142-event-battlepass-meet-guided-fury-the-17th-season-of-the-battle-pass-en"
)
BATTLE_PASS_18 = (
    "https://warthunder.com/en/news/"
    "9334-event-meet-the-last-legionnaire-the-18th-season-of-the-battle-pass-en"
)
SUMMERTIME_MADNESS = "https://warthunder.com/en/news/3249/current/"
PAINTBALL_ARENA_2026 = "https://warthunder.com/en/news/9879-event-"
SCIMITAR_STRIKE_2026 = (
    "https://warthunder.com/en/news/"
    "10135-event-get-the-scimitar-mk2-in-the-scimitar-strike-event-en"
)
WARBOND_PREMIUM_VEHICLES = (
    "https://warthunder.com/en/news/5071-news-how-can-you-get-premium-vehicles-warbonds-en"
)


CURATED_VEHICLE_NAME_ALIASES: tuple[VehicleNameAlias, ...] = (
    # The Battle Pass season 18 article contradicts itself: the prose writes
    # "Type K-8 No.13i" while its own reward list and vehicle heading write
    # "Type K-8 No.13", which is the name the game and wiki use. There is no
    # "No.13i" variant; the other real variant is "Type K-8 No.13 late".
    VehicleNameAlias(
        alias="Type K-8 No.13i",
        canonical_name="Type K-8 No.13",
        basis="OFFICIAL_ARTICLE_TYPO",
        source_url=BATTLE_PASS_18,
        source_label="Battle Pass season 18 \"The Last Legionnaire\"",
        reviewed_at=_day(2026, 9, 21),
    ),
)


# Deliberately short. An undeclared vehicle is reported as unreviewed rather
# than assumed clean, because assuming clean is exactly what lets the model be
# confidently wrong about a vehicle supplied outside the craft-event calendar.
DEFAULT_SUPPLY_CHANNEL_OBSERVATIONS: tuple[SupplyChannelObservation, ...] = (
    # Winter Concert 2025. Rank V-VII event vehicles are issued as coupons, and
    # the article states tradable coupons only reach the Market on 12 January
    # 2026 — four days after the event itself closed.
    SupplyChannelObservation(
        vehicle_name="MiG-25PD",
        channel_family="LIMITED_EVENT",
        delivery_mode="UPGRADEABLE_COUPON",
        asset_scope="VEHICLE",
        provenance="OFFICIAL_ARTICLE",
        source_url=WINTER_CONCERT_2025,
        event_label="Winter Concert 2025",
        occurred_at=_day(2025, 12, 19),
        earliest_tradeable_from=_day(2026, 1, 12),
        reviewed_at=_day(2026, 9, 21),
    ),
    SupplyChannelObservation(
        vehicle_name="CRV Block 2",
        channel_family="LIMITED_EVENT",
        delivery_mode="UPGRADEABLE_COUPON",
        asset_scope="VEHICLE",
        provenance="OFFICIAL_ARTICLE",
        source_url=WINTER_CONCERT_2025,
        event_label="Winter Concert 2025",
        occurred_at=_day(2025, 12, 19),
        earliest_tradeable_from=_day(2026, 1, 12),
        reviewed_at=_day(2026, 9, 21),
    ),
    # Reviewed by the operator; the official article URLs are still outstanding,
    # so these carry no date they way the Winter Concert pair does.
    # Paintball Arena ran 22 January to 9 February; stage 8 is the one that
    # issues the coupon and opens no earlier than 5 February, and the article
    # states a tradable coupon "can be sold after 6 days from the moment it's
    # created".
    SupplyChannelObservation(
        vehicle_name="T-72B3 Arena",
        channel_family="LIMITED_EVENT",
        delivery_mode="UPGRADEABLE_COUPON",
        asset_scope="VEHICLE",
        provenance="OFFICIAL_ARTICLE",
        source_url=PAINTBALL_ARENA_2026,
        event_label="Paintball Arena 2026",
        occurred_at=_hour(2026, 1, 22, 11),
        earliest_tradeable_from=_hour(2026, 2, 11, 11),
        reviewed_at=_day(2026, 9, 21),
    ),
    # Scimitar Strike ran 22 July to 5 August; stage 7 issues the coupon and
    # opens no earlier than 3 August, with the same six-day sale cooldown.
    SupplyChannelObservation(
        vehicle_name="Scimitar Mk.2",
        channel_family="LIMITED_EVENT",
        delivery_mode="UPGRADEABLE_COUPON",
        asset_scope="VEHICLE",
        provenance="OFFICIAL_ARTICLE",
        source_url=SCIMITAR_STRIKE_2026,
        event_label="Scimitar Strike 2026",
        occurred_at=_hour(2026, 7, 22, 11),
        earliest_tradeable_from=_hour(2026, 8, 9, 11),
        reviewed_at=_day(2026, 9, 21),
    ),
    # Battle Pass trophies hand the vehicle straight to an account at random, so
    # they remove buyers without adding sellers.
    *(
        SupplyChannelObservation(
            vehicle_name=name,
            channel_family="BATTLE_TROPHY",
            delivery_mode="RANDOM_ACCOUNT_GRANT",
            asset_scope="VEHICLE",
            provenance="OFFICIAL_ARTICLE",
            source_url=BATTLE_PASS_17,
            event_label="Battle Pass season 17 Battle Trophy",
            occurred_at=_day(2024, 10, 21),
            reviewed_at=_day(2026, 9, 21),
        )
        for name in ("T55E1", "Toldi IIA", "Sd.Kfz.251/22", "Ka-Chi")
    ),
    *(
        SupplyChannelObservation(
            vehicle_name=name,
            channel_family="BATTLE_TROPHY",
            delivery_mode="RANDOM_ACCOUNT_GRANT",
            asset_scope="VEHICLE",
            provenance="OFFICIAL_ARTICLE",
            source_url=BATTLE_PASS_18,
            event_label="Battle Pass season 18 Battle Trophy",
            occurred_at=_day(2025, 1, 20),
            reviewed_at=_day(2026, 9, 21),
        )
        for name in ("Type T-51a", "Voroshilov", "LVT-4/40", "Type K-8 No.13i")
    ),
    # Seasonal crates awarding the vehicle itself, at random, on no published
    # calendar.
    *(
        SupplyChannelObservation(
            vehicle_name=name,
            channel_family="SEASONAL_CRATE",
            delivery_mode="RANDOM_ACCOUNT_GRANT",
            asset_scope="VEHICLE",
            provenance="OFFICIAL_ARTICLE",
            source_url=SUMMERTIME_MADNESS,
            event_label="Summertime Madness trophies",
            reviewed_at=_day(2026, 9, 21),
        )
        for name in ("KV-IB (Germany)", "P-63A-10 (USSR)")
    ),
    # The Warbond Shop has offered the same vehicle as a plain account grant.
    # The family is not pinned to one delivery mode precisely because it has
    # also handed out tradeable vehicle coupons in other years.
    SupplyChannelObservation(
        vehicle_name="KV-IB (Germany)",
        channel_family="WARBOND_SHOP",
        delivery_mode="ACCOUNT_GRANT",
        asset_scope="VEHICLE",
        provenance="OFFICIAL_ARTICLE",
        source_url=WARBOND_PREMIUM_VEHICLES,
        event_label="Warbond Shop premium vehicles",
        reviewed_at=_day(2026, 9, 21),
    ),
)


def assess_supply_channel_coverage(
    *,
    vehicle_name: str,
    catalog: SupplyPoolCatalog | None = None,
    observations: Iterable[SupplyChannelObservation] | None = None,
) -> SupplyChannelCoverage:
    """Report how much of a vehicle's known supply the model actually describes.

    The supply-pool catalog describes crafting-event boxes and nothing else. A
    vehicle can also receive tradeable supply from a limited event's upgradeable
    coupon, and can lose demand to a Battle Trophy or Warbond grant that never
    touches the Marketplace. A vehicle with no catalogued event is therefore not
    a vehicle with stable scarcity; it is a vehicle the model knows nothing
    about.

    A catalogued ``ACCOUNT_ONLY`` return means only that *that event* injected
    no tradeable supply. It is never a claim that the vehicle receives none.
    """

    if not vehicle_name.strip():
        raise SupplyChannelError("vehicle_name must be non-empty.")
    resolved_catalog = catalog or DEFAULT_SUPPLY_POOL_CATALOG
    resolved = (
        tuple(observations)
        if observations is not None
        else DEFAULT_SUPPLY_CHANNEL_OBSERVATIONS
    )

    canonical = canonical_vehicle_name(vehicle_name)
    facts = resolved_catalog.bind_item(item_id=1, item_name=canonical)
    modelled_tradeable = sum(
        member.coupon_mode == "DIRECT_OR_COUPON" for member in facts.memberships
    )
    account_only = sum(
        member.coupon_mode == "ACCOUNT_ONLY" for member in facts.memberships
    )

    mine = tuple(
        value for value in resolved if _key(value.canonical_name) == _key(canonical)
    )
    unmodelled = tuple(value for value in mine if not value.modelled)
    supply_channels = _families(
        value for value in unmodelled if value.market_effect in TRADEABLE_SUPPLY_EFFECTS
    )
    demand_channels = _families(
        value for value in unmodelled if value.market_effect == "DEMAND_SUBSTITUTION"
    )
    cosmetic_channels = _families(
        value for value in unmodelled if value.market_effect == "NO_VEHICLE_EFFECT"
    )
    tradeable_dates = tuple(
        value.earliest_tradeable_from
        for value in unmodelled
        if value.earliest_tradeable_from is not None
        and value.market_effect in TRADEABLE_SUPPLY_EFFECTS
    )

    codes: list[str] = []
    if not mine:
        codes.append("supply_channels_not_reviewed")
    if account_only and not modelled_tradeable:
        codes.append("catalogued_returns_are_account_only_not_a_tradeable_supply_claim")
    for family in supply_channels:
        codes.append(f"unmodelled_tradeable_supply_channel_{family.lower()}")
    for family in demand_channels:
        codes.append(f"unmodelled_demand_substitution_channel_{family.lower()}")
    for family in cosmetic_channels:
        codes.append(f"cosmetic_channel_ignored_{family.lower()}")
    if any(value.provenance == "OPERATOR_REVIEW" for value in mine):
        codes.append("supply_channel_observation_not_independently_sourced")

    if not modelled_tradeable:
        coverage: ChannelCoverage = "UNCATALOGUED"
        codes.append("no_modelled_tradeable_supply_channel")
    elif not mine or supply_channels or demand_channels:
        coverage = "PARTIALLY_CATALOGUED"
    else:
        coverage = "FULLY_CATALOGUED"

    return SupplyChannelCoverage(
        vehicle_name=vehicle_name,
        coverage=coverage,
        reviewed=bool(mine),
        modelled_tradeable_event_count=modelled_tradeable,
        catalogued_account_only_event_count=account_only,
        observation_count=len(mine),
        observed_channel_families=_families(mine),
        unmodelled_supply_channels=supply_channels,
        unmodelled_demand_channels=demand_channels,
        ignored_cosmetic_channels=cosmetic_channels,
        earliest_unmodelled_tradeable_from=min(tradeable_dates, default=None),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def resolve_channel_coverage_withholding(
    *,
    coverage: SupplyChannelCoverage | None = None,
    policy: ChannelCoveragePolicy = "IGNORE",
) -> ChannelCoverageDecision:
    """Decide whether unmodelled supply should keep a score unpublished.

    ``IGNORE`` is the default and reproduces current behaviour exactly.
    ``WITHHOLD_UNCATALOGUED`` acts only on a vehicle with no modelled tradeable
    supply at all, which today publishes as if its scarcity were stable.
    ``WITHHOLD_UNMODELLED_SUPPLY`` adds the vehicles known to receive tradeable
    supply through a channel the model does not describe.
    ``WITHHOLD_UNREVIEWED`` additionally withholds everything nobody has
    checked, which is the honest position and a very large withholding set.
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
    elif policy == "WITHHOLD_UNMODELLED_SUPPLY":
        withhold = (
            coverage.coverage == "UNCATALOGUED"
            or bool(coverage.unmodelled_supply_channels)
        )
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


def observations_without_official_source(
    *,
    observations: Iterable[SupplyChannelObservation] | None = None,
) -> tuple[tuple[str, str | None], ...]:
    """Reviewed observations still waiting for an auditable article URL."""

    resolved = (
        tuple(observations)
        if observations is not None
        else DEFAULT_SUPPLY_CHANNEL_OBSERVATIONS
    )
    return tuple(
        (value.vehicle_name, value.event_label)
        for value in resolved
        if value.provenance != "OFFICIAL_ARTICLE"
    )


def unbound_observations(
    *,
    catalog: SupplyPoolCatalog | None = None,
    observations: Iterable[SupplyChannelObservation] | None = None,
) -> tuple[str, ...]:
    """Observations naming a vehicle the supply-pool catalog does not contain.

    Not an error: a limited-event vehicle legitimately has no crafting-event
    membership. Surfacing them keeps a spelling drift between an official
    article and the catalog from silently disabling an observation.
    """

    resolved_catalog = catalog or DEFAULT_SUPPLY_POOL_CATALOG
    resolved = (
        tuple(observations)
        if observations is not None
        else DEFAULT_SUPPLY_CHANNEL_OBSERVATIONS
    )
    names: dict[str, None] = {}
    for value in resolved:
        if (
            resolved_catalog.bind_item(
                item_id=1, item_name=value.canonical_name
            ).match_status
            != "MATCHED"
        ):
            names.setdefault(value.canonical_name, None)
    return tuple(names)
