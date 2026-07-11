from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from gaijin_market_analytics.backtesting.calibration_contracts import (
    ItemMarketHistory,
    OpportunityCalibrationConfig,
)
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.fees import FeePolicy
from gaijin_market_analytics.market_rules import MarketRules


def dataset_sha256(histories: tuple[ItemMarketHistory, ...]) -> str:
    payload = [
        {
            "item_id": history.item_id,
            "observations": [
                {
                    "observed_at": observation.observed_at.isoformat(),
                    "best_ask": _decimal_text(observation.best_ask),
                    "best_bid": _decimal_text(observation.best_bid),
                    "ask_count": observation.ask_count,
                    "bid_count": observation.bid_count,
                    "estimated_volume": _decimal_text(observation.estimated_volume),
                    "observation_key": observation.observation_key,
                    "observed_ask_quantity": observation.observed_ask_quantity,
                    "observed_bid_quantity": observation.observed_bid_quantity,
                    "quantity_semantics": observation.quantity_semantics,
                    "source_type": observation.source_type,
                    "review_status": observation.review_status,
                }
                for observation in history.observations
            ],
        }
        for history in sorted(histories, key=lambda value: value.item_id)
    ]
    return _sha256_json(payload)


def configuration_sha256(
    *,
    config: OpportunityCalibrationConfig,
    analysis_strategy: object,
    opportunity_scorer: object,
    fee_policy: FeePolicy,
    market_rules: MarketRules,
) -> str:
    payload = {
        "calibration": _canonical_json_value(config),
        "analysis_strategy": _strategy_payload(analysis_strategy),
        "opportunity_scorer": _strategy_payload(opportunity_scorer),
        "fee_policy": _canonical_json_value(fee_policy),
        "market_rules": _canonical_json_value(market_rules),
    }
    return _sha256_json(payload)


def _strategy_payload(value: object) -> dict[str, object]:
    return {
        "class": f"{value.__class__.__module__}.{value.__class__.__qualname__}",
        "strategy_name": getattr(value, "strategy_name", None),
        "strategy_version": getattr(value, "strategy_version", None),
        "feature_version": getattr(value, "feature_version", None),
        "config": _canonical_json_value(getattr(value, "config", None)),
    }


def _canonical_json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(
            Decimal(value.days) * Decimal("86400")
            + Decimal(value.seconds)
            + Decimal(value.microseconds) / Decimal("1000000")
        )
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    raise ContractValidationError(
        "Calibration configuration contains an unsupported value type: "
        f"{value.__class__.__module__}.{value.__class__.__qualname__}."
    )


def _sha256_json(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
