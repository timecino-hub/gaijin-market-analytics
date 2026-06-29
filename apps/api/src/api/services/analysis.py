from dataclasses import dataclass
from datetime import datetime, timedelta

from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult
from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.exceptions import AnalyticsError, ContractValidationError
from gaijin_market_analytics.horizons import horizon_delta
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.registry import StrategyRegistry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.analytics import market_snapshots_to_observations
from api.config import Settings
from api.db.models import Item, MarketSnapshot
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
        snapshots = await self._list_snapshots(
            item_id=item_id,
            window_start=window_start,
            as_of=as_of,
        )
        observations = market_snapshots_to_observations(snapshots)

        try:
            request = AnalysisRequest(
                item_id=item.id,
                horizon=horizon,
                as_of=as_of,
                observations=observations,
                fee_policy=GAIJIN_MARKET_RULES_V1.fee_policy,
                market_rules=GAIJIN_MARKET_RULES_V1,
                maximum_snapshot_age=timedelta(hours=maximum_snapshot_age_hours),
                minimum_snapshot_count=minimum_snapshot_count,
            )
        except ContractValidationError as exc:
            raise AnalysisInputError("The analysis input contract was invalid.") from exc

        try:
            strategy = self._registry.get("rule_based", "1.0.0")
        except AnalyticsError as exc:
            raise StrategyUnavailableError("The configured analysis strategy is unavailable.") from exc

        try:
            result = strategy.analyze(request)
        except ContractValidationError as exc:
            raise AnalysisInputError("The analysis input contract was invalid.") from exc

        return AnalysisServiceResult(
            item=item,
            result=result,
            maximum_snapshot_age_hours=maximum_snapshot_age_hours,
            minimum_snapshot_count=minimum_snapshot_count,
        )

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
        window_start: datetime,
        as_of: datetime,
    ) -> list[MarketSnapshot]:
        statement = (
            select(MarketSnapshot)
            .where(
                MarketSnapshot.item_id == item_id,
                MarketSnapshot.observed_at >= window_start,
                MarketSnapshot.observed_at <= as_of,
            )
            .order_by(MarketSnapshot.observed_at.asc(), MarketSnapshot.id.asc())
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

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
