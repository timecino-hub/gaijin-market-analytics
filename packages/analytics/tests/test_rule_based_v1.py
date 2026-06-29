from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gaijin_market_analytics.contracts import AnalysisRequest, MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon, AnalysisStatus, ReasonCode
from gaijin_market_analytics.fees import GAIJIN_MARKET_FEE_POLICY_V1, FeePolicy, calculate_sale_proceeds
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1, RuleBasedV1Config


AS_OF = datetime(2026, 6, 29, tzinfo=UTC)


def obs(
    days_back: int,
    *,
    ask: str | None = "10",
    bid: str | None = "9",
    ask_count: int | None = 10,
    bid_count: int | None = 10,
    key: str | None = None,
) -> MarketObservation:
    return MarketObservation(
        observed_at=AS_OF - timedelta(days=days_back),
        best_ask=Decimal(ask) if ask is not None else None,
        best_bid=Decimal(bid) if bid is not None else None,
        ask_count=ask_count,
        bid_count=bid_count,
        estimated_volume=Decimal("100"),
        observation_key=key,
    )


def request(
    observations: tuple[MarketObservation, ...],
    *,
    horizon: AnalysisHorizon = AnalysisHorizon.DAYS_7,
    minimum_snapshot_count: int = 2,
    maximum_snapshot_age: timedelta = timedelta(days=2),
) -> AnalysisRequest:
    return AnalysisRequest(
        item_id=123,
        horizon=horizon,
        as_of=AS_OF,
        observations=observations,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        maximum_snapshot_age=maximum_snapshot_age,
        minimum_snapshot_count=minimum_snapshot_count,
    )


def test_empty_observations_return_insufficient_data() -> None:
    result = RuleBasedV1().analyze(request(()))

    assert result.status == AnalysisStatus.INSUFFICIENT_DATA
    assert ReasonCode.INSUFFICIENT_SNAPSHOTS in result.reason_codes
    assert result.observation_count == 0


def test_snapshot_count_insufficient_returns_reason() -> None:
    result = RuleBasedV1().analyze(request((obs(0),), minimum_snapshot_count=3))

    assert result.status == AnalysisStatus.INSUFFICIENT_DATA
    assert ReasonCode.INSUFFICIENT_SNAPSHOTS in result.reason_codes


def test_time_coverage_insufficient_returns_reason() -> None:
    result = RuleBasedV1().analyze(request((obs(1), obs(0)), minimum_snapshot_count=2))

    assert result.status == AnalysisStatus.INSUFFICIENT_DATA
    assert ReasonCode.INSUFFICIENT_TIME_COVERAGE in result.reason_codes


def test_latest_snapshot_too_old_returns_no_recent_market() -> None:
    result = RuleBasedV1().analyze(
        request((obs(7), obs(2)), maximum_snapshot_age=timedelta(days=1))
    )

    assert result.status == AnalysisStatus.NO_RECENT_MARKET
    assert ReasonCode.STALE_LATEST_SNAPSHOT in result.reason_codes


def test_no_valid_ask_or_bid_returns_no_valid_price() -> None:
    no_ask = RuleBasedV1().analyze(request((obs(7, ask=None), obs(0, ask=None))))
    no_bid = RuleBasedV1().analyze(request((obs(7, bid=None), obs(0, bid=None))))

    assert no_ask.status == AnalysisStatus.NO_VALID_PRICE
    assert ReasonCode.NO_CURRENT_ASK in no_ask.reason_codes
    assert no_bid.status == AnalysisStatus.NO_VALID_PRICE
    assert ReasonCode.NO_CURRENT_BID in no_bid.reason_codes


def test_zero_or_negative_prices_are_reported_without_unhandled_exception() -> None:
    result = RuleBasedV1().analyze(
        request((obs(7, ask="0", bid="-1"), obs(0, ask="10", bid="9")))
    )

    assert ReasonCode.INVALID_PRICE in result.reason_codes


def test_ask_less_than_bid_reports_large_negative_spread_as_invalid_price_context() -> None:
    result = RuleBasedV1().analyze(request((obs(7), obs(0, ask="8", bid="9"))))

    assert result.spread_absolute == Decimal("-1")
    assert ReasonCode.INVALID_PRICE in result.reason_codes
    assert ReasonCode.ANALYSIS_COMPLETED in result.reason_codes


def test_horizon_inside_empty_but_full_input_has_data() -> None:
    result = RuleBasedV1().analyze(request((obs(10), obs(9)), minimum_snapshot_count=1))

    assert result.observation_count == 0
    assert ReasonCode.INSUFFICIENT_SNAPSHOTS in result.reason_codes


def test_normal_rule_based_result_uses_median_bid_reference_price_and_decimal_outputs() -> None:
    result = RuleBasedV1().analyze(
        request(
            (
                obs(7, ask="11", bid="8", key="a"),
                obs(3, ask="10", bid="9", key="b"),
                obs(0, ask="12", bid="10", key="c"),
            ),
            minimum_snapshot_count=3,
        )
    )

    assert result.status == AnalysisStatus.OK
    assert result.strategy_name == "rule_based"
    assert result.strategy_version == "1.0.0"
    assert result.feature_version == "market_features_v1"
    assert result.reference_sell_price == Decimal("9")
    assert result.current_ask == Decimal("12")
    assert result.current_bid == Decimal("10")
    assert result.gross_profit == Decimal("-3")
    assert result.sale_proceeds == Decimal("7.65")
    assert result.fee_amount == Decimal("1.35")
    assert result.net_profit == Decimal("-4.35")
    assert result.break_even_sell_price == Decimal("14.12")
    assert result.break_even_reachable is True
    assert result.maximum_listing_price == Decimal("2000.00")
    assert result.maximum_sale_proceeds == Decimal("1700.00")
    assert result.maximum_net_profit == Decimal("1688.00")
    assert result.fee_policy_name == "gaijin_market"
    assert result.fee_policy_version == "1.0.0"
    assert result.nominal_fee_rate == Decimal("0.15")
    assert result.currency_quantum == Decimal("0.01")
    assert result.proceeds_rounding == "seller_proceeds_round_down"
    assert isinstance(result.net_profit, Decimal)
    assert isinstance(result.net_roi, Decimal)
    assert ReasonCode.ANALYSIS_COMPLETED in result.reason_codes


def test_reference_sell_price_is_quantized_down_to_currency_quantum() -> None:
    result = RuleBasedV1().analyze(
        request(
            (
                obs(7, ask="2.00", bid="1.00", key="a"),
                obs(3, ask="2.00", bid="1.01", key="b"),
                obs(0, ask="2.00", bid="1.02", key="c"),
                obs(0, ask="2.00", bid="1.03", key="d"),
            ),
            minimum_snapshot_count=4,
        )
    )

    assert result.median_bid == Decimal("1.015")
    assert result.reference_sell_price == Decimal("1.01")


def test_prices_above_market_cap_are_excluded_before_reference_median() -> None:
    result = RuleBasedV1(RuleBasedV1Config(minimum_coverage_ratio=Decimal("0"))).analyze(
        request(
            (
                obs(7, ask="2000.01", bid="2000.01", key="a"),
                obs(3, ask="100.00", bid="100.00", key="b"),
                obs(0, ask="120.00", bid="120.00", key="c"),
            ),
            minimum_snapshot_count=3,
        )
    )

    assert result.current_ask == Decimal("120.00")
    assert result.current_bid == Decimal("120.00")
    assert result.median_bid == Decimal("110.00")
    assert result.reference_sell_price == Decimal("110.00")
    assert result.reference_sell_price <= Decimal("2000.00")
    assert ReasonCode.PRICE_ABOVE_MARKET_CAP in result.reason_codes


def test_all_bids_above_market_cap_return_no_valid_bid() -> None:
    result = RuleBasedV1(RuleBasedV1Config(minimum_coverage_ratio=Decimal("0"))).analyze(
        request(
            (
                obs(7, ask="100.00", bid="2000.01", key="a"),
                obs(0, ask="120.00", bid="2500.00", key="b"),
            )
        )
    )

    assert result.status == AnalysisStatus.NO_VALID_PRICE
    assert result.current_bid is None
    assert result.reference_sell_price is None
    assert ReasonCode.NO_CURRENT_BID in result.reason_codes
    assert ReasonCode.PRICE_ABOVE_MARKET_CAP in result.reason_codes


def test_break_even_at_market_cap_is_reachable() -> None:
    result = RuleBasedV1(RuleBasedV1Config(minimum_coverage_ratio=Decimal("0"))).analyze(
        request(
            (
                obs(7, ask="1700.00", bid="2000.00", key="a"),
                obs(0, ask="1700.00", bid="2000.00", key="b"),
            )
        )
    )

    assert result.break_even_sell_price == Decimal("2000.00")
    assert result.break_even_reachable is True
    assert result.maximum_net_profit == Decimal("0.00")
    assert ReasonCode.BREAK_EVEN_UNREACHABLE_UNDER_MARKET_CAP not in result.reason_codes


def test_break_even_above_market_cap_is_unreachable_without_clamping() -> None:
    result = RuleBasedV1(RuleBasedV1Config(minimum_coverage_ratio=Decimal("0"))).analyze(
        request(
            (
                obs(7, ask="1700.01", bid="2000.00", key="a"),
                obs(0, ask="1700.01", bid="2000.00", key="b"),
            )
        )
    )

    assert result.break_even_sell_price is None
    assert result.break_even_reachable is False
    assert result.maximum_net_profit == Decimal("-0.01")
    assert ReasonCode.BREAK_EVEN_UNREACHABLE_UNDER_MARKET_CAP in result.reason_codes


def test_break_even_reachable_is_none_when_current_ask_is_missing() -> None:
    result = RuleBasedV1(RuleBasedV1Config(minimum_coverage_ratio=Decimal("0"))).analyze(
        request(
            (
                obs(7, ask=None, bid="100.00", key="a"),
                obs(0, ask=None, bid="120.00", key="b"),
            )
        )
    )

    assert result.break_even_sell_price is None
    assert result.break_even_reachable is None
    assert result.maximum_net_profit is None
    assert ReasonCode.BREAK_EVEN_UNREACHABLE_UNDER_MARKET_CAP not in result.reason_codes


def test_rule_based_v1_uses_request_fee_policy_and_not_free_fee_rate() -> None:
    policy = FeePolicy(
        name="gaijin_market",
        version="1.0.0",
        nominal_rate=Decimal("0.15"),
        currency_quantum=Decimal("0.01"),
        proceeds_rounding="seller_proceeds_round_down",
    )
    analysis_request = AnalysisRequest(
        item_id=123,
        horizon=AnalysisHorizon.DAYS_7,
        as_of=AS_OF,
        observations=(obs(7, ask="1.99", bid="1.99"), obs(0, ask="1.99", bid="1.99")),
        fee_policy=policy,
        market_rules=GAIJIN_MARKET_RULES_V1,
        maximum_snapshot_age=timedelta(days=2),
        minimum_snapshot_count=2,
    )

    result = RuleBasedV1(RuleBasedV1Config(minimum_coverage_ratio=Decimal("0"))).analyze(
        analysis_request
    )

    assert result.reference_sell_price == Decimal("1.99")
    assert result.sale_proceeds == Decimal("1.69")
    assert result.fee_amount == Decimal("0.30")
    assert result.net_profit == Decimal("-0.30")
    assert result.net_roi == Decimal("-0.30") / Decimal("1.99")
    assert calculate_sale_proceeds(result.reference_sell_price) == result.sale_proceeds


def test_scores_are_decimal_and_between_zero_and_100() -> None:
    result = RuleBasedV1().analyze(
        request((obs(7), obs(0, ask="10.5", bid="9.5")), minimum_snapshot_count=2)
    )

    for score in (result.liquidity_score, result.risk_score, result.confidence_score):
        assert isinstance(score, Decimal)
        assert Decimal("0") <= score <= Decimal("100")


def test_same_input_is_deterministic_and_independent_of_input_order() -> None:
    observations = (
        obs(0, ask="12", bid="10", key="b"),
        obs(7, ask="11", bid="8", key="a"),
        obs(0, ask="13", bid="10.5", key="a"),
    )
    reversed_observations = tuple(reversed(observations))

    first = RuleBasedV1().analyze(request(observations, minimum_snapshot_count=3))
    second = RuleBasedV1().analyze(request(reversed_observations, minimum_snapshot_count=3))

    assert first == second


def test_low_liquidity_and_large_spread_reasons_use_config_thresholds() -> None:
    config = RuleBasedV1Config(
        minimum_coverage_ratio=Decimal("0"),
        low_liquidity_count_threshold=Decimal("10"),
        large_spread_ratio_threshold=Decimal("0.05"),
    )
    result = RuleBasedV1(config).analyze(
        request(
            (
                obs(7, ask="20", bid="10", ask_count=1, bid_count=1),
                obs(0, ask="20", bid="10", ask_count=1, bid_count=1),
            ),
            minimum_snapshot_count=2,
        )
    )

    assert ReasonCode.LOW_LIQUIDITY in result.reason_codes
    assert ReasonCode.LARGE_SPREAD in result.reason_codes


def test_analytics_package_does_not_import_fastapi_sqlalchemy_database_or_http_clients() -> None:
    source_root = Path(__file__).parents[1] / "src" / "gaijin_market_analytics"
    forbidden = ("fastapi", "sqlalchemy", "psycopg", "requests", "httpx", "urllib")

    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), path
