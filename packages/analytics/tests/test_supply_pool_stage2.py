from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.supply_cycle_impacts import (
    build_supply_cycle_impact_profile,
    measure_supply_event_impact,
)
from gaijin_market_analytics.supply_cycles import derive_supply_cycle_features
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


def daily_bucket(*, item_id: int, start: datetime, price: str, volume: int = 1):
    return HistoricalTradeBucketEvidence(
        item_id=item_id,
        granularity="1d",
        bucket_start_utc=start,
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def test_catalog_v1_has_audited_event_and_membership_counts():
    catalog = DEFAULT_SUPPLY_POOL_CATALOG
    assert catalog.version == "1.2.0"
    assert catalog.event_count == 17
    assert catalog.membership_count == 735
    assert all(
        event.completeness == "COMPLETE_VEHICLE_LIST"
        for event in catalog.catalog_events
    )
    assert all(
        event.declared_vehicle_count == len(event.members)
        for event in catalog.catalog_events
    )
    assert all(
        event.known_at_basis != "official_forum_timestamp" or event.known_at_source_url
        for event in catalog.catalog_events
    )


def test_catalog_resolution_is_exact_normalized_and_not_fuzzy():
    catalog = DEFAULT_SUPPLY_POOL_CATALOG
    assert catalog.resolve_vehicle_name("  IS-7  ") == ("IS-7", "MATCHED")
    assert catalog.resolve_vehicle_name("T-34E") == ("Т-34E", "MATCHED")
    assert catalog.resolve_vehicle_name("IS7") == (None, "UNMATCHED")


def test_is7_point_in_time_moves_from_seasonal_prior_to_confirmed_active():
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=17, item_name="IS-7")
    before_announcement = derive_supply_cycle_features(
        item_id=17,
        as_of=datetime(2026, 8, 20, tzinfo=UTC),
        events=facts.events,
        memberships=facts.memberships,
    )
    assert before_announcement.state == "SEASONAL_WINDOW_APPROACHING"
    assert before_announcement.confirmed_current_membership is False
    assert before_announcement.recurrence_rate == Decimal("1")

    active = derive_supply_cycle_features(
        item_id=17,
        as_of=datetime(2026, 9, 21, tzinfo=UTC),
        events=facts.events,
        memberships=facts.memberships,
    )
    assert active.state == "ACTIVE_SUPPLY_INJECTION"
    assert active.confirmed_current_membership is True
    assert active.current_event_key == "summer_2026_military_survival_kit"
    assert active.coupon_mode == "DIRECT_OR_COUPON"


def test_2026_summer_membership_is_not_visible_before_its_known_at_timestamp():
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=18, item_name="F-106A")
    before = derive_supply_cycle_features(
        item_id=18,
        as_of=datetime(2026, 8, 27, 11, 1, tzinfo=UTC),
        events=facts.events,
        memberships=facts.memberships,
    )
    after = derive_supply_cycle_features(
        item_id=18,
        as_of=datetime(2026, 8, 27, 11, 2, tzinfo=UTC),
        events=facts.events,
        memberships=facts.memberships,
    )
    assert before.state == "UNKNOWN"
    assert before.confirmed_current_membership is False
    assert after.state == "ACTIVE_SUPPLY_INJECTION"
    assert after.confirmed_current_membership is True


def test_account_only_return_is_confirmed_but_does_not_create_market_supply_regime():
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=19, item_name="Ka-Chi")
    cycle = derive_supply_cycle_features(
        item_id=19,
        as_of=datetime(2026, 9, 21, tzinfo=UTC),
        events=facts.events,
        memberships=facts.memberships,
    )
    assert cycle.confirmed_current_membership is True
    assert cycle.coupon_mode == "ACCOUNT_ONLY"
    assert cycle.observed_return_count == 5
    assert cycle.observed_tradeable_return_count == 0
    assert cycle.state == "NORMAL_DEPLETION"
    assert "confirmed_account_only_return_no_tradeable_supply" in cycle.explanation_codes


def test_catalog_membership_is_event_specific_not_fixed_family():
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=20, item_name="Whirlwind P.9")
    family_by_event = {event.event_key: event.family for event in facts.events}
    observed = {
        family_by_event[membership.event_key]
        for membership in facts.memberships
    }
    assert "SPRING_CRAFT" in observed
    assert "HISTORY_LUNAR" in observed


def test_supply_event_impact_measures_drawdown_activity_and_recovery():
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=21, item_name="IS-7")
    # Use a synthetic four-day event so expected values are transparent.
    from gaijin_market_analytics.supply_cycles import SupplyPoolEvent

    event = SupplyPoolEvent(
        event_key="synthetic-supply",
        family="SUMMER",
        known_at=datetime(2026, 1, 5, tzinfo=UTC),
        opens_at=datetime(2026, 1, 9, tzinfo=UTC),
        closes_at=datetime(2026, 1, 13, tzinfo=UTC),
    )
    del facts  # catalog binding is deliberately independent from impact math.
    start = datetime(2026, 1, 5, tzinfo=UTC)
    prices = (
        # PRE: median 100, activity 10
        ("100", 10), ("100", 10), ("100", 10), ("100", 10),
        # ACTIVE: trough 70 on day 2, median 82.5, activity 40
        ("90", 40), ("80", 40), ("70", 40), ("85", 40),
        # POST: median 87.5, activity 20
        ("80", 20), ("85", 20), ("90", 20), ("95", 20),
    )
    history = tuple(
        daily_bucket(
            item_id=21,
            start=start + timedelta(days=index),
            price=price,
            volume=volume,
        )
        for index, (price, volume) in enumerate(prices)
    )
    impact = measure_supply_event_impact(
        event=event,
        historical_buckets=history,
        as_of=datetime(2026, 1, 18, tzinfo=UTC),
    )
    assert impact.profile_eligible is True
    assert impact.pre_price_median == Decimal("100")
    assert impact.active_price_median == Decimal("82.5")
    assert impact.active_price_min == Decimal("70")
    assert impact.event_drawdown_ratio == Decimal("0.3")
    assert impact.activity_multiplier == Decimal("4")
    assert impact.post_price_ratio == Decimal("0.875")
    assert impact.days_to_trough == Decimal("2")
    assert impact.recovery_half_life_days == Decimal("1")

    profile = build_supply_cycle_impact_profile((impact,))
    assert profile.eligible_event_count == 1
    assert profile.median_event_drawdown_ratio == Decimal("0.3")
    assert profile.median_activity_multiplier == Decimal("4")


def test_supply_event_impact_is_point_in_time_safe_and_requires_completed_post_window():
    from gaijin_market_analytics.supply_cycles import SupplyPoolEvent

    event = SupplyPoolEvent(
        event_key="synthetic-supply",
        family="SUMMER",
        known_at=datetime(2026, 1, 5, tzinfo=UTC),
        opens_at=datetime(2026, 1, 9, tzinfo=UTC),
        closes_at=datetime(2026, 1, 13, tzinfo=UTC),
    )
    start = datetime(2026, 1, 5, tzinfo=UTC)
    base = tuple(
        daily_bucket(item_id=22, start=start + timedelta(days=i), price="100", volume=10)
        for i in range(10)
    )
    as_of = datetime(2026, 1, 15, tzinfo=UTC)
    baseline = measure_supply_event_impact(
        event=event,
        historical_buckets=base,
        as_of=as_of,
    )
    future = daily_bucket(
        item_id=22,
        start=datetime(2026, 1, 16, tzinfo=UTC),
        price="1",
        volume=999,
    )
    changed = measure_supply_event_impact(
        event=event,
        historical_buckets=(*base, future),
        as_of=as_of,
    )
    assert baseline == changed
    assert baseline.profile_eligible is False
    assert "supply_event_post_window_incomplete" in baseline.explanation_codes


def test_unknown_future_event_cannot_expose_impact_metrics():
    from gaijin_market_analytics.supply_cycles import SupplyPoolEvent

    event = SupplyPoolEvent(
        event_key="not-yet-announced",
        family="SUMMER",
        known_at=datetime(2026, 8, 27, 11, 2, tzinfo=UTC),
        opens_at=datetime(2026, 8, 27, 11, 0, tzinfo=UTC),
        closes_at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
    )
    impact = measure_supply_event_impact(
        event=event,
        historical_buckets=(),
        as_of=datetime(2026, 8, 27, 11, 1, tzinfo=UTC),
    )
    assert impact.event_visible is False
    assert impact.pre_price_median is None
    assert impact.profile_eligible is False
    assert impact.explanation_codes == ("supply_event_not_known_as_of",)


def test_item_level_impact_analysis_uses_only_known_tradeable_memberships():
    from gaijin_market_analytics.supply_cycle_impacts import analyze_item_supply_cycle_impacts
    from gaijin_market_analytics.supply_cycles import SupplyPoolEvent, SupplyPoolMembership

    tradeable = SupplyPoolEvent(
        event_key="tradeable",
        family="SUMMER",
        known_at=datetime(2025, 1, 1, tzinfo=UTC),
        opens_at=datetime(2025, 1, 5, tzinfo=UTC),
        closes_at=datetime(2025, 1, 9, tzinfo=UTC),
    )
    account_only = SupplyPoolEvent(
        event_key="account-only",
        family="SUMMER",
        known_at=datetime(2025, 2, 1, tzinfo=UTC),
        opens_at=datetime(2025, 2, 5, tzinfo=UTC),
        closes_at=datetime(2025, 2, 9, tzinfo=UTC),
    )
    future = SupplyPoolEvent(
        event_key="future",
        family="SUMMER",
        known_at=datetime(2027, 1, 1, tzinfo=UTC),
        opens_at=datetime(2027, 1, 5, tzinfo=UTC),
        closes_at=datetime(2027, 1, 9, tzinfo=UTC),
    )
    memberships = (
        SupplyPoolMembership(
            event_key="tradeable", item_id=23,
            known_at=tradeable.known_at, coupon_mode="DIRECT_OR_COUPON",
        ),
        SupplyPoolMembership(
            event_key="account-only", item_id=23,
            known_at=account_only.known_at, coupon_mode="ACCOUNT_ONLY",
        ),
        SupplyPoolMembership(
            event_key="future", item_id=23,
            known_at=future.known_at, coupon_mode="DIRECT_OR_COUPON",
        ),
    )
    start = datetime(2025, 1, 1, tzinfo=UTC)
    history = tuple(
        daily_bucket(item_id=23, start=start + timedelta(days=i), price="100", volume=10)
        for i in range(12)
    )
    analysis = analyze_item_supply_cycle_impacts(
        item_id=23,
        events=(tradeable, account_only, future),
        memberships=memberships,
        historical_buckets=history,
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert [impact.event_key for impact in analysis.impacts] == ["tradeable"]
    assert analysis.profile.eligible_event_count == 1


def test_complete_published_pool_absence_overrides_seasonal_prior():
    from gaijin_market_analytics.supply_cycles import SupplyPoolEvent, SupplyPoolMembership

    events = (
        SupplyPoolEvent(
            event_key="summer-2024", family="SUMMER",
            known_at=datetime(2024, 8, 1, tzinfo=UTC),
            opens_at=datetime(2024, 8, 8, tzinfo=UTC),
            closes_at=datetime(2024, 9, 4, tzinfo=UTC),
            membership_complete=True,
        ),
        SupplyPoolEvent(
            event_key="summer-2025", family="SUMMER",
            known_at=datetime(2025, 8, 1, tzinfo=UTC),
            opens_at=datetime(2025, 8, 7, tzinfo=UTC),
            closes_at=datetime(2025, 9, 3, tzinfo=UTC),
            membership_complete=True,
        ),
        SupplyPoolEvent(
            event_key="summer-2026", family="SUMMER",
            known_at=datetime(2026, 8, 10, tzinfo=UTC),
            opens_at=datetime(2026, 8, 20, tzinfo=UTC),
            closes_at=datetime(2026, 9, 15, tzinfo=UTC),
            membership_complete=True,
        ),
    )
    memberships = tuple(
        SupplyPoolMembership(
            event_key=event.event_key,
            item_id=24,
            known_at=event.known_at,
            coupon_mode="DIRECT_OR_COUPON",
        )
        for event in events[:2]
    )
    before = derive_supply_cycle_features(
        item_id=24,
        as_of=datetime(2026, 8, 9, tzinfo=UTC),
        events=events,
        memberships=memberships,
    )
    after = derive_supply_cycle_features(
        item_id=24,
        as_of=datetime(2026, 8, 20, tzinfo=UTC),
        events=events,
        memberships=memberships,
    )
    assert before.state == "SEASONAL_WINDOW_APPROACHING"
    assert after.state == "NORMAL_DEPLETION"
    assert after.current_event_key == "summer-2026"
    assert "confirmed_not_in_current_complete_pool" in after.explanation_codes
