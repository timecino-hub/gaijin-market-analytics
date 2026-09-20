from dataclasses import dataclass
from datetime import datetime, timedelta

from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult, MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.exceptions import AnalyticsError, ContractValidationError
from gaijin_market_analytics.horizons import horizon_delta
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.registry import StrategyRegistry
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.analytics import market_snapshot_rows_to_observations
from api.config import Settings
from api.db.models import Item, MarketSnapshot, OrderBookObservation
from api.services.items import ItemNotFoundError


class AnalysisInputError(ValueError):
    pass


class InvalidAnalyticsConfigurationError(ValueError):
    pass


class StrategyUnavailableError(LookupError):
    pass


@dataclass(frozen=True)
class AnalysisServiceResult:
    item: Item
    result: AnalysisResult
    observations: tuple[MarketObservation, ...]
    maximum_snapshot_age_hours: int
    minimum_snapshot_count: int

    @property
    def maximum_snapshot_age_seconds(self) -> int:
        return self.maximum_snapshot_age_hours * 3600


class ItemAnalysisService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: StrategyRegistry,
    ) -> None:
        self._session = session
        self._settings = settings
        self._registry = registry

    async def analyze_item(
        self,
        *,
        item_id: int,
        horizon: AnalysisHorizon,
        as_of: datetime,
    ) -> AnalysisServiceResult:
        item = await self._get_item(item_id)
        maximum_snapshot_age_hours = self._maximum_snapshot_age_hours()
        minimum_snapshot_count = self._minimum_snapshot_count()

        window_start = as_of - horizon_delta(horizon)
        observation_rows = await self._list_observation_rows(
            item_id=item_id,
            window_start=window_start,
            as_of=as_of,
        )
        return self.analyze_loaded_rows(
            item=item,
            horizon=horizon,
            as_of=as_of,
            observation_rows=observation_rows,
            maximum_snapshot_age_hours=maximum_snapshot_age_hours,
            minimum_snapshot_count=minimum_snapshot_count,
        )

    def analyze_loaded_rows(
        self,
        *,
        item: Item,
        horizon: AnalysisHorizon,
        as_of: datetime,
        observation_rows: list[tuple[MarketSnapshot, OrderBookObservation | None]],
        maximum_snapshot_age_hours: int | None = None,
        minimum_snapshot_count: int | None = None,
    ) -> AnalysisServiceResult:
        """Analyze preloaded rows so cross-item ranking avoids per-item DB queries."""

        resolved_maximum_age = (
            self._maximum_snapshot_age_hours()
            if maximum_snapshot_age_hours is None
            else maximum_snapshot_age_hours
        )
        resolved_minimum_count = (
            self._minimum_snapshot_count()
            if minimum_snapshot_count is None
            else minimum_snapshot_count
        )
        if resolved_maximum_age <= 0:
            raise InvalidAnalyticsConfigurationError(
                "ANALYTICS_MAXIMUM_SNAPSHOT_AGE_HOURS must be greater than 0."
            )
        if resolved_minimum_count <= 0:
            raise InvalidAnalyticsConfigurationError(
                "ANALYTICS_MINIMUM_SNAPSHOT_COUNT must be greater than 0."
            )

        observations = market_snapshot_rows_to_observations(observation_rows)
        try:
            request = AnalysisRequest(
                item_id=item.id,
                horizon=horizon,
                as_of=as_of,
                observations=observations,
                fee_policy=GAIJIN_MARKET_RULES_V1.fee_policy,
                market_rules=GAIJIN_MARKET_RULES_V1,
                maximum_snapshot_age=timedelta(hours=resolved_maximum_age),
                minimum_snapshot_count=resolved_minimum_count,
            )
        except ContractValidationError as exc:
            raise AnalysisInputError("The analysis input contract was invalid.") from exc

        try:
            strategy = self._registry.get(
                RuleBasedV1.strategy_name,
                RuleBasedV1.strategy_version,
            )
        except AnalyticsError as exc:
            raise StrategyUnavailableError("The configured analysis strategy is unavailable.") from exc

        try:
            result = strategy.analyze(request)
        except ContractValidationError as exc:
            raise AnalysisInputError("The analysis input contract was invalid.") from exc

        return AnalysisServiceResult(
            item=item,
            result=result,
            observations=observations,
            maximum_snapshot_age_hours=resolved_maximum_age,
            minimum_snapshot_count=resolved_minimum_count,
        )

    async def _get_item(self, item_id: int) -> Item:
        statement = select(Item).where(Item.id == item_id).limit(1)
        item = await self._session.scalar(statement)
        if item is None:
            raise ItemNotFoundError(f"Item {item_id} was not found.")
        return item

    async def _list_observation_rows(
        self,
        *,
        item_id: int,
        window_start: datetime,
        as_of: datetime,
    ) -> list[tuple[MarketSnapshot, OrderBookObservation | None]]:
        statement = (
            select(MarketSnapshot, OrderBookObservation)
            .outerjoin(
                OrderBookObservation,
                OrderBookObservation.market_snapshot_id == MarketSnapshot.id,
            )
            .where(
                MarketSnapshot.item_id == item_id,
                MarketSnapshot.observed_at >= window_start,
                MarketSnapshot.observed_at <= as_of,
            )
            .order_by(MarketSnapshot.observed_at.asc(), MarketSnapshot.id.asc())
        )
        result = await self._session.execute(statement)
        return [(snapshot, observation) for snapshot, observation in result.all()]

    def _maximum_snapshot_age_hours(self) -> int:
        value = self._settings.analytics_maximum_snapshot_age_hours
        if value <= 0:
            raise InvalidAnalyticsConfigurationError(
                "ANALYTICS_MAXIMUM_SNAPSHOT_AGE_HOURS must be greater than 0."
            )
        return value

    def _minimum_snapshot_count(self) -> int:
        value = self._settings.analytics_minimum_snapshot_count
        if value <= 0:
            raise InvalidAnalyticsConfigurationError(
                "ANALYTICS_MINIMUM_SNAPSHOT_COUNT must be greater than 0."
            )
        return value
