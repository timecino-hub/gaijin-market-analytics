from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.opportunities_v2 import (
    CurrentMarketEvidence,
    OpportunityScoreV2,
    OpportunityV2Config,
    OpportunityV2ContractError,
)
from gaijin_market_analytics.supply_channels import (
    DEFAULT_SUPPLY_CHANNEL_DECLARATIONS,
    SupplyChannelDeclaration,
    SupplyChannelError,
    assess_supply_channel_coverage,
    resolve_channel_coverage_withholding,
    unbound_declarations,
)

AS_OF = datetime(2026, 9, 21, tzinfo=UTC)


def _declaration(**overrides) -> SupplyChannelDeclaration:
    values = {
        "vehicle_name": "Kungstiger",
        "channels": ("CRAFT_EVENT",),
        "source_url": "https://warthunder.com/en/news/9989",
        "reviewed_at": AS_OF,
    }
    values.update(overrides)
    return SupplyChannelDeclaration(**values)


def test_a_craft_event_vehicle_with_no_review_is_only_partially_covered():
    coverage = assess_supply_channel_coverage(vehicle_name="Object 279")
    assert coverage.catalogued_tradeable_event_count > 0
    assert coverage.reviewed is False
    assert coverage.coverage == "PARTIALLY_CATALOGUED"
    assert "supply_channels_not_reviewed" in coverage.explanation_codes


def test_a_reviewed_craft_only_vehicle_is_fully_covered():
    coverage = assess_supply_channel_coverage(
        vehicle_name="Kungstiger",
        declarations=(_declaration(),),
    )
    assert coverage.coverage == "FULLY_CATALOGUED"
    assert coverage.reviewed is True
    assert coverage.unmodelled_channels == ()
    assert coverage.explanation_codes == ()


def test_a_seasonal_crate_vehicle_is_never_fully_covered():
    coverage = assess_supply_channel_coverage(vehicle_name="KV-IB (Germany)")
    assert coverage.catalogued_tradeable_event_count == 3
    assert coverage.coverage == "PARTIALLY_CATALOGUED"
    assert coverage.unmodelled_channels == ("SEASONAL_CRATE",)
    assert (
        "unmodelled_supply_channel_seasonal_crate" in coverage.explanation_codes
    )


def test_account_only_returns_are_not_a_claim_that_supply_never_arrives():
    # Ka-Chi has five catalogued returns, every one account-only, and is also in
    # a Battle Pass trophy. The catalog alone would read as "never tradeable".
    coverage = assess_supply_channel_coverage(vehicle_name="Ka-Chi")
    assert coverage.catalogued_account_only_event_count == 5
    assert coverage.catalogued_tradeable_event_count == 0
    assert coverage.coverage == "UNCATALOGUED"
    assert "BATTLE_TROPHY" in coverage.unmodelled_channels
    assert (
        "catalogued_returns_are_account_only_not_a_tradeable_supply_claim"
        in coverage.explanation_codes
    )


def test_a_vehicle_outside_the_catalog_is_uncatalogued_not_stable():
    coverage = assess_supply_channel_coverage(vehicle_name="CRV Block 2")
    assert coverage.coverage == "UNCATALOGUED"
    assert coverage.reviewed is False
    assert "no_modelled_tradeable_supply_channel" in coverage.explanation_codes


def test_declarations_that_do_not_bind_are_surfaced_not_hidden():
    names = unbound_declarations()
    assert "LVT-4/40" in names
    assert "Kungstiger" not in names


def test_shipped_declarations_all_carry_an_official_source():
    assert DEFAULT_SUPPLY_CHANNEL_DECLARATIONS
    for declaration in DEFAULT_SUPPLY_CHANNEL_DECLARATIONS:
        assert declaration.source_url.startswith("https://warthunder.com/")
        assert declaration.reviewed_at.tzinfo is not None
        assert declaration.channels


def test_declaration_contract_is_enforced():
    with pytest.raises(SupplyChannelError):
        _declaration(channels=())
    with pytest.raises(SupplyChannelError):
        _declaration(channels=("LOOT_BOX",))
    with pytest.raises(SupplyChannelError):
        _declaration(channels=("CRAFT_EVENT", "CRAFT_EVENT"))
    with pytest.raises(SupplyChannelError):
        _declaration(source_url="http://warthunder.com/x")
    with pytest.raises(SupplyChannelError):
        _declaration(reviewed_at=datetime(2026, 1, 1))
    with pytest.raises(SupplyChannelError):
        _declaration(vehicle_name="  ")


def test_ignore_policy_never_withholds():
    coverage = assess_supply_channel_coverage(vehicle_name="CRV Block 2")
    decision = resolve_channel_coverage_withholding(coverage=coverage)
    assert decision.policy == "IGNORE"
    assert decision.withhold is False


def test_uncatalogued_policy_acts_only_on_the_clear_cut_case():
    uncatalogued = assess_supply_channel_coverage(vehicle_name="CRV Block 2")
    partial = assess_supply_channel_coverage(vehicle_name="Object 279")
    assert (
        resolve_channel_coverage_withholding(
            coverage=uncatalogued, policy="WITHHOLD_UNCATALOGUED"
        ).withhold
        is True
    )
    assert (
        resolve_channel_coverage_withholding(
            coverage=partial, policy="WITHHOLD_UNCATALOGUED"
        ).withhold
        is False
    )


def test_unreviewed_policy_withholds_everything_not_fully_covered():
    partial = assess_supply_channel_coverage(vehicle_name="Object 279")
    full = assess_supply_channel_coverage(
        vehicle_name="Kungstiger", declarations=(_declaration(),)
    )
    assert (
        resolve_channel_coverage_withholding(
            coverage=partial, policy="WITHHOLD_UNREVIEWED"
        ).withhold
        is True
    )
    assert (
        resolve_channel_coverage_withholding(
            coverage=full, policy="WITHHOLD_UNREVIEWED"
        ).withhold
        is False
    )


def test_absent_coverage_never_withholds_on_its_own():
    decision = resolve_channel_coverage_withholding(policy="WITHHOLD_UNREVIEWED")
    assert decision.withhold is False
    assert decision.coverage is None
    assert "channel_coverage_unavailable_not_withheld" in decision.explanation_codes


def test_unsupported_policy_is_rejected():
    with pytest.raises(SupplyChannelError):
        resolve_channel_coverage_withholding(policy="WITHHOLD_EVERYTHING")  # type: ignore[arg-type]


def _bucket(offset: int) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=AS_OF - timedelta(days=8 - offset),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal("20"),
        reported_trade_volume=2,
    )


def _score(*, coverage=None, policy: str = "IGNORE"):
    scorer = OpportunityScoreV2(
        OpportunityV2Config(channel_coverage_policy=policy)  # type: ignore[arg-type]
    )
    return scorer.score(
        item_id=1,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=8),
        current_market=CurrentMarketEvidence(
            item_id=1,
            observed_at=AS_OF,
            best_ask=Decimal("10"),
            best_bid=Decimal("9"),
            observed_ask_quantity=20,
            observed_bid_quantity=20,
        ),
        historical_buckets=tuple(_bucket(offset) for offset in range(7)),
        supply_channel_coverage=coverage,
    )


def test_default_opportunity_behaviour_is_unchanged_by_coverage_evidence():
    baseline = _score()
    coverage = assess_supply_channel_coverage(vehicle_name="CRV Block 2")
    with_evidence = _score(coverage=coverage)
    assert baseline.score_published is True
    assert with_evidence.score_published is True
    assert baseline.score == with_evidence.score
    assert set(with_evidence.explanation_codes) - set(baseline.explanation_codes) == {
        "channel_coverage_policy_ignore",
        "supply_channel_coverage_uncatalogued",
    }


def test_an_uncatalogued_vehicle_can_be_withheld_instead_of_published():
    coverage = assess_supply_channel_coverage(vehicle_name="CRV Block 2")
    result = _score(coverage=coverage, policy="WITHHOLD_UNCATALOGUED")
    assert result.score_published is False
    assert result.score is None
    assert (
        "strategy_withheld_for_unmodelled_supply_channel"
        in result.explanation_codes
    )


def test_a_catalogued_vehicle_is_not_withheld_by_the_uncatalogued_policy():
    coverage = assess_supply_channel_coverage(vehicle_name="Object 279")
    result = _score(coverage=coverage, policy="WITHHOLD_UNCATALOGUED")
    assert result.score_published is True


def test_opportunity_rejects_an_unsupported_channel_policy():
    with pytest.raises(OpportunityV2ContractError):
        OpportunityV2Config(channel_coverage_policy="WITHHOLD_EVERYTHING")  # type: ignore[arg-type]
