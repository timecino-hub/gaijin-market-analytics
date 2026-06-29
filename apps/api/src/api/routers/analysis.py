from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.registry import StrategyRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from api.analytics_registry import get_strategy_registry
from api.clock import UtcClock, get_utc_clock
from api.config import Settings, get_settings
from api.db.session import get_session
from api.schemas.analysis import AnalysisEffectiveInputs, AnalysisResponse
from api.services.analysis import (
    AnalysisInputError,
    AnalysisServiceResult,
    InvalidAnalyticsConfigurationError,
    ItemAnalysisService,
    StrategyUnavailableError,
)
from api.services.items import ItemNotFoundError

router = APIRouter(prefix="/api/v1/items", tags=["analysis"])

ALLOWED_HORIZONS = {
    7: AnalysisHorizon.DAYS_7,
    30: AnalysisHorizon.DAYS_30,
    90: AnalysisHorizon.DAYS_90,
    180: AnalysisHorizon.DAYS_180,
}


@router.get("/{item_id}/analysis", response_model=AnalysisResponse)
async def get_item_analysis(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    horizon: str | None = None,
    fee_rate: str | None = None,
    as_of: str | None = None,
) -> AnalysisResponse:
    parsed_horizon = _parse_horizon(horizon)
    parsed_fee_rate = _parse_fee_rate(fee_rate)
    parsed_as_of = _parse_as_of(as_of, clock)

    service = ItemAnalysisService(session, settings, registry)
    try:
        result = await service.analyze_item(
            item_id=item_id,
            horizon=parsed_horizon,
            fee_rate=parsed_fee_rate,
            as_of=parsed_as_of,
        )
    except ItemNotFoundError as exc:
        raise _business_error(
            status.HTTP_404_NOT_FOUND,
            "item_not_found",
            "The requested item was not found.",
        ) from exc
    except AnalysisInputError as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "analysis_input_error",
            "The analysis input contract was invalid.",
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
            "analysis_unavailable",
            "Analysis is temporarily unavailable.",
        ) from exc

    return _analysis_response(result)


def _parse_horizon(value: str | None) -> AnalysisHorizon:
    if value is None or value == "":
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_horizon",
            "horizon must be one of: 7, 30, 90, 180.",
        )
    try:
        parsed = int(value)
    except ValueError as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_horizon",
            "horizon must be one of: 7, 30, 90, 180.",
        ) from exc
    try:
        return ALLOWED_HORIZONS[parsed]
    except KeyError as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_horizon",
            "horizon must be one of: 7, 30, 90, 180.",
        ) from exc


def _parse_fee_rate(value: str | None) -> Decimal:
    if value is None or value == "":
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_fee_rate",
            "fee_rate must satisfy 0 <= fee_rate < 1.",
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_fee_rate",
            "fee_rate must satisfy 0 <= fee_rate < 1.",
        ) from exc
    if not parsed.is_finite() or parsed < Decimal("0") or parsed >= Decimal("1"):
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_fee_rate",
            "fee_rate must satisfy 0 <= fee_rate < 1.",
        )
    return parsed


def _parse_as_of(value: str | None, clock: UtcClock) -> datetime:
    if value is None:
        return _normalize_as_of(clock())
    if value == "":
        raise _invalid_as_of_error()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _invalid_as_of_error() from exc
    return _normalize_as_of(parsed)


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


def _analysis_response(data: AnalysisServiceResult) -> AnalysisResponse:
    result = data.result
    item = data.item
    return AnalysisResponse(
        item_id=item.id,
        external_key=item.external_key,
        item_name=item.name,
        effective_inputs=AnalysisEffectiveInputs(
            horizon=result.horizon.value,
            as_of=result.as_of,
            fee_rate=data.fee_rate,
            maximum_snapshot_age_hours=data.maximum_snapshot_age_hours,
            minimum_snapshot_count=data.minimum_snapshot_count,
        ),
        status=result.status.value,
        strategy_name=result.strategy_name,
        strategy_version=result.strategy_version,
        feature_version=result.feature_version,
        observation_count=result.observation_count,
        first_observation_at=result.first_observation_at,
        last_observation_at=result.last_observation_at,
        current_ask=result.current_ask,
        current_bid=result.current_bid,
        reference_sell_price=result.reference_sell_price,
        gross_profit=result.gross_profit,
        net_profit=result.net_profit,
        net_roi=result.net_roi,
        break_even_sell_price=result.break_even_sell_price,
        spread_absolute=result.spread_absolute,
        spread_ratio=result.spread_ratio,
        median_bid=result.median_bid,
        median_ask=result.median_ask,
        price_volatility=result.price_volatility,
        liquidity_score=result.liquidity_score,
        risk_score=result.risk_score,
        confidence_score=result.confidence_score,
        reason_codes=[reason_code.value for reason_code in result.reason_codes],
    )


def _business_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
