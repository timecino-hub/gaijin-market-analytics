from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.opportunities import OpportunityScoreV1
from gaijin_market_analytics.registry import StrategyRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from api.analytics_registry import get_strategy_registry
from api.clock import UtcClock, get_utc_clock
from api.config import Settings, get_settings
from api.db.session import get_session
from api.schemas.analysis import (
    AnalysisEffectiveInputs,
    AnalysisFeePolicy,
    AnalysisMarketRules,
    OpportunityRankingFilters,
    OpportunityRankingItem,
    OpportunityRankingResponse,
)
from api.services.analysis import (
    AnalysisInputError,
    InvalidAnalyticsConfigurationError,
    StrategyUnavailableError,
)
from api.services.opportunities import (
    ItemOpportunityService,
    OpportunityCandidateLimitError,
    OpportunityRankingServiceResult,
    RankedOpportunityServiceItem,
)

router = APIRouter(prefix="/api/v1/opportunities", tags=["opportunities"])

ALLOWED_HORIZONS = {
    7: AnalysisHorizon.DAYS_7,
    30: AnalysisHorizon.DAYS_30,
    90: AnalysisHorizon.DAYS_90,
    180: AnalysisHorizon.DAYS_180,
}


@router.get("", response_model=OpportunityRankingResponse)
async def list_opportunities(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    horizon: str | None = None,
    as_of: str | None = None,
    page: str = "1",
    page_size: str = "25",
    eligible_only: str = "true",
    min_score: str = "0",
    search: str | None = None,
    category: str | None = None,
    rarity: str | None = None,
    include_inactive: str = "false",
) -> OpportunityRankingResponse:
    _reject_fee_rate_query(request)
    parsed_horizon = _parse_horizon(horizon)
    parsed_as_of = _parse_as_of(as_of, clock)
    parsed_page = _parse_positive_int(page, "page", maximum=None)
    parsed_page_size = _parse_positive_int(page_size, "page_size", maximum=100)
    parsed_eligible_only = _parse_bool(eligible_only, "eligible_only")
    parsed_include_inactive = _parse_bool(include_inactive, "include_inactive")
    parsed_minimum_score = _parse_score(min_score)
    parsed_search = _normalize_filter(search, "search", maximum=200)
    parsed_category = _normalize_filter(category, "category", maximum=100)
    parsed_rarity = _normalize_filter(rarity, "rarity", maximum=100)

    service = ItemOpportunityService(session, settings, registry)
    try:
        result = await service.list_ranked(
            horizon=parsed_horizon,
            as_of=parsed_as_of,
            page=parsed_page,
            page_size=parsed_page_size,
            eligible_only=parsed_eligible_only,
            minimum_score=parsed_minimum_score,
            search=parsed_search,
            category=parsed_category,
            rarity=parsed_rarity,
            include_inactive=parsed_include_inactive,
        )
    except OpportunityCandidateLimitError as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "opportunity_scope_too_large",
            "The opportunity scope is too large; narrow the item filters before ranking.",
        ) from exc
    except AnalysisInputError as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "analysis_input_error",
            "The opportunity input contract was invalid.",
        ) from exc
    except StrategyUnavailableError as exc:
        raise _business_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "strategy_not_available",
            "The configured analysis strategy is not available.",
        ) from exc
    except InvalidAnalyticsConfigurationError as exc:
        raise _business_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "invalid_analytics_configuration",
            "The analytics configuration is invalid.",
        ) from exc
    except Exception as exc:
        raise _business_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "opportunity_ranking_unavailable",
            "Opportunity ranking is temporarily unavailable.",
        ) from exc

    return _ranking_response(result)


def _ranking_response(result: OpportunityRankingServiceResult) -> OpportunityRankingResponse:
    return OpportunityRankingResponse(
        items=[_ranking_item(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        total_pages=result.total_pages,
        evaluated_total=result.evaluated_total,
        eligible_total=result.eligible_total,
        effective_inputs=_effective_inputs(result),
        strategy_name=OpportunityScoreV1.strategy_name,
        strategy_version=OpportunityScoreV1.strategy_version,
        feature_version=OpportunityScoreV1.feature_version,
        filters=OpportunityRankingFilters(
            eligible_only=result.eligible_only,
            minimum_score=result.minimum_score,
            search=result.search,
            category=result.category,
            rarity=result.rarity,
            include_inactive=result.include_inactive,
        ),
    )


def _ranking_item(data: RankedOpportunityServiceItem) -> OpportunityRankingItem:
    analysis = data.analysis.result
    item = data.analysis.item
    opportunity = data.opportunity
    return OpportunityRankingItem(
        rank=data.rank,
        item_id=item.id,
        external_key=item.external_key,
        item_name=item.name,
        category=item.category,
        rarity=item.rarity,
        is_active=item.is_active,
        analysis_status=analysis.status.value,
        observation_count=analysis.observation_count,
        first_observation_at=analysis.first_observation_at,
        last_observation_at=analysis.last_observation_at,
        eligible=opportunity.eligible,
        score=opportunity.score,
        raw_score=opportunity.raw_score,
        profitability_score=opportunity.profitability_score,
        liquidity_score=opportunity.liquidity_score,
        stability_score=opportunity.stability_score,
        data_confidence_score=opportunity.data_confidence_score,
        freshness_score=opportunity.freshness_score,
        risk_penalty=opportunity.risk_penalty,
        liquidity_source=opportunity.liquidity_source,
        quantity_observation_count=opportunity.quantity_observation_count,
        latest_observed_bid_quantity=opportunity.latest_observed_bid_quantity,
        latest_observed_ask_quantity=opportunity.latest_observed_ask_quantity,
        current_ask=analysis.current_ask,
        current_bid=analysis.current_bid,
        reference_sell_price=analysis.reference_sell_price,
        net_profit=analysis.net_profit,
        net_roi=analysis.net_roi,
        explanation_codes=list(opportunity.explanation_codes),
        analysis_reason_codes=[code.value for code in analysis.reason_codes],
    )


def _effective_inputs(result: OpportunityRankingServiceResult) -> AnalysisEffectiveInputs:
    rules = GAIJIN_MARKET_RULES_V1
    return AnalysisEffectiveInputs(
        horizon=result.horizon.value,
        as_of=result.as_of,
        maximum_snapshot_age_seconds=result.maximum_snapshot_age_seconds,
        minimum_snapshot_count=result.minimum_snapshot_count,
        fee_policy=AnalysisFeePolicy(
            name=rules.fee_policy.name,
            version=rules.fee_policy.version,
            nominal_fee_rate=rules.fee_policy.nominal_rate,
            currency_quantum=rules.fee_policy.currency_quantum,
            proceeds_rounding=rules.fee_policy.proceeds_rounding,
        ),
        market_rules=AnalysisMarketRules(
            name=rules.name,
            version=rules.version,
            maximum_listing_price=rules.maximum_listing_price,
            maximum_sale_proceeds=rules.maximum_sale_proceeds,
            currency_quantum=rules.currency_quantum,
        ),
    )


def _parse_horizon(value: str | None) -> AnalysisHorizon:
    if value is None or value == "":
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_horizon",
            "horizon must be one of: 7, 30, 90, 180.",
        )
    try:
        parsed = int(value)
        return ALLOWED_HORIZONS[parsed]
    except (ValueError, KeyError) as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_horizon",
            "horizon must be one of: 7, 30, 90, 180.",
        ) from exc


def _parse_as_of(value: str | None, clock: UtcClock) -> datetime:
    if value is None:
        return _normalize_as_of(clock())
    if value == "":
        raise _invalid_as_of_error()
    try:
        return _normalize_as_of(datetime.fromisoformat(value))
    except ValueError as exc:
        raise _invalid_as_of_error() from exc


def _normalize_as_of(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _invalid_as_of_error()
    return value.astimezone(UTC)


def _invalid_as_of_error() -> HTTPException:
    return _business_error(
        status.HTTP_400_BAD_REQUEST,
        "invalid_as_of",
        "as_of must be an ISO-8601 datetime with timezone.",
    )


def _parse_positive_int(value: str, name: str, maximum: int | None) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise _pagination_error() from exc
    if parsed <= 0 or (maximum is not None and parsed > maximum):
        raise _pagination_error()
    return parsed


def _pagination_error() -> HTTPException:
    return _business_error(
        status.HTTP_400_BAD_REQUEST,
        "invalid_pagination",
        "page must be positive and page_size must be between 1 and 100.",
    )


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise _business_error(
        status.HTTP_400_BAD_REQUEST,
        "invalid_boolean",
        f"{name} must be true or false.",
    )


def _parse_score(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise _invalid_score_error() from exc
    if not parsed.is_finite() or not Decimal("0") <= parsed <= Decimal("100"):
        raise _invalid_score_error()
    return parsed.quantize(Decimal("0.01"))


def _invalid_score_error() -> HTTPException:
    return _business_error(
        status.HTTP_400_BAD_REQUEST,
        "invalid_min_score",
        "min_score must be a decimal between 0 and 100.",
    )


def _normalize_filter(value: str | None, name: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_filter",
            f"{name} is too long.",
        )
    return normalized


def _reject_fee_rate_query(request: Request) -> None:
    if "fee_rate" in request.query_params:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "fee_rate_not_configurable",
            "Gaijin Market uses a fixed 15% fee with seller proceeds rounded down to 0.01 GJN.",
        )


def _business_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
