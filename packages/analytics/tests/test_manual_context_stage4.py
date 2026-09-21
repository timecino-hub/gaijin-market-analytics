from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.manual_context import (
    ManualMarketContextObservation,
    context_comparability,
    resolve_manual_market_context,
    weighted_decimal_median,
)


def obs(
    *,
    effective_at: datetime,
    known_at: datetime,
    strength: str = "1",
    attention: str = "1",
    item_id: int = 1,
):
    return ManualMarketContextObservation(
        item_id=item_id,
        effective_at=effective_at,
        known_at=known_at,
        strength_index=Decimal(strength),
        attention_index=Decimal(attention),
    )


def test_future_known_annotation_cannot_leak_into_past_replay():
    effective = datetime(2026, 3, 1, tzinfo=UTC)
    annotation = obs(
        effective_at=effective,
        known_at=datetime(2026, 4, 1, tzinfo=UTC),
        strength="1.20",
    )
    before = resolve_manual_market_context(
        item_id=1,
        effective_at=datetime(2026, 3, 15, tzinfo=UTC),
        knowledge_as_of=datetime(2026, 3, 15, tzinfo=UTC),
        observations=(annotation,),
    )
    after = resolve_manual_market_context(
        item_id=1,
        effective_at=datetime(2026, 3, 15, tzinfo=UTC),
        knowledge_as_of=datetime(2026, 4, 2, tzinfo=UTC),
        observations=(annotation,),
    )
    assert before.defaulted is True
    assert before.strength_index == Decimal("1")
    assert after.defaulted is False
    assert after.strength_index == Decimal("1.20")


def test_future_effective_context_does_not_apply_early_even_if_known():
    annotation = obs(
        effective_at=datetime(2026, 5, 1, tzinfo=UTC),
        known_at=datetime(2026, 4, 1, tzinfo=UTC),
        attention="1.30",
    )
    current = resolve_manual_market_context(
        item_id=1,
        effective_at=datetime(2026, 4, 20, tzinfo=UTC),
        knowledge_as_of=datetime(2026, 4, 20, tzinfo=UTC),
        observations=(annotation,),
    )
    assert current.defaulted is True
    assert current.attention_index == Decimal("1")


def test_latest_visible_revision_for_same_effective_state_wins():
    effective = datetime(2026, 3, 1, tzinfo=UTC)
    initial = obs(
        effective_at=effective,
        known_at=datetime(2026, 3, 1, tzinfo=UTC),
        strength="1.10",
    )
    revised = obs(
        effective_at=effective,
        known_at=datetime(2026, 3, 5, tzinfo=UTC),
        strength="1.25",
    )
    current = resolve_manual_market_context(
        item_id=1,
        effective_at=datetime(2026, 3, 10, tzinfo=UTC),
        knowledge_as_of=datetime(2026, 3, 10, tzinfo=UTC),
        observations=(initial, revised),
    )
    assert current.strength_index == Decimal("1.25")
    assert current.source_known_at == revised.known_at


def test_context_comparability_is_symmetric_and_neutral_is_one():
    at = datetime(2026, 1, 1, tzinfo=UTC)
    neutral = resolve_manual_market_context(
        item_id=1, effective_at=at, knowledge_as_of=at, observations=()
    )
    shifted = resolve_manual_market_context(
        item_id=1,
        effective_at=at,
        knowledge_as_of=at,
        observations=(obs(effective_at=at, known_at=at, strength="1.20", attention="1.50"),),
    )
    nn = context_comparability(neutral, neutral)
    forward = context_comparability(neutral, shifted)
    reverse = context_comparability(shifted, neutral)
    assert nn.combined_similarity == Decimal("1")
    assert forward == reverse
    assert forward.strength_similarity == Decimal("1") / Decimal("1.20")
    assert forward.attention_similarity == Decimal("1") / Decimal("1.50")


def test_weighted_median_preserves_even_equal_weight_median():
    assert weighted_decimal_median((
        (Decimal("10"), Decimal("1")),
        (Decimal("20"), Decimal("1")),
        (Decimal("30"), Decimal("1")),
        (Decimal("40"), Decimal("1")),
    )) == Decimal("25")


def test_weighted_median_moves_toward_more_comparable_evidence():
    assert weighted_decimal_median((
        (Decimal("10"), Decimal("0.1")),
        (Decimal("20"), Decimal("0.1")),
        (Decimal("100"), Decimal("1")),
    )) == Decimal("100")


def _bucket(day: int, price: str):
    from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence

    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=datetime(2026, 1, day, tzinfo=UTC),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=1,
    )


def _market(as_of: datetime):
    from gaijin_market_analytics.opportunities_v2 import CurrentMarketEvidence

    return CurrentMarketEvidence(
        item_id=1,
        observed_at=as_of,
        best_ask=Decimal("10"),
        best_bid=Decimal("9"),
        observed_ask_quantity=20,
        observed_bid_quantity=20,
    )


def test_neutral_manual_context_preserves_v2_reference_and_score():
    from gaijin_market_analytics.opportunities_v2 import OpportunityScoreV2

    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = tuple(_bucket(day, price) for day, price in enumerate(
        ("20", "20", "20", "22", "24", "24"), start=1
    ))
    scorer = OpportunityScoreV2()
    baseline = scorer.score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
    )
    explicit_neutral = scorer.score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
        manual_context_observations=(
            obs(
                effective_at=datetime(2025, 1, 1, tzinfo=UTC),
                known_at=datetime(2025, 1, 1, tzinfo=UTC),
            ),
        ),
    )
    assert baseline.reference_sell_price == Decimal("21")
    assert baseline.reference_sell_price == explicit_neutral.reference_sell_price
    assert baseline.score == explicit_neutral.score
    assert baseline.data_confidence_score == explicit_neutral.data_confidence_score


def test_context_shift_reduces_confidence_without_multiplying_reference_price():
    from gaijin_market_analytics.opportunities_v2 import OpportunityScoreV2

    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = tuple(_bucket(day, "20") for day in range(1, 7))
    context = (
        obs(
            effective_at=datetime(2026, 1, 7, tzinfo=UTC),
            known_at=datetime(2026, 1, 7, tzinfo=UTC),
            strength="1.20",
            attention="1.50",
        ),
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
        manual_context_observations=context,
    )
    assert result.reference_sell_price == Decimal("20")
    assert result.features.manual_strength_index == Decimal("1.20")
    assert result.features.manual_attention_index == Decimal("1.50")
    assert result.features.historical_context_comparability < Decimal("1")
    assert result.data_confidence_score is not None
    assert result.data_confidence_score < Decimal("100")
    assert "historical_context_comparability_reduced" in result.explanation_codes


def test_matching_historical_context_can_change_reference_by_reweighting_not_price_scaling():
    from gaijin_market_analytics.opportunities_v2 import OpportunityScoreV2

    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = (
        _bucket(1, "10"),
        _bucket(2, "10"),
        _bucket(3, "10"),
        _bucket(4, "100"),
        _bucket(5, "100"),
        _bucket(6, "100"),
    )
    context = (
        obs(
            effective_at=datetime(2026, 1, 1, tzinfo=UTC),
            known_at=datetime(2026, 1, 1, tzinfo=UTC),
            strength="0.50",
        ),
        obs(
            effective_at=datetime(2026, 1, 4, tzinfo=UTC),
            known_at=datetime(2026, 1, 4, tzinfo=UTC),
            strength="1.00",
        ),
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
        manual_context_observations=context,
    )
    assert result.features.historical_price_median == Decimal("55")
    assert result.features.context_weighted_historical_price_median == Decimal("100")
    assert result.reference_sell_price == Decimal("100")
    # No 2x price multiplication occurred: 100 is an observed historical price.
    assert result.reference_sell_price in {point.vwap_price for point in history}


def test_future_manual_context_change_cannot_change_past_opportunity_result():
    from gaijin_market_analytics.opportunities_v2 import OpportunityScoreV2

    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = tuple(_bucket(day, str(10 + day)) for day in range(1, 7))
    future = obs(
        effective_at=datetime(2026, 1, 5, tzinfo=UTC),
        known_at=datetime(2026, 2, 1, tzinfo=UTC),
        strength="1.80",
        attention="1.50",
    )
    scorer = OpportunityScoreV2()
    baseline = scorer.score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
    )
    with_future = scorer.score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
        manual_context_observations=(future,),
    )
    assert baseline.reference_sell_price == with_future.reference_sell_price
    assert baseline.score == with_future.score
    assert baseline.data_confidence_score == with_future.data_confidence_score


def test_extreme_context_shift_withholds_when_comparable_history_is_too_thin():
    from gaijin_market_analytics.opportunities_v2 import OpportunityScoreV2

    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = tuple(_bucket(day, "20") for day in range(1, 7))
    context = (
        obs(
            effective_at=datetime(2026, 1, 7, tzinfo=UTC),
            known_at=datetime(2026, 1, 7, tzinfo=UTC),
            strength="2.00",
            attention="2.00",
        ),
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
        manual_context_observations=context,
    )
    assert result.features.historical_effective_day_count == 6
    assert result.features.historical_context_effective_day_count < Decimal("3")
    assert result.score_published is False
    assert result.score is None
    assert "insufficient_context_comparable_history" in result.explanation_codes
