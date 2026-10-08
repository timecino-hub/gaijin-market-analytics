"""Bounded copies of exact analytical requests, invalidated by import revisions."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from threading import Lock
from time import monotonic
from typing import Annotated, Generic, TypeVar

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import HistoricalTradeImport, Item
from api.db.session import get_session

_Value = TypeVar("_Value")


@dataclass(frozen=True, slots=True)
class ReadingRevision:
    # Keeping the actual bind object prevents cross-database reuse and id reuse.
    database_bind: object
    latest_import_id: int | None
    latest_imported_at: datetime | None
    item_count: int
    latest_item_updated_at: datetime | None


async def get_reading_revision(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReadingRevision:
    """Check the normal import audit and catalog on every request.

    A new import can correct an old price without changing row count or the
    latest market date. Its audit id/time therefore invalidates the reading.
    Unsupported direct SQL changes are bounded by the cache's expiry instead.
    """

    statement = select(
        select(func.max(HistoricalTradeImport.id)).scalar_subquery(),
        select(func.max(HistoricalTradeImport.imported_at)).scalar_subquery(),
        select(func.count(Item.id)).scalar_subquery(),
        select(func.max(Item.updated_at)).scalar_subquery(),
    )
    row = (await session.execute(statement)).one()
    bind = session.get_bind()
    return ReadingRevision(
        database_bind=bind.engine,
        latest_import_id=row[0],
        latest_imported_at=row[1],
        item_count=row[2],
        latest_item_updated_at=row[3],
    )


def configuration_key(config: object) -> tuple:
    """Preserve Decimal representation as well as the numeric configuration.

    For example, the budget response retains ``100.00`` when requested, so it
    must not borrow a cached response containing the equal Decimal ``100``.
    """

    return tuple(
        (field.name, _configuration_value(getattr(config, field.name)))
        for field in fields(config)
    )


def _configuration_value(value: Hashable) -> Hashable:
    if isinstance(value, Decimal):
        return (Decimal, value.as_tuple())
    if isinstance(value, tuple):
        return tuple(_configuration_value(member) for member in value)
    return (type(value), value)


class BoundedReadingCache(Generic[_Value]):
    """An LRU per namespace, with exact keys, expiry and concurrent reuse.

    Every caller receives a deep copy, including the caller which calculated
    the reading. Pending work uses a thread-safe future so the cache can also
    be exercised by separate application event loops without sharing sessions.
    """

    def __init__(
        self,
        *,
        maximum_entries: int = 8,
        ttl_seconds: float = 3600,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if maximum_entries < 1 or ttl_seconds <= 0:
            raise ValueError("Cache capacity and expiry must be positive.")
        self._maximum_entries = maximum_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, _Value]] = OrderedDict()
        self._pending: dict[Hashable, Future[_Value]] = {}
        self._lock = Lock()

    async def get_or_compute(
        self, key: Hashable, compute: Callable[[], Awaitable[_Value]]
    ) -> _Value:
        with self._lock:
            now = self._clock()
            expired = [
                entry_key
                for entry_key, (expiry, _) in self._entries.items()
                if expiry <= now
            ]
            for entry_key in expired:
                del self._entries[entry_key]
            if key in self._entries:
                self._entries.move_to_end(key)
                return deepcopy(self._entries[key][1])
            pending = self._pending.get(key)
            owner = pending is None
            if pending is None:
                pending = Future()
                self._pending[key] = pending

        if not owner:
            # Cancellation of a waiting HTTP request does not cancel the work
            # needed by other requests with the same exact key.
            return deepcopy(await asyncio.shield(asyncio.wrap_future(pending)))

        try:
            value = deepcopy(await compute())
        except BaseException as error:
            with self._lock:
                del self._pending[key]
            pending.set_exception(error)
            raise

        with self._lock:
            self._entries[key] = (self._clock() + self._ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._maximum_entries:
                self._entries.popitem(last=False)
            del self._pending[key]
        pending.set_result(value)
        return deepcopy(value)
