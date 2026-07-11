from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.horizons import horizon_delta
from gaijin_market_analytics.opportunities import (
    OpportunityScoreResult,
    OpportunityScoreV1,
    rank_opportunity_scores,
)
from gaijin_market_analytics.registry import StrategyRegistry
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.db.models import Item, MarketSnapshot, OrderBookObservation
from api.services.analysis import (
    AnalysisServiceResult,
    InvalidAnalyticsConfigurationError,
    ItemAnalysisService,
)


class OpportunityCandidateLimitError(ValueError):
    def __init__(self, *, candidate_count: int, maximum_candidates: int) -> None:
        self.candidate_count = candidate_count
        self.maximum_candidates = maximum_candidates
        super().__init__(
            f"Opportunity scope contains {candidate_count} items; "
            f"maximum is {maximum_candidates}."
        )


@dataclass(frozen=True)
class OpportunityServiceResult:
    analysis: AnalysisServiceResult
    opportunity: OpportunityScoreResult


@dataclass(frozen=True)
class RankedOpportunityServiceItem:
    rank: int
    analysis: AnalysisServiceResult
    opportunity: OpportunityScoreResult


@dataclass(frozen=True)
class OpportunityRankingServiceResult:
    items: tuple[RankedOpportunityServiceItem, ...]
    page: int
    page_size: int
    total: int
    total_pages: int
    evaluated_total: int
    eligible_total: int
    as_of: datetime
    horizon: AnalysisHorizon
    maximum_snapshot_age_hours: int
    minimum_snapshot_count: int
    minimum_score: Decimal
    eligible_only: bool
    include_inactive: bool
    search: str | None
    category: str | None
    rarity: str | None

    @property
    def maximum_snapshot_age_seconds(self) -> int:
        return self.maximum_snapshot_age_hours * 3600


class ItemOpportunityService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: StrategyRegistry,
    ) -> None:
        self._session = session
        self._settings = settings
        self._analysis_service = ItemAnalysisService(session, settings, registry)
        self._scorer = OpportunityScoreV1()

    async def score_item(
        self,
        *,
        item_id: int,
        horizon: AnalysisHorizon,
        as_of: datetime,
    ) -> OpportunityServiceResult:
        analysis = await self._analysis_service.analyze_item(
            item_id=item_id,
            horizon=horizon,
            as_of=as_of,
        )
        opportunity = self._score_analysis(analysis)
        return OpportunityServiceResult(
            analysis=analysis,
            opportunity=opportunity,
        )

    async def list_ranked(
        self,
        *,
        horizon: AnalysisHorizon,
        as_of: datetime,
        page: int,
        page_size: int,
        eligible_only: bool,
        minimum_score: Decimal,
        search: str | None,
        category: str | None,
        rarity: str | None,
        include_inactive: bool,
    ) -> OpportunityRankingServiceResult:
        maximum_snapshot_age_hours = self._maximum_snapshot_age_hours()
        minimum_snapshot_count = self._minimum_snapshot_count()
        maximum_candidates = self._maximum_ranking_candidates()
        window_start = as_of - horizon_delta(horizon)

        items = await self._list_candidate_items(
            window_start=window_start,
            as_of=as_of,
            search=search,
            category=category,
            rarity=rarity,
            include_inactive=include_inactive,
            maximum_candidates=maximum_candidates,
        )
        rows_by_item = await self._list_observation_rows(
            item_ids=tuple(item.id for item in items),
            window_start=window_start,
            as_of=as_of,
        )

        analyses_by_item: dict[int, AnalysisServiceResult] = {}
        opportunities: list[OpportunityScoreResult] = []
        for item in items:
            analysis = self._analysis_service.analyze_loaded_rows(
                item=item,
                horizon=horizon,
                as_of=as_of,
                observation_rows=rows_by_item.get(item.id, []),
                maximum_snapshot_age_hours=maximum_snapshot_age_hours,
                minimum_snapshot_count=minimum_snapshot_count,
            )
            analyses_by_item[item.id] = analysis
            opportunities.append(self._score_analysis(analysis))

        ranked = rank_opportunity_scores(
            tuple(opportunities),
            eligible_only=eligible_only,
            minimum_score=minimum_score,
        )
        eligible_total = sum(opportunity.eligible for opportunity in opportunities)
        total = len(ranked)
        total_pages = (total + page_size - 1) // page_size
        page_start = (page - 1) * page_size
        page_entries = ranked[page_start : page_start + page_size]
        result_items = tuple(
            RankedOpportunityServiceItem(
                rank=entry.rank,
                analysis=analyses_by_item[entry.opportunity.item_id],
                opportunity=entry.opportunity,
            )
            for entry in page_entries
        )
        return OpportunityRankingServiceResult(
            items=result_items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            evaluated_total=len(opportunities),
            eligible_total=eligible_total,
            as_of=as_of,
            horizon=horizon,
            maximum_snapshot_age_hours=maximum_snapshot_age_hours,
            minimum_snapshot_count=minimum_snapshot_count,
            minimum_score=minimum_score,
            eligible_only=eligible_only,
            include_inactive=include_inactive,
            search=search,
            category=category,
            rarity=rarity,
        )

    def _score_analysis(self, analysis: AnalysisServiceResult) -> OpportunityScoreResult:
        return self._scorer.score(
            analysis=analysis.result,
            observations=analysis.observations,
            maximum_snapshot_age=timedelta(hours=analysis.maximum_snapshot_age_hours),
            minimum_snapshot_count=analysis.minimum_snapshot_count,
        )

    async def _list_candidate_items(
        self,
        *,
        window_start: datetime,
        as_of: datetime,
        search: str | None,
        category: str | None,
        rarity: str | None,
        include_inactive: bool,
        maximum_candidates: int,
    ) -> list[Item]:
        snapshot_in_window = exists(
            select(MarketSnapshot.id)
            .where(
                MarketSnapshot.item_id == Item.id,
                MarketSnapshot.observed_at >= window_start,
                MarketSnapshot.observed_at <= as_of,
            )
            .correlate(Item)
        )
        filters: list[object] = [snapshot_in_window]
        if not include_inactive:
            filters.append(Item.is_active.is_(True))
        if search:
            pattern = f"%{search}%"
            filters.append(or_(Item.name.ilike(pattern), Item.external_key.ilike(pattern)))
        if category:
            filters.append(Item.category == category)
        if rarity:
            filters.append(Item.rarity == rarity)

        statement = (
            select(Item)
            .where(*filters)
            .order_by(Item.id.asc())
            .limit(maximum_candidates + 1)
        )
        result = await self._session.execute(statement)
        items = list(result.scalars().all())
        if len(items) > maximum_candidates:
            raise OpportunityCandidateLimitError(
                candidate_count=len(items),
                maximum_candidates=maximum_candidates,
            )
        return items

    async def _list_observation_rows(
        self,
        *,
        item_ids: tuple[int, ...],
        window_start: datetime,
        as_of: datetime,
    ) -> dict[int, list[tuple[MarketSnapshot, OrderBookObservation | None]]]:
        if not item_ids:
            return {}
        statement = (
            select(MarketSnapshot, OrderBookObservation)
            .outerjoin(
                OrderBookObservation,
                OrderBookObservation.market_snapshot_id == MarketSnapshot.id,
            )
            .where(
                MarketSnapshot.item_id.in_(item_ids),
                MarketSnapshot.observed_at >= window_start,
                MarketSnapshot.observed_at <= as_of,
            )
            .order_by(
                MarketSnapshot.item_id.asc(),
                MarketSnapshot.observed_at.asc(),
                MarketSnapshot.id.asc(),
            )
        )
        result = await self._session.execute(statement)
        grouped: defaultdict[
            int, list[tuple[MarketSnapshot, OrderBookObservation | None]]
        ] = defaultdict(list)
        for snapshot, observation in result.all():
            grouped[snapshot.item_id].append((snapshot, observation))
        return dict(grouped)

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

    def _maximum_ranking_candidates(self) -> int:
        value = self._settings.analytics_opportunity_max_candidates
        if value <= 0:
            raise InvalidAnalyticsConfigurationError(
                "ANALYTICS_OPPORTUNITY_MAX_CANDIDATES must be greater than 0."
            )
        return value
