from dataclasses import dataclass, field

from gaijin_market_analytics.exceptions import DuplicateStrategyError, StrategyNotFoundError
from gaijin_market_analytics.strategies.base import AnalysisStrategy


@dataclass
class StrategyRegistry:
    _strategies: dict[tuple[str, str], AnalysisStrategy] = field(default_factory=dict)

    def register(self, strategy: AnalysisStrategy) -> None:
        key = (strategy.strategy_name, strategy.strategy_version)
        if key in self._strategies:
            raise DuplicateStrategyError(
                f"Strategy {strategy.strategy_name} {strategy.strategy_version} is already registered."
            )
        self._strategies[key] = strategy

    def get(self, name: str, version: str) -> AnalysisStrategy:
        try:
            return self._strategies[(name, version)]
        except KeyError as exc:
            raise StrategyNotFoundError(f"Strategy {name} {version} was not found.") from exc

    def list_strategies(self) -> tuple[AnalysisStrategy, ...]:
        return tuple(
            self._strategies[key]
            for key in sorted(self._strategies, key=lambda value: (value[0], value[1]))
        )


def register(registry: StrategyRegistry, strategy: AnalysisStrategy) -> None:
    registry.register(strategy)


def get(registry: StrategyRegistry, name: str, version: str) -> AnalysisStrategy:
    return registry.get(name, version)


def list_strategies(registry: StrategyRegistry) -> tuple[AnalysisStrategy, ...]:
    return registry.list_strategies()
