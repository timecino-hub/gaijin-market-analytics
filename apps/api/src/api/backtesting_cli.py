from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from gaijin_market_analytics.backtesting import BacktestConfig
from gaijin_market_analytics.exceptions import AnalyticsError
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from sqlalchemy.exc import SQLAlchemyError

from api.analytics_registry import get_strategy_registry
from api.config import get_settings
from api.db.session import async_session_factory
from api.services.backtesting import (
    BacktestDataNotFoundError,
    BacktestStrategyUnavailableError,
    ItemBacktestService,
)
from api.services.items import ItemNotFoundError


EXIT_PARAMETER_ERROR = 2
EXIT_ITEM_NOT_FOUND = 3
EXIT_NO_DATA = 4
EXIT_DATABASE_UNAVAILABLE = 5
EXIT_STRATEGY_UNAVAILABLE = 6


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        config = BacktestConfig(
            strategy_name=args.strategy_name,
            strategy_version=args.strategy_version,
            lookback_horizon_days=args.lookback_horizon,
            forward_horizon_days=args.forward_horizon,
            start_at=args.start,
            end_at=args.end,
            cadence_days=args.cadence_days,
            require_complete_forward_window=True,
        )
    except AnalyticsError as exc:
        _exit(EXIT_PARAMETER_ERROR, f"Invalid backtest configuration: {exc}")

    try:
        payload = asyncio.run(_run(args.item_id, config))
    except ItemNotFoundError:
        _exit(EXIT_ITEM_NOT_FOUND, f"Item {args.item_id} was not found.")
    except BacktestDataNotFoundError:
        _exit(EXIT_NO_DATA, "No snapshots were found for the item in the requested range.")
    except BacktestStrategyUnavailableError:
        _exit(EXIT_STRATEGY_UNAVAILABLE, "The requested strategy is not available.")
    except SQLAlchemyError:
        _exit(EXIT_DATABASE_UNAVAILABLE, "Database is unavailable.")

    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=True,
        indent=2 if args.pretty else None,
        sort_keys=True,
        default=_json_default,
    )
    sys.stdout.write("\n")


async def _run(item_id: int, config: BacktestConfig) -> dict[str, Any]:
    settings = get_settings()
    registry = get_strategy_registry()
    async with async_session_factory() as session:
        service = ItemBacktestService(session, settings, registry)
        effective_config = service.config_with_runtime_analysis_settings(config)
        result = await service.backtest_item(item_id=item_id, config=effective_config)
    return {
        "metadata": {
            "item_id": item_id,
            "item": {
                "external_key": result.item.external_key,
                "name": result.item.name,
            },
            "strategy": {
                "name": effective_config.strategy_name,
                "version": effective_config.strategy_version,
            },
            "market_rules": {
                "name": GAIJIN_MARKET_RULES_V1.name,
                "version": GAIJIN_MARKET_RULES_V1.version,
                "maximum_listing_price": GAIJIN_MARKET_RULES_V1.maximum_listing_price,
                "maximum_sale_proceeds": GAIJIN_MARKET_RULES_V1.maximum_sale_proceeds,
                "currency_quantum": GAIJIN_MARKET_RULES_V1.currency_quantum,
            },
            "lookback_horizon_days": effective_config.lookback_horizon_days,
            "forward_horizon_days": effective_config.forward_horizon_days,
            "start_at": effective_config.start_at,
            "end_at": effective_config.end_at,
            "cadence_days": effective_config.cadence_days,
            "require_complete_forward_window": effective_config.require_complete_forward_window,
            "maximum_snapshot_age_hours": effective_config.maximum_snapshot_age_hours,
            "minimum_snapshot_count": effective_config.minimum_snapshot_count,
        },
        "summary": result.result.summary,
        "cases": result.result.cases,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a read-only walk-forward backtest for one item.")
    parser.add_argument("--item-id", required=True, type=int)
    parser.add_argument("--lookback-horizon", required=True, type=int)
    parser.add_argument("--forward-horizon", required=True, type=int)
    parser.add_argument("--start", required=True, type=_parse_datetime)
    parser.add_argument("--end", required=True, type=_parse_datetime)
    parser.add_argument("--cadence-days", required=True, type=int)
    parser.add_argument("--strategy-name", default="rule_based")
    parser.add_argument("--strategy-version", default="1.0.0")
    parser.add_argument("--pretty", action="store_true")
    return parser


def _parse_datetime(value: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("datetime must be ISO-8601 with timezone.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include Z or an explicit UTC offset.")
    return parsed.astimezone(UTC)


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def _exit(code: int, message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
