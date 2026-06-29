import pytest

from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult
from gaijin_market_analytics.exceptions import DuplicateStrategyError, StrategyNotFoundError
from gaijin_market_analytics.registry import StrategyRegistry
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1


class DummyStrategy:
    strategy_name = "dummy"
    strategy_version = "1.0.0"
    feature_version = "dummy_features_v1"

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        raise NotImplementedError


def test_registry_registers_gets_and_lists_in_stable_order() -> None:
    registry = StrategyRegistry()
    rule = RuleBasedV1()
    dummy = DummyStrategy()

    registry.register(rule)
    registry.register(dummy)

    assert registry.get("rule_based", "1.0.0") is rule
    assert [strategy.strategy_name for strategy in registry.list_strategies()] == ["dummy", "rule_based"]


def test_duplicate_strategy_registration_is_rejected() -> None:
    registry = StrategyRegistry()
    registry.register(DummyStrategy())

    with pytest.raises(DuplicateStrategyError):
        registry.register(DummyStrategy())


def test_missing_strategy_is_rejected() -> None:
    with pytest.raises(StrategyNotFoundError):
        StrategyRegistry().get("missing", "1.0.0")
