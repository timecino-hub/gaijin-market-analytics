from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping

from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.horizons import horizon_delta
from gaijin_market_analytics.manual_context import ManualMarketContextObservation
from gaijin_market_analytics.opportunities_v2 import (
    CurrentMarketEvidence,
    OpportunityScoreV2,
    OpportunityScoreV2Result,
    OpportunityV2Config,
    rank_opportunity_scores_v2,
)
from gaijin_market_analytics.registry import StrategyRegistry
from gaijin_market_analytics.reissue_exposure import (
    derive_reissue_exposure_features,
    merge_reissue_exposure_events,
    reissue_events_from_supply_pool,
)
from gaijin_market_analytics.reissue_exposure_catalog_v1 import (
    bind_supplemental_reissue_events,
)
from gaijin_market_analytics.supply_cycles import derive_supply_cycle_features
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.db.models import HistoricalTradeBucket, Item, MarketSnapshot, OrderBookObservation
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
    opportunity: OpportunityScoreV2Result


@dataclass(frozen=True)
class RankedOpportunityServiceItem:
    rank: int
    analysis: AnalysisServiceResult
    opportunity: OpportunityScoreV2Result


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
        manual_context_by_item: Mapping[
            int, tuple[ManualMarketContextObservation, ...]
        ] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._analysis_service = ItemAnalysisService(session, settings, registry)
        self._manual_context_by_item = dict(manual_context_by_item or {})

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
        window_start = as_of - horizon_delta(horizon)
        historical_rows = await self._list_historical_rows(
            item_ids=(item_id,),
            window_start=window_start,
            as_of=as_of,
        )
        opportunity = self._score_analysis(
            analysis,
            historical_rows.get(item_id, []),
            window_start=window_start,
        )
        return OpportunityServiceResult(analysis=analysis, opportunity=opportunity)

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
        item_ids = tuple(item.id for item in items)
        rows_by_item = await self._list_observation_rows(
            item_ids=item_ids,
            window_start=window_start,
            as_of=as_of,
        )
        history_by_item = await self._list_historical_rows(
            item_ids=item_ids,
            window_start=window_start,
            as_of=as_of,
        )

        analyses_by_item: dict[int, AnalysisServiceResult] = {}
        opportunities: list[OpportunityScoreV2Result] = []
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
            opportunities.append(
                self._score_analysis(
                    analysis,
                    history_by_item.get(item.id, []),
                    window_start=window_start,
                )
            )

        ranked = rank_opportunity_scores_v2(
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
                rank=page_start + index,
                analysis=analyses_by_item[opportunity.item_id],
                opportunity=opportunity,
            )
            for index, opportunity in enumerate(page_entries, start=1)
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

    def _score_analysis(
        self,
        analysis: AnalysisServiceResult,
        historical_rows: list[HistoricalTradeBucket],
        *,
        window_start: datetime,
    ) -> OpportunityScoreV2Result:
        current = self._current_market_evidence(analysis)
        history = tuple(self._historical_evidence(row) for row in historical_rows)
        scorer = OpportunityScoreV2(
            OpportunityV2Config(
                maximum_current_market_age=timedelta(
                    hours=analysis.maximum_snapshot_age_hours
                )
            )
        )
        catalog_facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(
            item_id=analysis.item.id,
            item_name=analysis.item.name,
        )
        supply_cycle = derive_supply_cycle_features(
            item_id=analysis.item.id,
            as_of=analysis.result.as_of,
            events=catalog_facts.events,
            memberships=catalog_facts.memberships,
        )
        supply_cycle = replace(
            supply_cycle,
            explanation_codes=tuple(
                dict.fromkeys((
                    *supply_cycle.explanation_codes,
                    f"supply_pool_catalog_{catalog_facts.match_status.lower()}",
                    f"supply_pool_catalog_version_{DEFAULT_SUPPLY_POOL_CATALOG.version.replace('.', '_')}",
                ))
            ),
        )
        reissue_events = merge_reissue_exposure_events(
            reissue_events_from_supply_pool(
                item_id=analysis.item.id,
                events=catalog_facts.events,
                memberships=catalog_facts.memberships,
            ),
            bind_supplemental_reissue_events(
                item_id=analysis.item.id,
                item_name=analysis.item.name,
            ),
        )
        reissue_exposure = derive_reissue_exposure_features(
            item_id=analysis.item.id,
            as_of=analysis.result.as_of,
            events=reissue_events,
        )
        return scorer.score(
            item_id=analysis.item.id,
            as_of=analysis.result.as_of,
            window_start=window_start,
            current_market=current,
            historical_buckets=history,
            supply_cycle=supply_cycle,
            reissue_exposure=reissue_exposure,
            manual_context_observations=self._manual_context_by_item.get(
                analysis.item.id, ()
            ),
        )

    @staticmethod
    def _current_market_evidence(
        analysis: AnalysisServiceResult,
    ) -> CurrentMarketEvidence | None:
        if not analysis.observations:
            return None
        latest = analysis.observations[-1]
        return CurrentMarketEvidence(
            item_id=analysis.item.id,
            observed_at=latest.observed_at,
            best_ask=latest.best_ask,
            best_bid=latest.best_bid,
            ask_count=latest.ask_count,
            bid_count=latest.bid_count,
            observed_ask_quantity=latest.observed_ask_quantity,
            observed_bid_quantity=latest.observed_bid_quantity,
        )

    @staticmethod
    def _historical_evidence(row: HistoricalTradeBucket) -> HistoricalTradeBucketEvidence:
        return HistoricalTradeBucketEvidence(
            item_id=row.item_id,
            granularity=row.granularity,  # type: ignore[arg-type]
            bucket_start_utc=row.bucket_start_utc,
            bucket_duration=timedelta(seconds=row.bucket_duration_seconds),
            vwap_price=row.reported_vwap_price,
            reported_trade_volume=row.reported_trade_volume,
            price_semantics=row.price_semantics,
            volume_semantics=row.volume_semantics,
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

    async def _list_historical_rows(
        self,
        *,
        item_ids: tuple[int, ...],
        window_start: datetime,
        as_of: datetime,
    ) -> dict[int, list[HistoricalTradeBucket]]:
        if not item_ids:
            return {}
        statement = (
            select(HistoricalTradeBucket)
            .where(
                HistoricalTradeBucket.item_id.in_(item_ids),
                HistoricalTradeBucket.bucket_start_utc >= window_start,
                HistoricalTradeBucket.bucket_start_utc <= as_of,
            )
            .order_by(
                HistoricalTradeBucket.item_id.asc(),
                HistoricalTradeBucket.bucket_start_utc.asc(),
                HistoricalTradeBucket.granularity.asc(),
                HistoricalTradeBucket.id.asc(),
            )
        )
        result = await self._session.scalars(statement)
        grouped: defaultdict[int, list[HistoricalTradeBucket]] = defaultdict(list)
        for row in result.all():
            grouped[row.item_id].append(row)
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
