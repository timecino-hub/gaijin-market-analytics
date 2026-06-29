from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from gaijin_market_analytics.backtesting import BacktestConfig, BacktestResult, run_backtest
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.registry import StrategyRegistry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.analytics import market_snapshots_to_observations
from api.config import Settings
from api.db.models import Item, MarketSnapshot
from api.services.items import ItemNotFoundError


class BacktestDataNotFoundError(LookupError):
    pass


class BacktestStrategyUnavailableError(LookupError):
    pass


@dataclass(frozen=True)
class ItemBacktestServiceResult:
    item: Item
    result: BacktestResult


class ItemBacktestService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: StrategyRegistry,
    ) -> None:
        self._session = session
        self._settings = settings
        self._registry = registry

    async def backtest_item(
        self,
        *,
        item_id: int,
        config: BacktestConfig,
    ) -> ItemBacktestServiceResult:
        item = await self._get_item(item_id)
        snapshots = await self._list_snapshots(item_id=item_id, config=config)
        if not snapshots:
            raise BacktestDataNotFoundError("No snapshots were found in the requested range.")
        try:
            strategy = self._registry.get(config.strategy_name, config.strategy_version)
        except Exception as exc:
            raise BacktestStrategyUnavailableError("The configured strategy is unavailable.") from exc

        observations = market_snapshots_to_observations(snapshots)
        result = run_backtest(
            observations=observations,
            config=config,
            fee_policy=GAIJIN_MARKET_RULES_V1.fee_policy,
            market_rules=GAIJIN_MARKET_RULES_V1,
            strategy=strategy,
        )
        return ItemBacktestServiceResult(item=item, result=result)

    async def _get_item(self, item_id: int) -> Item:
        statement = select(Item).where(Item.id == item_id).limit(1)
        item = await self._session.scalar(statement)
        if item is None:
            raise ItemNotFoundError(f"Item {item_id} was not found.")
        return item

    async def _list_snapshots(
        self,
        *,
        item_id: int,
        config: BacktestConfig,
    ) -> list[MarketSnapshot]:
        window_start = config.start_at - timedelta(days=config.lookback_horizon_days)
        window_end = config.end_at + timedelta(days=config.forward_horizon_days)
        statement = (
            select(MarketSnapshot)
            .where(
                MarketSnapshot.item_id == item_id,
                MarketSnapshot.observed_at >= window_start,
                MarketSnapshot.observed_at <= window_end,
            )
            .order_by(MarketSnapshot.observed_at.asc(), MarketSnapshot.id.asc())
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    def config_with_runtime_analysis_settings(self, config: BacktestConfig) -> BacktestConfig:
        return BacktestConfig(
            strategy_name=config.strategy_name,
            strategy_version=config.strategy_version,
            lookback_horizon_days=config.lookback_horizon_days,
            forward_horizon_days=config.forward_horizon_days,
            start_at=config.start_at,
            end_at=config.end_at,
            cadence_days=config.cadence_days,
            require_complete_forward_window=config.require_complete_forward_window,
            maximum_snapshot_age_hours=self._settings.analytics_maximum_snapshot_age_hours,
            minimum_snapshot_count=self._settings.analytics_minimum_snapshot_count,
        )
