from functools import lru_cache

from gaijin_market_analytics.registry import StrategyRegistry
from gaijin_market_analytics.strategies.base import AnalysisStrategy
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1


@lru_cache(maxsize=1)
def get_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(RuleBasedV1())
    return registry


def get_rule_based_strategy() -> AnalysisStrategy:
    return get_strategy_registry().get("rule_based", "1.0.0")
