from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from gaijin_market_analytics.supply_cycles import derive_supply_cycle_features
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


def _json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def snapshot(item_id: int, item_name: str, as_of: datetime) -> dict[str, object]:
    catalog = DEFAULT_SUPPLY_POOL_CATALOG
    facts = catalog.bind_item(item_id=item_id, item_name=item_name)
    cycle = derive_supply_cycle_features(
        item_id=item_id,
        as_of=as_of,
        events=facts.events,
        memberships=facts.memberships,
    )
    return {
        "item_name": item_name,
        "matched_catalog_name": facts.matched_catalog_name,
        "match_status": facts.match_status,
        "as_of": as_of,
        "state": cycle.state,
        "family": cycle.pool_family,
        "coupon_mode": cycle.coupon_mode,
        "confirmed_current_membership": cycle.confirmed_current_membership,
        "observed_return_count": cycle.observed_return_count,
        "observed_tradeable_return_count": cycle.observed_tradeable_return_count,
        "eligible_family_event_count": cycle.eligible_family_event_count,
        "recurrence_rate": cycle.recurrence_rate,
        "current_event_key": cycle.current_event_key,
        "explanation_codes": cycle.explanation_codes,
    }


def main() -> None:
    catalog = DEFAULT_SUPPLY_POOL_CATALOG
    family_counts: dict[str, dict[str, int]] = {}
    for event in catalog.catalog_events:
        data = family_counts.setdefault(event.family, {"events": 0, "memberships": 0})
        data["events"] += 1
        data["memberships"] += len(event.members)

    report = {
        "catalog_version": catalog.version,
        "event_count": catalog.event_count,
        "membership_count": catalog.membership_count,
        "family_counts": family_counts,
        "events": [
            {
                "event_key": event.event_key,
                "event_name": event.event_name,
                "family": event.family,
                "known_at": event.known_at,
                "opens_at": event.opens_at,
                "closes_at": event.closes_at,
                "known_at_basis": event.known_at_basis,
                "known_at_source_url": event.known_at_source_url,
                "declared_vehicle_count": event.declared_vehicle_count,
                "catalog_vehicle_count": len(event.members),
                "account_only_count": sum(
                    member.coupon_mode == "ACCOUNT_ONLY" for member in event.members
                ),
                "source_url": event.source_url,
            }
            for event in catalog.catalog_events
        ],
        "point_in_time_examples": [
            snapshot(1, "IS-7", datetime(2026, 8, 20, tzinfo=UTC)),
            snapshot(1, "IS-7", datetime(2026, 9, 21, tzinfo=UTC)),
            snapshot(2, "Ka-Chi", datetime(2026, 9, 21, tzinfo=UTC)),
            snapshot(3, "F-106A", datetime(2026, 8, 27, 11, 1, tzinfo=UTC)),
            snapshot(3, "F-106A", datetime(2026, 8, 27, 11, 2, tzinfo=UTC)),
            snapshot(4, "Whirlwind P.9", datetime(2026, 9, 21, tzinfo=UTC)),
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json))


if __name__ == "__main__":
    main()
