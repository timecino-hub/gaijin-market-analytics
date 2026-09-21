from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import (
    HistoricalTradeBucketEvidence,
    select_historical_trade_evidence,
)
from gaijin_market_analytics.opportunities_v2 import (
    CurrentMarketEvidence,
    OpportunityScoreV2,
    OpportunityV2Config,
)
from gaijin_market_analytics.supply_cycles import (
    SupplyPoolEvent,
    SupplyPoolMembership,
    derive_supply_cycle_features,
)

AS_OF = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)


def bucket(*, start: datetime, price: str, granularity: str = "1d", item_id: int = 1):
    duration = timedelta(hours=1) if granularity == "1h" else timedelta(days=1)
    return HistoricalTradeBucketEvidence(
        item_id=item_id,
        granularity=granularity,  # type: ignore[arg-type]
        bucket_start_utc=start,
        bucket_duration=duration,
        vwap_price=Decimal(price),
        reported_trade_volume=1,
    )


def current(*, ask="10", bid="9", at=AS_OF):
    return CurrentMarketEvidence(
        item_id=1,
        observed_at=at,
        best_ask=Decimal(ask),
        best_bid=Decimal(bid),
        observed_ask_quantity=20,
        observed_bid_quantity=20,
    )


def test_future_and_incomplete_buckets_do_not_affect_point_in_time_result():
    window = AS_OF - timedelta(days=7)
    past = tuple(
        bucket(start=window + timedelta(days=i), price=str(20 + i))
        for i in range(6)
    )
    future = bucket(start=AS_OF, price="999")
    score = OpportunityScoreV2()
    baseline = score.score(
        item_id=1,
        as_of=AS_OF,
        window_start=window,
        current_market=current(),
        historical_buckets=past,
    )
    changed = score.score(
        item_id=1,
        as_of=AS_OF,
        window_start=window,
        current_market=current(),
        historical_buckets=(*past, future),
    )
    assert baseline.score == changed.score
    assert baseline.reference_sell_price == changed.reference_sell_price
    assert baseline.net_profit == changed.net_profit


def test_hourly_bucket_replaces_overlapping_daily_bucket_without_double_weighting():
    day = datetime(2026, 9, 20, tzinfo=UTC)
    evidence = select_historical_trade_evidence(
        (
            bucket(start=day, price="100", granularity="1d"),
            bucket(start=day + timedelta(hours=12), price="50", granularity="1h"),
        ),
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=7),
    )
    assert evidence.hourly_bucket_count == 1
    assert evidence.daily_bucket_count == 0
    assert evidence.overlapping_daily_buckets_dropped == 1
    assert [point.vwap_price for point in evidence.buckets] == [Decimal("50")]


def test_insufficient_history_does_not_publish_zero_score():
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=7),
        current_market=current(),
        historical_buckets=(bucket(start=AS_OF - timedelta(days=2), price="20"),),
    )
    assert result.score_published is False
    assert result.score is None
    assert result.reference_sell_price is None
    assert "score_not_published" in result.explanation_codes


def test_reference_sell_price_comes_only_from_historical_trades():
    points = tuple(
        bucket(start=AS_OF - timedelta(days=7 - i), price=price)
        for i, price in enumerate(("20", "20", "20", "22", "24", "24"))
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=8),
        current_market=current(ask="10", bid="1"),
        historical_buckets=points,
    )
    assert result.score_published is True
    assert result.reference_sell_price == Decimal("21")
    assert result.reference_sell_price != Decimal("1")
    assert result.sale_proceeds == Decimal("17.85")
    assert result.net_profit == Decimal("7.85")


def test_active_supply_injection_withholds_normal_opportunity_strategy():
    event = SupplyPoolEvent(
        event_key="summer-2026",
        family="SUMMER",
        known_at=datetime(2026, 8, 27, tzinfo=UTC),
        opens_at=datetime(2026, 8, 27, tzinfo=UTC),
        closes_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    membership = SupplyPoolMembership(
        event_key=event.event_key,
        item_id=1,
        known_at=event.known_at,
        coupon_mode="DIRECT_OR_COUPON",
    )
    cycle = derive_supply_cycle_features(
        item_id=1,
        as_of=AS_OF,
        events=(event,),
        memberships=(membership,),
    )
    points = tuple(
        bucket(start=AS_OF - timedelta(days=7 - i), price="20")
        for i in range(6)
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=8),
        current_market=current(),
        historical_buckets=points,
        supply_cycle=cycle,
    )
    assert cycle.state == "ACTIVE_SUPPLY_INJECTION"
    assert result.score_published is False
    assert result.score is None
    assert "strategy_withheld_for_supply_cycle_regime" in result.explanation_codes


def test_future_announcement_does_not_leak_into_past_cycle_state():
    event = SupplyPoolEvent(
        event_key="summer-2026",
        family="SUMMER",
        known_at=datetime(2026, 8, 27, tzinfo=UTC),
        opens_at=datetime(2026, 8, 27, tzinfo=UTC),
        closes_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    membership = SupplyPoolMembership(
        event_key=event.event_key,
        item_id=1,
        known_at=event.known_at,
        coupon_mode="DIRECT_OR_COUPON",
    )
    before = derive_supply_cycle_features(
        item_id=1,
        as_of=datetime(2026, 8, 1, tzinfo=UTC),
        events=(event,),
        memberships=(membership,),
    )
    after = derive_supply_cycle_features(
        item_id=1,
        as_of=datetime(2026, 9, 1, tzinfo=UTC),
        events=(event,),
        memberships=(membership,),
    )
    assert before.state == "UNKNOWN"
    assert after.state == "ACTIVE_SUPPLY_INJECTION"


def test_seasonal_prior_requires_observed_perfect_recurrence_and_known_history():
    events = (
        SupplyPoolEvent(
            event_key="summer-2024",
            family="SUMMER",
            known_at=datetime(2024, 8, 8, tzinfo=UTC),
            opens_at=datetime(2024, 8, 8, tzinfo=UTC),
            closes_at=datetime(2024, 9, 4, tzinfo=UTC),
        ),
        SupplyPoolEvent(
            event_key="summer-2025",
            family="SUMMER",
            known_at=datetime(2025, 8, 7, tzinfo=UTC),
            opens_at=datetime(2025, 8, 7, tzinfo=UTC),
            closes_at=datetime(2025, 9, 3, tzinfo=UTC),
        ),
    )
    memberships = tuple(
        SupplyPoolMembership(
            event_key=event.event_key,
            item_id=1,
            known_at=event.known_at,
            coupon_mode="DIRECT_OR_COUPON",
        )
        for event in events
    )
    cycle = derive_supply_cycle_features(
        item_id=1,
        as_of=datetime(2026, 8, 20, tzinfo=UTC),
        events=events,
        memberships=memberships,
    )
    assert cycle.recurrence_rate == Decimal("1")
    assert cycle.state == "SEASONAL_WINDOW_APPROACHING"


def test_stale_history_is_unpublished_even_with_many_buckets():
    config = OpportunityV2Config(maximum_historical_trade_age=timedelta(days=7))
    points = tuple(
        bucket(start=AS_OF - timedelta(days=20 - i), price="20")
        for i in range(6)
    )
    result = OpportunityScoreV2(config).score(
        item_id=1,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=30),
        current_market=current(),
        historical_buckets=points,
    )
    assert result.score is None
    assert "stale_historical_trade_evidence" in result.explanation_codes


def test_unpublished_diagnostics_only_survive_zero_minimum_score():
    from gaijin_market_analytics.opportunities_v2 import rank_opportunity_scores_v2

    unpublished = OpportunityScoreV2().score(
        item_id=1,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=7),
        current_market=current(),
        historical_buckets=(),
    )
    assert rank_opportunity_scores_v2(
        (unpublished,), eligible_only=False, minimum_score=Decimal("0")
    ) == (unpublished,)
    assert rank_opportunity_scores_v2(
        (unpublished,), eligible_only=False, minimum_score=Decimal("1")
    ) == ()


def test_hourly_resolution_does_not_give_one_day_multiple_price_votes():
    start = AS_OF - timedelta(days=5)
    points = (
        bucket(start=start, price="10", granularity="1d"),
        bucket(start=start + timedelta(days=1), price="20", granularity="1d"),
        bucket(start=start + timedelta(days=2), price="30", granularity="1d"),
        bucket(start=start + timedelta(days=3), price="40", granularity="1d"),
        bucket(start=start + timedelta(days=4), price="100", granularity="1h"),
        bucket(start=start + timedelta(days=4, hours=1), price="100", granularity="1h"),
        bucket(start=start + timedelta(days=4, hours=2), price="100", granularity="1h"),
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=6),
        current_market=current(ask="10", bid="9"),
        historical_buckets=points,
    )
    assert result.features.historical_bucket_count == 7
    assert result.features.historical_effective_day_count == 5
    assert result.reference_sell_price == Decimal("30")


# --- the post-event entry rule ships inert ----------------------------------


def _postevent_signal(*, shallow: bool):
    from gaijin_market_analytics.postevent_entry_rule import evaluate_postevent_entry

    opens = AS_OF - timedelta(days=20)
    closes = AS_OF - timedelta(days=6)
    pre = tuple(
        bucket(start=opens - timedelta(days=14 - i), price="100") for i in range(14)
    )
    post = tuple(
        bucket(start=closes + timedelta(days=i), price="90" if shallow else "60")
        for i in range(3)
    )
    return evaluate_postevent_entry(
        item_id=1,
        as_of=AS_OF,
        events=(
            SupplyPoolEvent(
                event_key="spring-2026",
                family="spring",
                known_at=opens - timedelta(days=7),
                opens_at=opens,
                closes_at=closes,
            ),
        ),
        historical_buckets=(*pre, *post),
    )


def _scored(policy, signal):
    window = AS_OF - timedelta(days=7)
    past = tuple(
        bucket(start=window + timedelta(days=i), price=str(20 + i)) for i in range(6)
    )
    return OpportunityScoreV2(OpportunityV2Config(postevent_entry_policy=policy)).score(
        item_id=1,
        as_of=AS_OF,
        window_start=window,
        current_market=current(),
        historical_buckets=past,
        postevent_entry=signal,
    )


def test_the_default_policy_is_unchanged_by_a_post_event_signal():
    """The shipped default must produce what it produced before the rule existed."""

    shallow = _postevent_signal(shallow=True)
    assert shallow.state == "SHALLOW_DRAWDOWN"

    without = _scored("IGNORE", None)
    with_signal = _scored("IGNORE", shallow)

    assert with_signal == without


def test_annotate_adds_reasoning_without_changing_publication():
    shallow = _postevent_signal(shallow=True)
    ignored = _scored("IGNORE", shallow)
    annotated = _scored("ANNOTATE", shallow)

    assert annotated.score_published == ignored.publishable
    assert annotated.score == ignored.score
    assert "postevent_entry_drawdown_gate_failed" in annotated.explanation_codes
    assert "postevent_entry_annotated_only" in annotated.explanation_codes


def test_only_the_withholding_policy_unpublishes_a_shallow_drawdown():
    shallow = _postevent_signal(shallow=True)
    deep = _postevent_signal(shallow=False)
    assert deep.state == "ENTRY_CANDIDATE"

    withheld = _scored("WITHHOLD_SHALLOW_DRAWDOWN", shallow)
    kept = _scored("WITHHOLD_SHALLOW_DRAWDOWN", deep)

    assert withheld.score_published is False
    assert (
        "strategy_withheld_for_shallow_post_event_drawdown"
        in withheld.explanation_codes
    )
    assert "strategy_withheld_for_shallow_post_event_drawdown" not in kept.explanation_codes


def test_an_unsupported_post_event_policy_is_rejected():
    import pytest

    from gaijin_market_analytics.opportunities_v2 import OpportunityV2ContractError

    with pytest.raises(OpportunityV2ContractError, match="postevent_entry_policy"):
        OpportunityV2Config(postevent_entry_policy="ALWAYS_BUY")
