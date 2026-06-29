from collections.abc import Callable
from datetime import UTC, datetime

UtcClock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_utc_clock() -> UtcClock:
    return utc_now
