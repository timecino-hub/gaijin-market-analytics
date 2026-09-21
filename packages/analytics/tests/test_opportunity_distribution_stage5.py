from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.manual_context import ManualMarketContextObservation
from gaijin_market_analytics.opportunities_v2 import CurrentMarketEvidence, OpportunityScoreV2


def _bucket(start: datetime, price: str) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=start,
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=1,
    )


def _market(as_of: datetime, ask: str = "10", bid: str = "9") -> CurrentMarketEvidence:
    return CurrentMarketEvidence(
        item_id=1,
        observed_at=as_of,
        best_ask=Decimal(ask),
        best_bid=Decimal(bid),
        observed_ask_quantity=20,
        observed_bid_quantity=20,
    )


def test_neutral_history_has_full_context_support_and_empirical_profit_share():
    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = tuple(
        _bucket(datetime(2026, 1, day, tzinfo=UTC), "20")
        for day in range(1, 7)
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
    )
    assert result.features.historical_context_effective_day_count == Decimal("6")
    assert result.features.historical_context_effective_sample_size == Decimal("6")
    assert result.features.historical_context_high_quality_day_count == Decimal("6")
    assert result.features.historical_profitable_context_weight_share == Decimal("1")
    assert result.eligible is True


def test_exact_half_profitable_history_is_not_a_majority_even_if_median_reference_is_positive():
    as_of = datetime(2026, 1, 8, tzinfo=UTC)
    history = tuple(
        _bucket(datetime(2026, 1, day, tzinfo=UTC), price)
        for day, price in enumerate(("10", "10", "10", "20", "20", "20"), start=1)
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of, ask="12", bid="11"),
        historical_buckets=history,
    )
    assert result.score_published is True
    assert result.reference_sell_price == Decimal("15")
    assert result.sale_proceeds == Decimal("12.75")
    assert result.net_profit == Decimal("0.75")
    assert result.features.historical_profitable_context_weight_share == Decimal("0.5")
    assert result.eligible is False
    assert "historical_profitability_majority_not_supported" in result.explanation_codes


def test_profitability_score_is_discounted_by_empirical_profitable_support():
    as_of = datetime(2026, 1, 7, tzinfo=UTC)
    history = tuple(
        _bucket(datetime(2026, 1, day, tzinfo=UTC), price)
        for day, price in enumerate(("10", "20", "20", "20", "20"), start=1)
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of, ask="12", bid="11"),
        historical_buckets=history,
    )
    assert result.features.historical_profitable_context_weight_share == Decimal("0.8")
    # Reference 20 => after-fee ROI 5/12; raw ROI profitability saturates at 100,
    # then empirical support discounts it to 80.
    assert result.profitability_score == Decimal("80.00")
    assert result.eligible is True


def test_many_uniformly_low_comparability_days_cannot_fake_high_quality_history():
    as_of = datetime(2026, 5, 1, tzinfo=UTC)
    start = as_of - timedelta(days=100)
    history = tuple(_bucket(start + timedelta(days=i), "20") for i in range(100))
    context = (
        ManualMarketContextObservation(
            item_id=1,
            effective_at=as_of,
            known_at=as_of,
            strength_index=Decimal("10"),
            attention_index=Decimal("1"),
        ),
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=start,
        current_market=_market(as_of),
        historical_buckets=history,
        manual_context_observations=context,
    )
    # 99 days at 0.1 similarity + the boundary day at 1.0 similarity.
    assert result.features.historical_context_effective_day_count > Decimal("3")
    assert result.features.historical_context_effective_sample_size > Decimal("3")
    assert result.features.historical_context_high_quality_day_count < Decimal("3")
    assert result.score_published is True
    assert result.data_confidence_score is not None
    assert result.data_confidence_score < Decimal("70")
    assert "historical_context_quality_discount_applied" in result.explanation_codes


def test_partial_current_utc_day_hourly_evidence_uses_actual_availability_for_context():
    as_of = datetime(2026, 1, 8, 12, 30, tzinfo=UTC)
    history = tuple(
        _bucket(datetime(2026, 1, day, tzinfo=UTC), "20")
        for day in range(1, 7)
    ) + (
        HistoricalTradeBucketEvidence(
            item_id=1,
            granularity="1h",
            bucket_start_utc=datetime(2026, 1, 8, 10, 0, tzinfo=UTC),
            bucket_duration=timedelta(hours=1),
            vwap_price=Decimal("21"),
            reported_trade_volume=1,
        ),
    )
    result = OpportunityScoreV2().score(
        item_id=1,
        as_of=as_of,
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_market=_market(as_of),
        historical_buckets=history,
    )
    assert result.score_published is True
    assert result.features.historical_end_at == datetime(2026, 1, 8, 11, 0, tzinfo=UTC)
