from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from collections.abc import Mapping
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Item, ManualOrderBookImport
from api.pricing.manual_order_book_price import CONTRACT_ID, CONTRACT_VERSION, CURRENCY_CODE
from api.pricing.manual_order_book_read_model import render_manual_order_book_price_read_model


STALE_AFTER_SECONDS = 7 * 24 * 60 * 60


class CurrentOrderBookNotFoundError(LookupError):
    pass


class CurrentOrderBookContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CurrentOrderBookData:
    item: Item
    source: ManualOrderBookImport
    read_model: dict[str, Any]
    freshness: Literal["fresh", "stale"]
    spread_display_text: str


@dataclass(frozen=True, slots=True)
class CurrentOrderBookInterpretation:
    read_model: dict[str, Any]
    freshness: Literal["fresh", "stale"]
    spread_display_text: str


async def get_current_order_book(
    session: AsyncSession,
    *,
    item_id: int,
    now: datetime | None = None,
) -> CurrentOrderBookData:
    item = await session.get(Item, item_id)
    if item is None:
        raise CurrentOrderBookNotFoundError("item_not_found")

    statement = (
        select(ManualOrderBookImport)
        .where(ManualOrderBookImport.item_id == item_id)
        .order_by(
            ManualOrderBookImport.captured_at.desc(),
            ManualOrderBookImport.id.desc(),
        )
        .limit(1)
    )
    source = await session.scalar(statement)
    if source is None:
        raise CurrentOrderBookNotFoundError("order_book_not_found")

    interpreted = interpret_current_order_book_capture(
        capture_payload=source.capture_payload,
        captured_at=source.captured_at,
        now=now,
    )
    return CurrentOrderBookData(
        item=item,
        source=source,
        read_model=interpreted.read_model,
        freshness=interpreted.freshness,
        spread_display_text=interpreted.spread_display_text,
    )


def interpret_current_order_book_capture(
    *,
    capture_payload: Mapping[str, Any],
    captured_at: datetime,
    now: datetime | None = None,
) -> CurrentOrderBookInterpretation:
    try:
        document = json.dumps(
            capture_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        rendered = render_manual_order_book_price_read_model(
            document,
            contract_id=CONTRACT_ID,
            contract_version=CONTRACT_VERSION,
            display_currency=CURRENCY_CODE,
        )
        read_model = json.loads(rendered)
        levels = read_model["levels"]
        buy_levels = [level for level in levels if level["side"] == "BUY"]
        sell_levels = [level for level in levels if level["side"] == "SELL"]
        best_buy = max(buy_levels, key=lambda level: level["price_raw"])
        best_sell = min(sell_levels, key=lambda level: level["price_raw"])
        spread = Decimal(best_sell["display_amount_gjn"]) - Decimal(
            best_buy["display_amount_gjn"]
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CurrentOrderBookContractError("price_contract_not_applicable") from exc

    observed_now = (now or datetime.now(UTC)).astimezone(UTC)
    normalized_captured_at = captured_at.astimezone(UTC)
    freshness: Literal["fresh", "stale"] = (
        "stale"
        if observed_now - normalized_captured_at > timedelta(seconds=STALE_AFTER_SECONDS)
        else "fresh"
    )
    return CurrentOrderBookInterpretation(
        read_model=read_model,
        freshness=freshness,
        spread_display_text=f"{spread:.2f}",
    )
