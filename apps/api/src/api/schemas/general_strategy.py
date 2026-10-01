"""Read-only contract for the adopted general timing rule and calculations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrategyContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GeneralStrategyPolicy(StrategyContract):
    candidate: Literal[False]
    designation: Literal["CURRENT_GENERAL_STRATEGY"]
    adoption_basis: Literal["USER_SELECTED"]
    observation_lead_days: int
    quote_check_lead_days: int
    observation_gate: str
    buy_cost_cap: str
    alternative_cost_cap: str
    preferred_net_target: str
    minimum_net_target: str
    lower_target_after_days: int
    review_after_days: int
    minimum_baseline_days: int
    baseline_method: str
    default_fee_is_assumption: Literal[True]
    force_liquidation: Literal[False]
    cost_input_basis: str


class GeneralStrategyEntry(StrategyContract):
    baseline: str | None
    observation_price: str | None
    quoted_cost: str | None
    observation_threshold: str | None
    buy_cost_ceiling: str | None
    alternative_buy_cost_ceiling: str | None
    observation_on: str | None
    quote_check_on: str | None
    status: Literal[
        "BASELINE_REQUIRED", "EVENT_DATE_REQUIRED", "OBSERVATION_NOT_COMPLETE",
        "ENTRY_WINDOW_CLOSED", "OBSERVATION_PRICE_REQUIRED", "OBSERVATION_GATE_FAILED",
        "QUOTED_COST_REQUIRED", "COST_CAP_EXCEEDED", "PRICE_CONDITIONS_SATISFIED",
    ]
    reason_codes: list[str]
    fill_claim: Literal[False]


class GeneralStrategyExit(StrategyContract):
    status: Literal["NOT_EVALUATED", "TARGET50", "TARGET40", "REVIEW_REQUIRED"]
    entry_cost: str | None
    bought_at: str | None
    preferred_offer: str | None
    minimum_offer: str | None
    lower_target_at: str | None
    review_at: str | None
    current_target: str | None
    planned_offer: str | None
    force_liquidation: Literal[False]
    fill_claim: Literal[False]


class GeneralStrategyResearch(StrategyContract):
    data_as_of: str
    opportunity_count: int
    cycle_count: int
    rule_count: int
    result_sha256: str
    execution_basis: str
    optimality_proven: Literal[False]
    fill_claim: Literal[False]


class GeneralStrategyExample(StrategyContract):
    item_name: str
    baseline: str
    entry_cost: str
    buy_evaluation_on: str
    offer50: str
    offer40: str
    condition_on: str
    condition_target: str
    condition_holding_days: int
    fill_claim: Literal[False]
    fee_assumption: str
    buy_cost_bias_assumption: str
    price_basis: str


class GeneralStrategyResponse(StrategyContract):

    version: str
    as_of: str
    research_only: Literal[True]
    automated_trading: Literal[False]
    fee_rate: str
    policy: GeneralStrategyPolicy
    entry: GeneralStrategyEntry
    exit: GeneralStrategyExit
    research: GeneralStrategyResearch
    examples: list[GeneralStrategyExample]
