from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import unicodedata

from .reissue_exposure import ItemReissueExposureEvent
from .supply_pool_catalog import utc

SUPPLEMENTAL_REISSUE_CATALOG_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class SupplementalReissueCatalogEvent:
    event_key: str
    family: str
    known_at: datetime
    opens_at: datetime
    closes_at: datetime
    source_url: str
    member_names: tuple[str, ...]


# The 2022 tenth-anniversary event granted these vehicles directly to accounts.
# It is intentionally *not* treated as Marketplace supply.  For items such as
# M8 LAC it can still substitute demand because a player who earns the vehicle
# no longer needs to acquire the coupon from the market.
ANNIVERSARY_2022_RARE_VEHICLES = SupplementalReissueCatalogEvent(
    event_key="anniversary_2022_ten_rare_vehicles",
    family="UNKNOWN",
    known_at=utc(2022, 10, 31, 23, 59),
    opens_at=utc(2022, 10, 31, 11),
    closes_at=utc(2022, 11, 14, 11),
    source_url="https://warthunder.com/en/news/7945-special-happy-anniversary-war-thunder-en",
    member_names=(
        "TB-3M-17-32",
        "I-29",
        "Ya-5M",
        "A13 Mk II 1939",
        "D.371 H.S.9",
        "M8 LAC",
        "He 51 B-2/H",
        "LaGG-3-23",
        "DB-7 (Great Britain)",
        "XF5F",
    ),
)

SUPPLEMENTAL_REISSUE_EVENTS = (ANNIVERSARY_2022_RARE_VEHICLES,)


def bind_supplemental_reissue_events(
    *,
    item_id: int,
    item_name: str,
) -> tuple[ItemReissueExposureEvent, ...]:
    normalized = _normalize(item_name)
    values: list[ItemReissueExposureEvent] = []
    for event in SUPPLEMENTAL_REISSUE_EVENTS:
        if any(_normalize(name) == normalized for name in event.member_names):
            values.append(
                ItemReissueExposureEvent(
                    item_id=item_id,
                    event_key=event.event_key,
                    family=event.family,
                    known_at=event.known_at,
                    opens_at=event.opens_at,
                    closes_at=event.closes_at,
                    effect="ACCOUNT_ONLY_DEMAND_SUBSTITUTION",
                    source_url=event.source_url,
                )
            )
    return tuple(values)


def _normalize(value: str) -> str:
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
    aliases = {
        "db-7 (britain)": "db-7 (great britain)",
    }
    value = " ".join(normalized.split()).casefold()
    return aliases.get(value, value)
