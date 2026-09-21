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
    CURATED_VEHICLE_NAME_ALIASES,
    DEFAULT_SUPPLY_CHANNEL_OBSERVATIONS,
    SupplyChannelError,
    SupplyChannelObservation,
    VehicleNameAlias,
    assess_supply_channel_coverage,
    canonical_vehicle_name,
    derive_market_effect,
    observations_without_official_source,
    resolve_channel_coverage_withholding,
    unbound_observations,
)

AS_OF = datetime(2026, 9, 21, tzinfo=UTC)


def _observation(**overrides) -> SupplyChannelObservation:
    values = {
        "vehicle_name": "Kungstiger",
        "channel_family": "CRAFTING_EVENT",
        "delivery_mode": "TRADEABLE_COUPON",
        "asset_scope": "VEHICLE",
        "provenance": "OFFICIAL_ARTICLE",
        "source_url": "https://warthunder.com/en/news/9989",
        "reviewed_at": AS_OF,
    }
    values.update(overrides)
    return SupplyChannelObservation(**values)


@pytest.mark.parametrize(
    ("delivery_mode", "asset_scope", "expected"),
    [
        ("TRADEABLE_COUPON", "VEHICLE", "DIRECT_TRADEABLE_SUPPLY"),
        ("RANDOM_TRADEABLE_COUPON", "VEHICLE", "DIRECT_TRADEABLE_SUPPLY"),
        ("UPGRADEABLE_COUPON", "VEHICLE", "CONDITIONAL_TRADEABLE_SUPPLY"),
        ("NONTRADABLE_COUPON", "VEHICLE", "DEMAND_SUBSTITUTION"),
        ("ACCOUNT_GRANT", "VEHICLE", "DEMAND_SUBSTITUTION"),
        ("RANDOM_ACCOUNT_GRANT", "VEHICLE", "DEMAND_SUBSTITUTION"),
        ("UPGRADEABLE_COUPON", "MIXED", "CONDITIONAL_TRADEABLE_SUPPLY"),
        # A camouflage coupon never becomes a tradeable vehicle, whatever the
        # delivery mode of the crate that contained it.
        ("TRADEABLE_COUPON", "COSMETIC", "NO_VEHICLE_EFFECT"),
        ("RANDOM_TRADEABLE_COUPON", "COSMETIC", "NO_VEHICLE_EFFECT"),
    ],
)
def test_market_effect_separates_origin_from_tradeable_supply(
    delivery_mode, asset_scope, expected
):
    assert (
        derive_market_effect(delivery_mode=delivery_mode, asset_scope=asset_scope)
        == expected
    )


def test_one_family_can_deliver_differently_on_different_occasions():
    grant = _observation(
        channel_family="WARBOND_SHOP", delivery_mode="ACCOUNT_GRANT"
    )
    coupon = _observation(
        channel_family="WARBOND_SHOP", delivery_mode="TRADEABLE_COUPON"
    )
    assert grant.market_effect == "DEMAND_SUBSTITUTION"
    assert coupon.market_effect == "DIRECT_TRADEABLE_SUPPLY"


def test_a_craft_event_vehicle_with_no_review_is_only_partially_covered():
    coverage = assess_supply_channel_coverage(vehicle_name="Object 279")
    assert coverage.modelled_tradeable_event_count > 0
    assert coverage.reviewed is False
    assert coverage.coverage == "PARTIALLY_CATALOGUED"
    assert "supply_channels_not_reviewed" in coverage.explanation_codes


def test_a_reviewed_craft_only_vehicle_is_fully_covered():
    coverage = assess_supply_channel_coverage(
        vehicle_name="Kungstiger", observations=(_observation(),)
    )
    assert coverage.coverage == "FULLY_CATALOGUED"
    assert coverage.reviewed is True
    assert coverage.unmodelled_supply_channels == ()
    assert coverage.unmodelled_demand_channels == ()
    assert coverage.explanation_codes == ()


def test_a_limited_event_vehicle_has_supply_the_reissue_catalog_never_sees():
    coverage = assess_supply_channel_coverage(vehicle_name="MiG-25PD")
    assert coverage.reviewed is True
    assert coverage.modelled_tradeable_event_count == 0
    assert coverage.coverage == "UNCATALOGUED"
    assert coverage.unmodelled_supply_channels == ("LIMITED_EVENT",)
    assert (
        "unmodelled_tradeable_supply_channel_limited_event"
        in coverage.explanation_codes
    )


def test_coupons_reach_the_market_after_the_event_closes():
    coverage = assess_supply_channel_coverage(vehicle_name="CRV Block 2")
    # Winter Concert 2025 closed on 8 January; its coupons became tradeable on
    # the 12th, so the supply and the event window are not the same interval.
    assert coverage.earliest_unmodelled_tradeable_from == datetime(
        2026, 1, 12, tzinfo=UTC
    )


def test_the_earliest_tradeable_date_is_a_lower_bound_not_an_observation():
    # Paintball Arena opened 22 January; stage 8 issues the coupon no earlier
    # than 5 February and the article imposes a six-day sale cooldown.
    coverage = assess_supply_channel_coverage(vehicle_name="T-72B3 Arena")
    assert coverage.earliest_unmodelled_tradeable_from == datetime(
        2026, 2, 11, 11, tzinfo=UTC
    )
    scimitar = assess_supply_channel_coverage(vehicle_name="Scimitar Mk.2")
    assert scimitar.earliest_unmodelled_tradeable_from == datetime(
        2026, 8, 9, 11, tzinfo=UTC
    )


def test_account_only_returns_are_not_a_claim_that_supply_never_arrives():
    # Ka-Chi has five catalogued returns, every one account-only, and is also in
    # a Battle Pass trophy. The catalog alone would read as "never tradeable".
    coverage = assess_supply_channel_coverage(vehicle_name="Ka-Chi")
    assert coverage.catalogued_account_only_event_count == 5
    assert coverage.modelled_tradeable_event_count == 0
    assert coverage.coverage == "UNCATALOGUED"
    assert coverage.unmodelled_supply_channels == ()
    assert coverage.unmodelled_demand_channels == ("BATTLE_TROPHY",)
    assert (
        "catalogued_returns_are_account_only_not_a_tradeable_supply_claim"
        in coverage.explanation_codes
    )
    assert (
        "unmodelled_demand_substitution_channel_battle_trophy"
        in coverage.explanation_codes
    )


def test_a_multi_channel_vehicle_reports_every_family():
    coverage = assess_supply_channel_coverage(vehicle_name="KV-IB (Germany)")
    assert coverage.modelled_tradeable_event_count == 3
    assert coverage.observed_channel_families == ("SEASONAL_CRATE", "WARBOND_SHOP")
    assert coverage.unmodelled_demand_channels == ("SEASONAL_CRATE", "WARBOND_SHOP")
    assert coverage.coverage == "PARTIALLY_CATALOGUED"


def test_a_cosmetic_only_channel_is_ignored_by_the_vehicle_supply_model():
    coverage = assess_supply_channel_coverage(
        vehicle_name="Kungstiger",
        observations=(
            _observation(),
            _observation(
                channel_family="SEASONAL_CRATE",
                delivery_mode="RANDOM_TRADEABLE_COUPON",
                asset_scope="COSMETIC",
            ),
        ),
    )
    assert coverage.ignored_cosmetic_channels == ("SEASONAL_CRATE",)
    assert coverage.unmodelled_supply_channels == ()
    assert coverage.unmodelled_demand_channels == ()
    assert coverage.coverage == "FULLY_CATALOGUED"
    assert "cosmetic_channel_ignored_seasonal_crate" in coverage.explanation_codes


def test_an_unreviewed_vehicle_outside_the_catalog_is_uncatalogued():
    coverage = assess_supply_channel_coverage(vehicle_name="Definitely Not A Vehicle")
    assert coverage.coverage == "UNCATALOGUED"
    assert coverage.reviewed is False
    assert "no_modelled_tradeable_supply_channel" in coverage.explanation_codes


def test_every_shipped_observation_now_has_an_official_source():
    assert observations_without_official_source() == ()


def test_an_unsourced_observation_is_flagged_when_one_exists():
    coverage = assess_supply_channel_coverage(
        vehicle_name="Kungstiger",
        observations=(
            _observation(provenance="OPERATOR_REVIEW", source_url=None),
        ),
    )
    assert coverage.reviewed is True
    assert (
        "supply_channel_observation_not_independently_sourced"
        in coverage.explanation_codes
    )


def test_observations_that_do_not_bind_are_surfaced_not_hidden():
    names = unbound_observations()
    # First-issue limited-event vehicles legitimately have no reissue history.
    assert "MiG-25PD" in names
    assert "LVT-4/40" in names
    assert "Ka-Chi" not in names
    # Resolved by the curated alias, so no longer an unexplained gap.
    assert "Type K-8 No.13i" not in names
    assert "Type K-8 No.13" not in names


def test_a_curated_alias_resolves_one_confirmed_spelling_only():
    assert canonical_vehicle_name("Type K-8 No.13i") == "Type K-8 No.13"
    # Never fuzzy: a near miss must stay unresolved rather than bind to a
    # different vehicle's supply events.
    assert canonical_vehicle_name("Type K-8 No.13x") == "Type K-8 No.13x"
    assert canonical_vehicle_name("Type K-8") == "Type K-8"
    assert canonical_vehicle_name("Kungstiger") == "Kungstiger"


def test_both_spellings_resolve_to_the_same_coverage():
    typo = assess_supply_channel_coverage(vehicle_name="Type K-8 No.13i")
    canonical = assess_supply_channel_coverage(vehicle_name="Type K-8 No.13")
    assert typo.observation_count == canonical.observation_count == 1
    assert typo.unmodelled_demand_channels == canonical.unmodelled_demand_channels
    assert typo.coverage == canonical.coverage


def test_alias_contract_is_enforced():
    values = {
        "alias": "Type K-8 No.13i",
        "canonical_name": "Type K-8 No.13",
        "basis": "OFFICIAL_ARTICLE_TYPO",
        "source_url": "https://warthunder.com/en/news/9334",
        "source_label": "Battle Pass season 18",
        "reviewed_at": AS_OF,
    }
    VehicleNameAlias(**values)
    for override in (
        {"alias": "  "},
        {"canonical_name": "  "},
        {"canonical_name": "Type K-8 No.13i"},
        {"basis": "GUESS"},
        {"source_url": "http://warthunder.com/x"},
        {"source_label": " "},
        {"reviewed_at": datetime(2026, 1, 1)},
    ):
        with pytest.raises(SupplyChannelError):
            VehicleNameAlias(**{**values, **override})


def test_the_alias_table_stays_hand_curated():
    assert len(CURATED_VEHICLE_NAME_ALIASES) == 1
    alias = CURATED_VEHICLE_NAME_ALIASES[0]
    assert alias.basis == "OFFICIAL_ARTICLE_TYPO"
    assert alias.source_url.startswith("https://warthunder.com/")


def test_shipped_observations_keep_their_provenance_contract():
    assert DEFAULT_SUPPLY_CHANNEL_OBSERVATIONS
    for value in DEFAULT_SUPPLY_CHANNEL_OBSERVATIONS:
        if value.provenance == "OFFICIAL_ARTICLE":
            assert value.source_url is not None
            assert value.source_url.startswith("https://warthunder.com/")
        assert value.reviewed_at.tzinfo is not None


def test_observation_contract_is_enforced():
    with pytest.raises(SupplyChannelError):
        _observation(channel_family="LOOT_BOX")
    with pytest.raises(SupplyChannelError):
        _observation(delivery_mode="FREE")
    with pytest.raises(SupplyChannelError):
        _observation(asset_scope="DECAL")
    with pytest.raises(SupplyChannelError):
        _observation(provenance="RUMOUR")
    with pytest.raises(SupplyChannelError):
        _observation(source_url=None)
    with pytest.raises(SupplyChannelError):
        _observation(source_url="http://warthunder.com/x")
    with pytest.raises(SupplyChannelError):
        _observation(reviewed_at=datetime(2026, 1, 1))
    with pytest.raises(SupplyChannelError):
        _observation(vehicle_name="  ")
    with pytest.raises(SupplyChannelError):
        _observation(
            occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
            earliest_tradeable_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(SupplyChannelError):
        _observation(
            asset_scope="COSMETIC",
            earliest_tradeable_from=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_an_operator_review_may_omit_the_source_url():
    value = _observation(provenance="OPERATOR_REVIEW", source_url=None)
    assert value.source_url is None
    assert value.market_effect == "DIRECT_TRADEABLE_SUPPLY"


def test_ignore_policy_never_withholds():
    coverage = assess_supply_channel_coverage(vehicle_name="MiG-25PD")
    assert resolve_channel_coverage_withholding(coverage=coverage).withhold is False


def test_each_policy_widens_the_withholding_set():
    uncatalogued = assess_supply_channel_coverage(vehicle_name="MiG-25PD")
    unmodelled_supply = assess_supply_channel_coverage(
        vehicle_name="Kungstiger",
        observations=(
            _observation(),
            _observation(
                channel_family="LIMITED_EVENT", delivery_mode="UPGRADEABLE_COUPON"
            ),
        ),
    )
    unreviewed = assess_supply_channel_coverage(vehicle_name="Object 279")
    full = assess_supply_channel_coverage(
        vehicle_name="Kungstiger", observations=(_observation(),)
    )

    def held(coverage, policy):
        return resolve_channel_coverage_withholding(
            coverage=coverage, policy=policy
        ).withhold

    assert unmodelled_supply.coverage == "PARTIALLY_CATALOGUED"
    assert held(uncatalogued, "WITHHOLD_UNCATALOGUED") is True
    assert held(unmodelled_supply, "WITHHOLD_UNCATALOGUED") is False
    assert held(unmodelled_supply, "WITHHOLD_UNMODELLED_SUPPLY") is True
    assert held(unreviewed, "WITHHOLD_UNMODELLED_SUPPLY") is False
    assert held(unreviewed, "WITHHOLD_UNREVIEWED") is True
    assert held(full, "WITHHOLD_UNREVIEWED") is False


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
    coverage = assess_supply_channel_coverage(vehicle_name="MiG-25PD")
    with_evidence = _score(coverage=coverage)
    assert baseline.score_published is True
    assert with_evidence.score_published is True
    assert baseline.score == with_evidence.score
    assert set(with_evidence.explanation_codes) - set(baseline.explanation_codes) == {
        "channel_coverage_policy_ignore",
        "supply_channel_coverage_uncatalogued",
    }


def test_a_limited_event_vehicle_can_be_withheld_instead_of_published():
    coverage = assess_supply_channel_coverage(vehicle_name="MiG-25PD")
    result = _score(coverage=coverage, policy="WITHHOLD_UNCATALOGUED")
    assert result.score_published is False
    assert result.score is None
    assert (
        "strategy_withheld_for_unmodelled_supply_channel"
        in result.explanation_codes
    )


def test_a_catalogued_vehicle_is_not_withheld_by_the_uncatalogued_policy():
    coverage = assess_supply_channel_coverage(vehicle_name="Object 279")
    assert _score(coverage=coverage, policy="WITHHOLD_UNCATALOGUED").score_published


def test_opportunity_rejects_an_unsupported_channel_policy():
    with pytest.raises(OpportunityV2ContractError):
        OpportunityV2Config(channel_coverage_policy="WITHHOLD_EVERYTHING")  # type: ignore[arg-type]
