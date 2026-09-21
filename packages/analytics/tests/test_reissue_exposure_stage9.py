from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gaijin_market_analytics.reissue_exposure import (
    ItemReissueExposureEvent,
    ReissueExposureError,
    derive_reissue_exposure_features,
    merge_reissue_exposure_events,
    reissue_events_from_supply_pool,
)
from gaijin_market_analytics.reissue_exposure_catalog_v1 import (
    bind_supplemental_reissue_events,
)
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


def _events_for(item_id: int, item_name: str):
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=item_id, item_name=item_name)
    supply = reissue_events_from_supply_pool(
        item_id=item_id,
        events=facts.events,
        memberships=facts.memberships,
    )
    supplemental = bind_supplemental_reissue_events(
        item_id=item_id,
        item_name=item_name,
    )
    return merge_reissue_exposure_events(supply, supplemental)


def test_catalog_v12_extends_audit_back_to_2022():
    catalog = DEFAULT_SUPPLY_POOL_CATALOG
    assert catalog.version == "1.2.0"
    assert catalog.event_count == 17
    assert catalog.membership_count == 735


def test_m8_has_tradeable_and_account_only_reissue_axes():
    events = _events_for(2, "M8 LAC")
    features = derive_reissue_exposure_features(
        item_id=2,
        as_of=datetime(2026, 3, 17, 11, tzinfo=UTC),
        events=events,
    )
    assert features.known_return_count == 5
    assert features.completed_return_count == 5
    assert features.known_tradeable_supply_count == 4
    assert features.completed_tradeable_supply_count == 4
    assert features.known_account_only_return_count == 1
    assert features.completed_account_only_return_count == 1
    assert features.distinct_family_count == 2
    assert features.trailing_1095d_tradeable_supply_count == 3
    assert features.trailing_1095d_account_only_return_count == 0
    assert features.days_since_last_tradeable_supply_close == Decimal("1")
    assert "cross_family_reissue_history_present" in features.explanation_codes


def test_excelsior_has_no_2022_reissue_in_audited_catalog():
    events = _events_for(3, "Excelsior")
    features = derive_reissue_exposure_features(
        item_id=3,
        as_of=datetime(2026, 3, 17, 11, tzinfo=UTC),
        events=events,
    )
    assert features.completed_tradeable_supply_count == 3
    assert features.completed_account_only_return_count == 0
    assert features.distinct_family_count == 1


def test_future_or_not_yet_open_reissue_is_not_counted():
    events = _events_for(4, "M8 LAC")
    before_open = derive_reissue_exposure_features(
        item_id=4,
        as_of=datetime(2022, 8, 8, 12, tzinfo=UTC),
        events=events,
    )
    after_open = derive_reissue_exposure_features(
        item_id=4,
        as_of=datetime(2022, 8, 10, 12, tzinfo=UTC),
        events=events,
    )
    assert before_open.known_return_count == 0
    assert after_open.known_return_count == 1
    assert after_open.known_tradeable_supply_count == 1


def test_anniversary_direct_grant_is_demand_substitution_not_market_supply():
    events = _events_for(5, "M8 LAC")
    features = derive_reissue_exposure_features(
        item_id=5,
        as_of=datetime(2022, 11, 15, 11, tzinfo=UTC),
        events=events,
    )
    assert features.known_return_count == 2
    assert features.known_tradeable_supply_count == 1
    assert features.known_account_only_return_count == 1
    assert features.completed_tradeable_supply_count == 1
    assert features.completed_account_only_return_count == 1


def test_account_only_supply_pool_membership_does_not_become_tradeable_exposure():
    events = _events_for(6, "Ka-Chi")
    features = derive_reissue_exposure_features(
        item_id=6,
        as_of=datetime(2022, 8, 30, 12, tzinfo=UTC),
        events=events,
    )
    assert features.completed_tradeable_supply_count == 0
    assert features.completed_account_only_return_count == 1


def test_merge_rejects_double_counted_reissue_event_keys():
    event = ItemReissueExposureEvent(
        item_id=7,
        event_key="same",
        family="SUMMER",
        known_at=datetime(2024, 1, 1, tzinfo=UTC),
        opens_at=datetime(2024, 1, 2, tzinfo=UTC),
        closes_at=datetime(2024, 1, 3, tzinfo=UTC),
        effect="TRADEABLE_SUPPLY",
    )
    with pytest.raises(ReissueExposureError):
        merge_reissue_exposure_events((event,), (event,))


def test_exposure_layer_is_descriptive_and_has_no_price_multiplier():
    features = derive_reissue_exposure_features(
        item_id=8,
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        events=(),
    )
    assert features.known_return_count == 0
    assert features.days_since_last_tradeable_supply_close is None
    assert not hasattr(features, "penalty")
    assert not hasattr(features, "price_multiplier")


def test_opportunity_v23_exposes_reissue_diagnostics_without_changing_score():
    from datetime import timedelta
    from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
    from gaijin_market_analytics.opportunities_v2 import CurrentMarketEvidence, OpportunityScoreV2

    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = tuple(
        HistoricalTradeBucketEvidence(
            item_id=9,
            granularity="1d",
            bucket_start_utc=datetime(2026, 1, day, tzinfo=UTC),
            bucket_duration=timedelta(days=1),
            vwap_price=Decimal("20"),
            reported_trade_volume=1,
        )
        for day in range(1, 7)
    )
    market = CurrentMarketEvidence(
        item_id=9,
        observed_at=as_of,
        best_ask=Decimal("10"),
        best_bid=Decimal("9"),
        observed_ask_quantity=20,
        observed_bid_quantity=20,
    )
    neutral = OpportunityScoreV2().score(
        item_id=9,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=market,
        historical_buckets=history,
    )
    exposure = derive_reissue_exposure_features(
        item_id=9,
        as_of=as_of,
        events=(
            ItemReissueExposureEvent(
                item_id=9,
                event_key="older-return",
                family="SUMMER",
                known_at=datetime(2025, 1, 1, tzinfo=UTC),
                opens_at=datetime(2025, 1, 2, tzinfo=UTC),
                closes_at=datetime(2025, 1, 3, tzinfo=UTC),
                effect="TRADEABLE_SUPPLY",
            ),
        ),
    )
    enriched = OpportunityScoreV2().score(
        item_id=9,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=market,
        historical_buckets=history,
        reissue_exposure=exposure,
    )
    assert enriched.strategy_version == "2.3.0"
    assert enriched.feature_version == "opportunity_features_v2_3"
    assert enriched.score == neutral.score
    assert enriched.eligible == neutral.eligible
    assert enriched.reference_sell_price == neutral.reference_sell_price
    assert enriched.reissue_known_return_count == 1
    assert enriched.reissue_completed_tradeable_supply_count == 1
    assert "reissue_exposure_diagnostic_only_no_score_adjustment" in enriched.explanation_codes
