from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

MANUAL_MARKET_CONTEXT_VERSION = "1.0.0"
NEUTRAL_CONTEXT_INDEX = Decimal("1")


class ManualMarketContextError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManualMarketContextObservation:
    """Operator-supplied, point-in-time market context for one item.

    ``effective_at`` is when the operator considers the context to apply.
    ``known_at`` is when that judgement became available to the system. Keeping
    both timestamps prevents a later human annotation from leaking into an
    earlier walk-forward replay.

    Index semantics are deliberately dimensionless. ``1`` is neutral; values
    above/below one mean stronger/weaker relative context. They are never used
    as direct price multipliers by this module.
    """

    item_id: int
    effective_at: datetime
    known_at: datetime
    strength_index: Decimal = NEUTRAL_CONTEXT_INDEX
    attention_index: Decimal = NEUTRAL_CONTEXT_INDEX
    note: str | None = None

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise ManualMarketContextError("item_id must be positive.")
        object.__setattr__(self, "effective_at", _utc(self.effective_at, "effective_at"))
        object.__setattr__(self, "known_at", _utc(self.known_at, "known_at"))
        _validate_index(self.strength_index, "strength_index")
        _validate_index(self.attention_index, "attention_index")
        if self.note is not None and not isinstance(self.note, str):
            raise ManualMarketContextError("note must be a string or None.")


@dataclass(frozen=True, slots=True)
class ResolvedManualMarketContext:
    item_id: int
    effective_at: datetime
    knowledge_as_of: datetime
    strength_index: Decimal
    attention_index: Decimal
    source_effective_at: datetime | None
    source_known_at: datetime | None
    visible_observation_count: int
    defaulted: bool
    version: str = MANUAL_MARKET_CONTEXT_VERSION


@dataclass(frozen=True, slots=True)
class ContextComparability:
    strength_similarity: Decimal
    attention_similarity: Decimal
    combined_similarity: Decimal
    version: str = MANUAL_MARKET_CONTEXT_VERSION


def resolve_manual_market_context(
    *,
    item_id: int,
    effective_at: datetime,
    knowledge_as_of: datetime,
    observations: Iterable[ManualMarketContextObservation],
) -> ResolvedManualMarketContext:
    """Resolve the context that applies at ``effective_at`` using only known facts.

    A retroactive operator annotation is visible only after its ``known_at``.
    A future-effective annotation may be known in advance but does not apply
    before ``effective_at``. Revisions for the same effective timestamp are
    resolved by the latest visible ``known_at``.
    """

    if item_id <= 0:
        raise ManualMarketContextError("item_id must be positive.")
    effective_at = _utc(effective_at, "effective_at")
    knowledge_as_of = _utc(knowledge_as_of, "knowledge_as_of")
    if effective_at > knowledge_as_of:
        raise ManualMarketContextError("effective_at must not be later than knowledge_as_of.")

    values = tuple(observations)
    if any(value.item_id != item_id for value in values):
        raise ManualMarketContextError("manual context contains a different item_id.")

    identities: set[tuple[datetime, datetime]] = set()
    for value in values:
        identity = (value.effective_at, value.known_at)
        if identity in identities:
            raise ManualMarketContextError(
                "duplicate manual context effective_at/known_at identity."
            )
        identities.add(identity)

    visible = tuple(
        value
        for value in values
        if value.effective_at <= effective_at and value.known_at <= knowledge_as_of
    )
    selected = max(
        visible,
        key=lambda value: (value.effective_at, value.known_at),
        default=None,
    )
    if selected is None:
        return ResolvedManualMarketContext(
            item_id=item_id,
            effective_at=effective_at,
            knowledge_as_of=knowledge_as_of,
            strength_index=NEUTRAL_CONTEXT_INDEX,
            attention_index=NEUTRAL_CONTEXT_INDEX,
            source_effective_at=None,
            source_known_at=None,
            visible_observation_count=0,
            defaulted=True,
        )
    return ResolvedManualMarketContext(
        item_id=item_id,
        effective_at=effective_at,
        knowledge_as_of=knowledge_as_of,
        strength_index=selected.strength_index,
        attention_index=selected.attention_index,
        source_effective_at=selected.effective_at,
        source_known_at=selected.known_at,
        visible_observation_count=len(visible),
        defaulted=False,
    )


def context_comparability(
    current: ResolvedManualMarketContext,
    historical: ResolvedManualMarketContext,
) -> ContextComparability:
    """Return a symmetric, unit-free context similarity in ``(0, 1]``.

    Each dimension uses ``min(index) / max(index)``. The combined value is the
    product of strength and attention similarities. This changes historical
    *comparability*, never the quoted or historical market price itself.
    """

    if current.item_id != historical.item_id:
        raise ManualMarketContextError("contexts must refer to the same item_id.")
    strength = _ratio_similarity(current.strength_index, historical.strength_index)
    attention = _ratio_similarity(current.attention_index, historical.attention_index)
    return ContextComparability(
        strength_similarity=strength,
        attention_similarity=attention,
        combined_similarity=strength * attention,
    )


def weighted_decimal_median(
    values: Iterable[tuple[Decimal, Decimal]],
) -> Decimal | None:
    """Deterministic weighted median with midpoint behaviour on exact half ties.

    Weights must be positive Decimal values. An exact cumulative half-weight tie
    returns the average of the current and next value, so equal weights reproduce
    the ordinary median for even-sized inputs.
    """

    points = tuple(values)
    if not points:
        return None
    for value, weight in points:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ManualMarketContextError("weighted median values must be finite Decimals.")
        if (
            not isinstance(weight, Decimal)
            or not weight.is_finite()
            or weight <= Decimal("0")
        ):
            raise ManualMarketContextError("weighted median weights must be positive Decimals.")

    ordered = tuple(sorted(points, key=lambda pair: pair[0]))
    total = sum((weight for _, weight in ordered), Decimal("0"))
    half = total / Decimal("2")
    cumulative = Decimal("0")
    for index, (value, weight) in enumerate(ordered):
        cumulative += weight
        if cumulative > half:
            return value
        if cumulative == half:
            if index + 1 < len(ordered):
                return (value + ordered[index + 1][0]) / Decimal("2")
            return value
    return ordered[-1][0]


def _ratio_similarity(left: Decimal, right: Decimal) -> Decimal:
    return min(left, right) / max(left, right)


def _validate_index(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise ManualMarketContextError(f"{field_name} must be a positive finite Decimal.")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManualMarketContextError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
