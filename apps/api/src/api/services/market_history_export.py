from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from gaijin_market_analytics.datasets.market_history_export import (
    MarketHistoryExportProfile,
    MarketHistoryExportValidationError,
    MarketHistoryQualityTier,
    MarketHistorySourceRecord,
    build_market_history_export,
)
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Item, MarketSnapshot, OrderBookObservation, ScreenReviewImport
from api.schemas.local_recognition import (
    ObservedAtSource,
    ReviewSourceMetadata,
    ReviewedCandidate,
)
from api.services.local_recognition_candidate import candidate_payload_sha256


class MarketHistoryExportError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class MarketHistoryDatabaseExportResult:
    payload: dict[str, Any]
    database_schema_revision: str
    read_only_confirmed: bool


async def export_market_history_from_database(
    session: AsyncSession,
    *,
    export_profile: MarketHistoryExportProfile | str = (
        MarketHistoryExportProfile.POINT_IN_TIME_PRIMARY
    ),
    item_ids: Iterable[int] | None = None,
    item_mapping_fingerprint: str | None = None,
    allow_exclusions: bool = False,
) -> MarketHistoryDatabaseExportResult:
    normalized_item_ids = _normalize_item_ids(item_ids)
    if session.in_transaction():
        raise MarketHistoryExportError(
            "export_session_transaction_active",
            "Market history export requires a fresh database session.",
        )

    async with session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        read_only_value = await session.scalar(text("SHOW transaction_read_only"))
        if str(read_only_value).lower() != "on":
            raise MarketHistoryExportError(
                "export_read_only_not_confirmed",
                "The PostgreSQL transaction is not read-only.",
            )
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if not isinstance(revision, str) or not revision.strip():
            raise MarketHistoryExportError(
                "export_schema_revision_unavailable",
                "The database schema revision could not be determined.",
            )

        statement = (
            select(ScreenReviewImport, MarketSnapshot, OrderBookObservation, Item)
            .outerjoin(
                MarketSnapshot,
                MarketSnapshot.id == ScreenReviewImport.market_snapshot_id,
            )
            .outerjoin(
                OrderBookObservation,
                OrderBookObservation.screen_review_import_id == ScreenReviewImport.id,
            )
            .outerjoin(Item, Item.id == ScreenReviewImport.item_id)
            .order_by(
                ScreenReviewImport.item_id.asc(),
                MarketSnapshot.observed_at.asc().nulls_last(),
                ScreenReviewImport.review_id.asc(),
            )
        )
        if normalized_item_ids:
            statement = statement.where(ScreenReviewImport.item_id.in_(normalized_item_ids))
        rows = list((await session.execute(statement)).all())

        orphan_statement = (
            select(func.count())
            .select_from(OrderBookObservation)
            .outerjoin(
                ScreenReviewImport,
                ScreenReviewImport.id
                == OrderBookObservation.screen_review_import_id,
            )
            .outerjoin(
                MarketSnapshot,
                MarketSnapshot.id == OrderBookObservation.market_snapshot_id,
            )
            .where(or_(ScreenReviewImport.id.is_(None), MarketSnapshot.id.is_(None)))
        )
        if normalized_item_ids:
            orphan_statement = orphan_statement.where(
                or_(
                    ScreenReviewImport.item_id.in_(normalized_item_ids),
                    MarketSnapshot.item_id.in_(normalized_item_ids),
                )
            )
        orphan_count = int(await session.scalar(orphan_statement) or 0)

        records: list[MarketHistorySourceRecord] = []
        exclusions: dict[str, int] = {}
        conflict_count = 0
        if orphan_count:
            if not allow_exclusions:
                raise MarketHistoryExportError(
                    "export_orphan_order_book_observation",
                    "An order-book observation is missing its review or snapshot relation.",
                )
            exclusions["export_orphan_order_book_observation"] = orphan_count
            conflict_count += orphan_count

        for imported, snapshot, observation, item in rows:
            try:
                records.append(
                    source_record_from_models(
                        imported=imported,
                        snapshot=snapshot,
                        observation=observation,
                        item=item,
                    )
                )
            except MarketHistoryExportError as exc:
                if not allow_exclusions:
                    raise
                exclusions[exc.code] = exclusions.get(exc.code, 0) + 1
                conflict_count += 1

        payload = build_market_history_export(
            records,
            database_schema_revision=revision,
            export_profile=export_profile,
            item_mapping_fingerprint=item_mapping_fingerprint,
            source_record_count=len(rows) + orphan_count,
            exclusion_reason_counts=exclusions,
            conflict_count=conflict_count,
        )

    return MarketHistoryDatabaseExportResult(
        payload=payload,
        database_schema_revision=revision,
        read_only_confirmed=True,
    )


def source_record_from_models(
    *,
    imported: ScreenReviewImport | None,
    snapshot: MarketSnapshot | None,
    observation: OrderBookObservation | None,
    item: Item | None,
) -> MarketHistorySourceRecord:
    if imported is None:
        _fail("export_review_import_missing")
    if snapshot is None:
        _fail("export_snapshot_missing")
    if observation is None:
        _fail("export_order_book_observation_missing")
    if item is None:
        _fail("export_item_missing")

    if imported.market_snapshot_id != snapshot.id:
        _fail("export_review_snapshot_relation_mismatch")
    if imported.item_id != snapshot.item_id or imported.item_id != item.id:
        _fail("export_item_relation_mismatch")
    if observation.screen_review_import_id != imported.id:
        _fail("export_observation_review_relation_mismatch")
    if observation.market_snapshot_id != snapshot.id:
        _fail("export_observation_snapshot_relation_mismatch")
    if observation.observed_bid_quantity != imported.total_bid_quantity:
        _fail("export_bid_quantity_mismatch")
    if observation.observed_ask_quantity != imported.total_ask_quantity:
        _fail("export_ask_quantity_mismatch")
    if observation.quantity_semantics != "screenshot_display_quantity":
        _fail("export_quantity_semantics_invalid")
    if observation.source_type != "screen_review":
        _fail("export_source_type_invalid")
    if observation.source_version != imported.candidate_version:
        _fail("export_source_version_mismatch")
    if observation.review_status != imported.review_status:
        _fail("export_review_status_mismatch")
    if imported.review_status not in {"confirmed", "confirmed_with_edits"}:
        _fail("export_review_status_invalid")
    if snapshot.ask_count is not None or snapshot.bid_count is not None:
        _fail("export_screen_review_legacy_count_present")
    if snapshot.estimated_volume is not None:
        _fail("export_screen_review_estimated_volume_present")
    if snapshot.source_import_job_id is not None:
        _fail("export_screen_review_import_job_present")
    observed_at = _aware_utc(snapshot.observed_at, "snapshot_observed_at")
    imported_at = _aware_utc(imported.imported_at, "review_imported_at")

    _validate_raw_candidate_audit_payload(imported.candidate_payload)
    candidate = _strict_model_validate(
        ReviewedCandidate,
        imported.candidate_payload,
        "export_candidate_payload_invalid",
    )
    source_metadata = _strict_model_validate(
        ReviewSourceMetadata,
        imported.source_metadata,
        "export_source_metadata_invalid",
    )
    recalculated_hash = candidate_payload_sha256(imported.candidate_payload)
    if recalculated_hash != imported.candidate_sha256:
        _fail("export_candidate_hash_mismatch")
    _validate_candidate_audit_state(candidate)
    if candidate.review_id != imported.review_id:
        _fail("export_candidate_review_id_mismatch")
    if candidate.candidate_version != imported.candidate_version:
        _fail("export_candidate_version_mismatch")
    if candidate.status != imported.review_status:
        _fail("export_candidate_status_mismatch")
    if _aware_utc(candidate.observed_at, "candidate_observed_at") != observed_at:
        _fail("export_candidate_observed_at_mismatch")
    if candidate.best_bid != snapshot.best_bid:
        _fail("export_candidate_best_bid_mismatch")
    if candidate.best_ask != snapshot.best_ask:
        _fail("export_candidate_best_ask_mismatch")
    if candidate.total_bid_quantity != imported.total_bid_quantity:
        _fail("export_candidate_bid_quantity_mismatch")
    if candidate.total_ask_quantity != imported.total_ask_quantity:
        _fail("export_candidate_ask_quantity_mismatch")
    if candidate.item_identity.item_id is not None and candidate.item_identity.item_id != item.id:
        _fail("export_candidate_item_id_mismatch")
    if candidate.item_identity.item_key != item.external_key:
        _fail("export_candidate_item_key_mismatch")
    if candidate.item_identity.item_name != item.name:
        _fail("export_candidate_item_name_mismatch")
    has_incomplete_quantities = (
        imported.total_bid_quantity is None or imported.total_ask_quantity is None
    )
    if has_incomplete_quantities and not candidate.acknowledge_incomplete_quantities:
        _fail("export_incomplete_quantities_not_acknowledged")

    quality_tier = classify_quality_tier(
        source_metadata=source_metadata,
        candidate=candidate,
    )
    if quality_tier is MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE:
        _fail("export_quality_tier_unknown")

    page_identity = (
        source_metadata.page_identity.model_dump(mode="json")
        if source_metadata.page_identity is not None
        else None
    )
    page_item_match = None
    if source_metadata.page_identity is not None:
        page_item_key = source_metadata.page_identity.item_key
        page_item_match = (
            None if page_item_key is None else page_item_key == item.external_key
        )

    try:
        return MarketHistorySourceRecord(
            item_id=item.id,
            item_external_key=item.external_key,
            item_name=item.name,
            item_category=item.category,
            item_rarity=item.rarity,
            item_is_active=item.is_active,
            observed_at=observed_at,
            best_bid=_decimal_or_none(snapshot.best_bid),
            best_ask=_decimal_required(snapshot.best_ask, "snapshot_best_ask"),
            bid_count=snapshot.bid_count,
            ask_count=snapshot.ask_count,
            estimated_volume=_decimal_or_none(snapshot.estimated_volume),
            observed_bid_quantity=observation.observed_bid_quantity,
            observed_ask_quantity=observation.observed_ask_quantity,
            quantity_semantics=observation.quantity_semantics,
            source_type=observation.source_type,
            source_version=observation.source_version,
            review_status=observation.review_status,
            source_review_id=imported.review_id,
            candidate_version=imported.candidate_version,
            candidate_sha256=imported.candidate_sha256,
            quality_tier=quality_tier,
            observed_at_source=candidate.observed_at_source.value,
            incomplete_quantities_acknowledged=(
                candidate.acknowledge_incomplete_quantities
            ),
            edited_fields=tuple(sorted(set(candidate.recognition.edited_fields))),
            capture_schema_version=source_metadata.capture_schema_version,
            client_capture_id=source_metadata.client_capture_id,
            capture_started_at=source_metadata.capture_started_at,
            captured_at=source_metadata.captured_at,
            page_identity=page_identity,
            capture_sha256=source_metadata.capture_sha256,
            extension_version=source_metadata.extension_version,
            imported_at=imported_at,
            page_item_key_matches_database_item=page_item_match,
        )
    except MarketHistoryExportValidationError as exc:
        raise MarketHistoryExportError(exc.code, str(exc)) from exc


def classify_quality_tier(
    *,
    source_metadata: ReviewSourceMetadata,
    candidate: ReviewedCandidate,
) -> MarketHistoryQualityTier:
    if source_metadata.capture_schema_version == "point_in_time_capture_v1":
        if not _valid_point_in_time_metadata(source_metadata):
            return MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE
        edited_fields = set(candidate.recognition.edited_fields)
        if candidate.observed_at_source is ObservedAtSource.BROWSER_CAPTURE:
            if "observed_at" in edited_fields:
                return MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE
            if _aware_utc(candidate.observed_at, "candidate_observed_at") != _aware_utc(
                source_metadata.captured_at, "captured_at"
            ):
                return MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE
            return MarketHistoryQualityTier.BROWSER_CAPTURED
        if candidate.observed_at_source is ObservedAtSource.USER_EDITED:
            if "observed_at" not in edited_fields:
                return MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE
            return MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED
        return MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE

    if (
        source_metadata.capture_schema_version == "legacy_v1"
        and source_metadata.observation_time_semantics == "legacy_server_time"
        and candidate.observed_at_source is ObservedAtSource.REVIEW_CREATED_DEFAULT
        and source_metadata.client_capture_id is None
        and source_metadata.capture_started_at is None
        and source_metadata.captured_at is None
        and source_metadata.page_identity is None
    ):
        return MarketHistoryQualityTier.LEGACY_SERVER_TIME
    return MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE


def _validate_raw_candidate_audit_payload(payload: object) -> None:
    if not isinstance(payload, Mapping):
        _fail("export_candidate_payload_invalid")
    for field in ("imported", "database_written", "market_snapshot_created"):
        if field in payload and payload[field] is not False:
            _fail("export_candidate_audit_state_invalid")
    for field in (
        "database_item_id",
        "screen_review_import_id",
        "market_snapshot_id",
        "order_book_observation_id",
        "imported_at",
    ):
        if field in payload and payload[field] is not None:
            _fail("export_candidate_audit_state_invalid")


def _validate_candidate_audit_state(candidate: ReviewedCandidate) -> None:
    if (
        candidate.imported
        or candidate.database_written
        or candidate.market_snapshot_created
        or candidate.database_item_id is not None
        or candidate.screen_review_import_id is not None
        or candidate.market_snapshot_id is not None
        or candidate.order_book_observation_id is not None
        or candidate.imported_at is not None
    ):
        _fail("export_candidate_audit_state_invalid")


def _valid_point_in_time_metadata(source: ReviewSourceMetadata) -> bool:
    if (
        source.source != "browser_extension"
        or source.observation_time_semantics != "browser_captured_at"
        or source.client_capture_id is None
        or source.capture_started_at is None
        or source.captured_at is None
        or source.page_identity is None
        or source.capture_sha256 is None
        or source.source_url_safe is None
        or source.capture_duration_ms is None
    ):
        return False
    try:
        capture_id = uuid.UUID(source.client_capture_id)
        started = _aware_utc(source.capture_started_at, "capture_started_at")
        captured = _aware_utc(source.captured_at, "captured_at")
    except (ValueError, MarketHistoryExportError):
        return False
    if capture_id.version != 4 or started > captured:
        return False
    duration_ms = int((captured - started).total_seconds() * 1000)
    if duration_ms < 0 or duration_ms > 30_000 or source.capture_duration_ms != duration_ms:
        return False
    if len(source.capture_sha256) != 64:
        return False
    try:
        int(source.capture_sha256, 16)
    except ValueError:
        return False
    identity = source.page_identity
    canonical_url = f"{identity.origin}{identity.market_path}"
    if source.source_url_safe != canonical_url:
        return False
    if (
        not identity.market_path.startswith("/market/1067/")
        or "?" in identity.market_path
        or "#" in identity.market_path
    ):
        return False
    return True


def _strict_model_validate(
    model: type[BaseModel],
    payload: object,
    code: str,
) -> Any:
    if not isinstance(payload, Mapping):
        _fail(code)
    unexpected = set(payload) - set(model.model_fields)
    if unexpected:
        _fail(code)
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise MarketHistoryExportError(code, "Persisted review metadata is invalid.") from exc


def _normalize_item_ids(item_ids: Iterable[int] | None) -> tuple[int, ...]:
    if item_ids is None:
        return ()
    normalized = tuple(sorted(set(item_ids)))
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in normalized
    ):
        _fail("export_item_id_invalid")
    return normalized


def _aware_utc(value: datetime | None, field: str) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        _fail(f"export_{field}_timezone_required")
    return value.astimezone(UTC)


def _decimal_required(value: Any, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        _fail(f"export_{field}_invalid")
    return value


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _decimal_required(value, "decimal")


def _fail(code: str, message: str | None = None) -> None:
    raise MarketHistoryExportError(code, message)
