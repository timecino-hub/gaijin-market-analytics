"""A calculation endpoint: no session, persistence or Marketplace access."""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from gaijin_market_analytics.general_strategy import (
    GeneralStrategyError,
    GeneralStrategyInputs,
    evaluate_general_strategy,
)

from api.clock import UtcClock, get_utc_clock
from api.schemas.general_strategy import GeneralStrategyResponse

router = APIRouter(prefix="/api/v1/general-strategy", tags=["general-strategy"])


@router.get(
    "",
    response_model=GeneralStrategyResponse,
    summary="Read the candidate timing rule and calculate manual price thresholds",
    description="Uses supplied values and UTC time only. Does not read or write a database or place orders.",
)
def get_general_strategy(
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    baseline: str | None = None,
    observation_price: str | None = None,
    quoted_cost: str | None = None,
    event_closes_on: str | None = None,
    entry_cost: str | None = None,
    bought_at: str | None = None,
    fee_rate: str = "0.15",
    as_of: str | None = None,
) -> GeneralStrategyResponse:
    moment = (
        _timestamp(as_of, "as_of")
        if as_of is not None
        else _timestamp(clock().isoformat(), "as_of")
    )
    purchase = _timestamp(bought_at, "bought_at") if bought_at is not None else None
    if (entry_cost is None) != (purchase is None):
        _bad_request(
            "incomplete_purchase", "entry_cost and bought_at must be supplied together."
        )
    if purchase is not None and purchase > moment:
        _bad_request("bought_at_in_future", "bought_at cannot be later than as_of.")
    inputs = GeneralStrategyInputs(
        baseline=_amount(baseline, "baseline"),
        observation_price=_amount(observation_price, "observation_price"),
        quoted_cost=_amount(quoted_cost, "quoted_cost"),
        event_closes_on=_date(event_closes_on),
        entry_cost=_amount(entry_cost, "entry_cost"),
        bought_at=purchase,
        fee_rate=_fee(fee_rate),
    )
    try:
        result = evaluate_general_strategy(inputs, as_of=moment)
    except GeneralStrategyError as error:
        _bad_request(error.code, error.message)
    return GeneralStrategyResponse.model_validate(result)


def _decimal(value: str, name: str) -> Decimal:
    if len(value) > 64:
        _bad_request(
            f"invalid_{name}",
            f"{name} must be a finite decimal with at most 64 characters.",
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _bad_request(f"invalid_{name}", f"{name} must be a finite decimal.")
    if not parsed.is_finite():
        _bad_request(f"invalid_{name}", f"{name} must be a finite decimal.")
    return Decimal(0) if parsed == 0 else parsed


def _amount(value: str | None, name: str) -> Decimal | None:
    if value is None:
        return None
    parsed = _decimal(value, name)
    if parsed < Decimal("1e-18") or parsed > Decimal("1e12"):
        _bad_request(
            f"invalid_{name}", f"{name} must be between 1e-18 and 1e12 (inclusive)."
        )
    if len(parsed.as_tuple().digits) > 28:
        _bad_request(f"invalid_{name}", f"{name} may contain at most 28 significant digits.")
    return parsed


def _fee(value: str) -> Decimal:
    parsed = _decimal(value, "fee_rate")
    if (
        parsed < 0
        or parsed >= 1
        or (parsed != 0 and parsed.adjusted() < -18)
        or len(parsed.as_tuple().digits) > 28
    ):
        _bad_request(
            "invalid_fee_rate",
            "fee_rate must be zero or a decimal from 1e-18 (inclusive) to one (exclusive).",
        )
    return parsed


def _timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _bad_request(f"invalid_{name}", f"{name} must be an ISO-8601 timestamp.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _bad_request(f"{name}_timezone_required", f"{name} must include a timezone.")
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError):
        _bad_request(
            f"invalid_{name}", f"{name} is outside the supported UTC timestamp range."
        )


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _bad_request(
            "invalid_event_closes_on",
            "event_closes_on must be a calendar date (YYYY-MM-DD).",
        )
    if parsed.isoformat() != value:
        _bad_request(
            "invalid_event_closes_on",
            "event_closes_on must be a calendar date (YYYY-MM-DD).",
        )
    return parsed


def _bad_request(code: str, message: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message},
    )
