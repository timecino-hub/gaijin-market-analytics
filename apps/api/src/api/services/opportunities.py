from dataclasses import dataclass
from datetime import datetime, timedelta

from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.opportunities import OpportunityScoreResult, OpportunityScoreV1
from gaijin_market_analytics.registry import StrategyRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.services.analysis import AnalysisServiceResult, ItemAnalysisService


@dataclass(frozen=True)
class OpportunityServiceResult:
    analysis: AnalysisServiceResult
    opportunity: OpportunityScoreResult


class ItemOpportunityService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: StrategyRegistry,
    ) -> None:
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
        opportunity = self._scorer.score(
            analysis=analysis.result,
            observations=analysis.observations,
            maximum_snapshot_age=timedelta(hours=analysis.maximum_snapshot_age_hours),
            minimum_snapshot_count=analysis.minimum_snapshot_count,
        )
        return OpportunityServiceResult(
            analysis=analysis,
            opportunity=opportunity,
        )
