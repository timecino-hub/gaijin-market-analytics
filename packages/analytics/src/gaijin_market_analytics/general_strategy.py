"""Versioned user-adopted general strategy; pure planning, no claimed fills."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext

GENERAL_STRATEGY_VERSION = "2026-10-01.2"
_QUANTUM = Decimal("0.01")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_DAY = timedelta(days=1)


class GeneralStrategyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class GeneralStrategyInputs:
    baseline: Decimal | None = None
    observation_price: Decimal | None = None
    quoted_cost: Decimal | None = None
    event_closes_on: date | None = None
    entry_cost: Decimal | None = None
    bought_at: datetime | None = None
    fee_rate: Decimal = Decimal("0.15")


def _money(value: Decimal | None, field: str) -> None:
    if value is not None and (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= _ZERO
        or value > Decimal("1000000000000")
    ):
        raise GeneralStrategyError("invalid_amount", f"{field} must be a finite positive Decimal <= 1e12.")


def _moment(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GeneralStrategyError("timezone_required", f"{field} must include a timezone.")
    return value.astimezone(UTC)


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _string(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def take_profit_offer(cost: Decimal, target: Decimal, fee_rate: Decimal) -> Decimal:
    """Ceil both required receipt and price, preserving the net target at cents."""
    _money(cost, "entry_cost")
    if not target.is_finite() or target <= _ZERO:
        raise GeneralStrategyError("invalid_target", "target must be finite and positive.")
    if not fee_rate.is_finite() or not _ZERO <= fee_rate < _ONE:
        raise GeneralStrategyError("invalid_fee_rate", "fee_rate must be in [0, 1).")
    with localcontext() as context:
        context.prec = 64
        needed = (cost * (_ONE + target)).quantize(_QUANTUM, rounding=ROUND_CEILING)
        return (needed / (_ONE - fee_rate)).quantize(_QUANTUM, rounding=ROUND_CEILING)


def evaluate_general_strategy(inputs: GeneralStrategyInputs, *, as_of: datetime) -> dict[str, object]:
    now = _moment(as_of, "as_of")
    for field in ("baseline", "observation_price", "quoted_cost", "entry_cost"):
        _money(getattr(inputs, field), field)
    if not isinstance(inputs.fee_rate, Decimal) or not inputs.fee_rate.is_finite() or not _ZERO <= inputs.fee_rate < _ONE:
        raise GeneralStrategyError("invalid_fee_rate", "fee_rate must be a finite Decimal in [0, 1).")
    if (inputs.entry_cost is None) != (inputs.bought_at is None):
        raise GeneralStrategyError("position_fields_required", "entry_cost and bought_at must be supplied together.")
    if inputs.event_closes_on is not None and (not isinstance(inputs.event_closes_on, date) or isinstance(inputs.event_closes_on, datetime)):
        raise GeneralStrategyError("invalid_date", "event_closes_on must be a UTC calendar date.")

    try:
        observation_on = inputs.event_closes_on - 3 * _DAY if inputs.event_closes_on else None
        quote_check_on = observation_on + _DAY if observation_on else None
    except OverflowError as error:
        raise GeneralStrategyError("invalid_date", "event_closes_on is outside the supported planning range.") from error
    with localcontext() as context:
        context.prec = 64
        threshold = inputs.baseline * Decimal("0.75") if inputs.baseline is not None else None
        cap = inputs.baseline * Decimal("0.55") if inputs.baseline is not None else None
        alternative_cap = inputs.baseline * Decimal("0.60") if inputs.baseline is not None else None
    if inputs.baseline is None:
        entry_status = "BASELINE_REQUIRED"
    elif observation_on is None:
        entry_status = "EVENT_DATE_REQUIRED"
    elif now < datetime.combine(quote_check_on, time.min, tzinfo=UTC):
        entry_status = "OBSERVATION_NOT_COMPLETE"
    elif now.date() > quote_check_on:
        entry_status = "ENTRY_WINDOW_CLOSED"
    elif inputs.observation_price is None:
        entry_status = "OBSERVATION_PRICE_REQUIRED"
    elif inputs.observation_price > threshold:
        entry_status = "OBSERVATION_GATE_FAILED"
    elif inputs.quoted_cost is None:
        entry_status = "QUOTED_COST_REQUIRED"
    elif inputs.quoted_cost > cap:
        entry_status = "COST_CAP_EXCEEDED"
    else:
        entry_status = "PRICE_CONDITIONS_SATISFIED"

    exit_plan: dict[str, object] = {
        "status": "NOT_EVALUATED", "entry_cost": _string(inputs.entry_cost), "bought_at": None,
        "preferred_offer": None, "minimum_offer": None, "lower_target_at": None,
        "review_at": None, "current_target": None, "planned_offer": None,
        "force_liquidation": False, "fill_claim": False,
    }
    if inputs.bought_at is not None and inputs.entry_cost is not None:
        bought = _moment(inputs.bought_at, "bought_at")
        if bought > now:
            raise GeneralStrategyError("bought_at_in_future", "bought_at cannot be later than as_of.")
        try:
            lower_at, review_at = bought + 150 * _DAY, bought + 180 * _DAY
        except OverflowError as error:
            raise GeneralStrategyError("invalid_date", "bought_at is outside the supported planning range.") from error
        preferred = take_profit_offer(inputs.entry_cost, Decimal("0.50"), inputs.fee_rate)
        minimum = take_profit_offer(inputs.entry_cost, Decimal("0.40"), inputs.fee_rate)
        phase = "TARGET50" if now < lower_at else "TARGET40" if now < review_at else "REVIEW_REQUIRED"
        exit_plan.update(
            status=phase, bought_at=_stamp(bought), preferred_offer=str(preferred), minimum_offer=str(minimum),
            lower_target_at=_stamp(lower_at), review_at=_stamp(review_at),
            current_target="0.50" if phase == "TARGET50" else "0.40" if phase == "TARGET40" else None,
            planned_offer=str(preferred) if phase == "TARGET50" else str(minimum) if phase == "TARGET40" else None,
        )

    return {
        "version": GENERAL_STRATEGY_VERSION, "as_of": _stamp(now), "research_only": True,
        "automated_trading": False, "fee_rate": format(inputs.fee_rate, "f"),
        "policy": {
            "candidate": False, "designation": "CURRENT_GENERAL_STRATEGY", "adoption_basis": "USER_SELECTED",
            "observation_lead_days": 3, "quote_check_lead_days": 2,
            "observation_gate": "0.75", "buy_cost_cap": "0.55", "alternative_cost_cap": "0.60",
            "preferred_net_target": "0.50", "minimum_net_target": "0.40",
            "lower_target_after_days": 150, "review_after_days": 180,
            "minimum_baseline_days": 5, "baseline_method": "MEDIAN_COMPLETE_DAYS_IN_PRE_EVENT_EQUAL_SPAN",
            "default_fee_is_assumption": True, "force_liquidation": False,
            "cost_input_basis": "ACTUAL_TOTAL_QUOTED_COST_NOT_DAILY_VWAP",
        },
        "entry": {
            "baseline": _string(inputs.baseline), "observation_price": _string(inputs.observation_price),
            "quoted_cost": _string(inputs.quoted_cost), "observation_threshold": _string(threshold),
            "buy_cost_ceiling": _string(cap), "alternative_buy_cost_ceiling": _string(alternative_cap),
            "observation_on": observation_on.isoformat() if observation_on else None,
            "quote_check_on": quote_check_on.isoformat() if quote_check_on else None,
            "status": entry_status, "reason_codes": [entry_status], "fill_claim": False,
        },
        "exit": exit_plan,
        "research": {
            "data_as_of": "2026-09-21T12:12:19.625000Z", "opportunity_count": 46, "cycle_count": 13,
            "rule_count": 90, "result_sha256": "f66d9252e7bb0e669114fb9d10fe994e2b0e2e5b4c83f5dd1aa045158bbcc794",
            "execution_basis": "PRICE_PROXY_AND_CONDITION_ONLY", "optimality_proven": False, "fill_claim": False,
        },
        "examples": [
            {"item_name": "TIS MA", "baseline": "102.78", "entry_cost": "47.1645792",
             "fee_assumption": "0.15", "buy_cost_bias_assumption": "0.044", "price_basis": "HISTORICAL_VWAP_PROXY_ONLY",
             "buy_evaluation_on": "2025-01-12", "offer50": "83.24", "offer40": "77.70",
             "condition_on": "2025-05-20", "condition_target": "0.50", "condition_holding_days": 128, "fill_claim": False},
            {"item_name": "AUBL/74 HVG", "baseline": "140.78", "entry_cost": "58.0837752",
             "fee_assumption": "0.15", "buy_cost_bias_assumption": "0.044", "price_basis": "HISTORICAL_VWAP_PROXY_ONLY",
             "buy_evaluation_on": "2024-09-02", "offer50": "102.51", "offer40": "95.68",
             "condition_on": "2025-01-30", "condition_target": "0.40", "condition_holding_days": 150, "fill_claim": False},
        ],
    }
