from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.datasets.market_history_export import (
    market_history_export_json_bytes,
)
from sqlalchemy import create_engine, text

from api.db.session import async_session_factory
from api.schemas.local_recognition import (
    CandidateRecognition,
    ItemIdentity,
    ObservedAtSource,
    ReviewSourceMetadata,
    ReviewedCandidate,
    SafePageIdentity,
)
from api.services.local_recognition_candidate import (
    candidate_audit_payload,
    candidate_payload_sha256,
)
from api.services.market_history_export import export_market_history_from_database


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _candidate() -> ReviewedCandidate:
    return ReviewedCandidate(
        review_id="export-review",
        observed_at=BASE,
        observed_at_source=ObservedAtSource.BROWSER_CAPTURE,
        item_identity=ItemIdentity(
            item_id=None,
            item_key="export-item",
            item_name="Export Item",
        ),
        best_bid=Decimal("12.34"),
        best_ask=Decimal("13.00"),
        total_bid_quantity=5,
        total_ask_quantity=7,
        recognition=CandidateRecognition(
            layout_name="market",
            layout_version="1",
            config_sha256="3" * 64,
            ocr_backend="windows-ocr",
            edited_fields=[],
            parser_version="1",
            runner_version="1",
        ),
        status="confirmed",
    )


def _metadata() -> ReviewSourceMetadata:
    return ReviewSourceMetadata(
        source="browser_extension",
        extension_version="1.0.0",
        source_url_safe="https://trade.gaijin.net/market/1067/export-item",
        source_tab_title="Export Item",
        capture_sha256="2" * 64,
        pairing_id="test-pairing",
        capture_schema_version="point_in_time_capture_v1",
        client_capture_id="00000000-0000-4000-8000-000000000001",
        capture_started_at=BASE,
        captured_at=BASE,
        capture_duration_ms=0,
        page_identity=SafePageIdentity(
            origin="https://trade.gaijin.net",
            market_path="/market/1067/export-item",
            item_key="export-item",
        ),
        observation_time_semantics="browser_captured_at",
    )


def _seed(database_url: str, *, internal_offset: int = 0, imported_at: datetime = BASE) -> None:
    reviewed = _candidate()
    payload = candidate_audit_payload(reviewed)
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            item_id = int(
                conn.execute(
                    text(
                        """
                        INSERT INTO items (id, external_key, name, category, is_active)
                        VALUES (900001, 'export-item', 'Export Item', 'vehicle', true)
                        ON CONFLICT (id) DO NOTHING
                        RETURNING id
                        """
                    )
                ).scalar_one_or_none()
                or 900001
            )
            snapshot_id = 910000 + internal_offset
            import_id = 920000 + internal_offset
            observation_id = 930000 + internal_offset
            conn.execute(
                text(
                    """
                    INSERT INTO market_snapshots (
                        id, item_id, observed_at, best_ask, best_bid,
                        ask_count, bid_count, estimated_volume, source_import_job_id
                    ) VALUES (
                        :id, :item_id, :observed_at, 13.000000, 12.340000,
                        NULL, NULL, NULL, NULL
                    )
                    """
                ),
                {"id": snapshot_id, "item_id": item_id, "observed_at": BASE},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO screen_review_imports (
                        id, review_id, item_id, market_snapshot_id, review_status,
                        candidate_version, candidate_sha256, total_bid_quantity,
                        total_ask_quantity, candidate_payload, source_metadata,
                        reviewer_note, imported_at
                    ) VALUES (
                        :id, 'export-review', :item_id, :snapshot_id, 'confirmed',
                        'screen_review_candidate_v1', :candidate_sha256, 5, 7,
                        CAST(:candidate_payload AS jsonb), CAST(:source_metadata AS jsonb),
                        NULL, :imported_at
                    )
                    """
                ),
                {
                    "id": import_id,
                    "item_id": item_id,
                    "snapshot_id": snapshot_id,
                    "candidate_sha256": candidate_payload_sha256(payload),
                    "candidate_payload": json.dumps(payload),
                    "source_metadata": json.dumps(_metadata().model_dump(mode="json")),
                    "imported_at": imported_at,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO order_book_observations (
                        id, market_snapshot_id, screen_review_import_id,
                        observed_bid_quantity, observed_ask_quantity,
                        quantity_semantics, source_type, source_version,
                        review_status, created_at
                    ) VALUES (
                        :id, :snapshot_id, :import_id, 5, 7,
                        'screenshot_display_quantity', 'screen_review',
                        'screen_review_candidate_v1', 'confirmed', :created_at
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "snapshot_id": snapshot_id,
                    "import_id": import_id,
                    "created_at": imported_at,
                },
            )
    finally:
        engine.dispose()


def _delete_export_rows(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    DELETE FROM order_book_observations
                    WHERE screen_review_import_id IN (
                        SELECT id FROM screen_review_imports WHERE review_id = 'export-review'
                    )
                    """
                )
            )
            conn.execute(
                text("DELETE FROM screen_review_imports WHERE review_id = 'export-review'")
            )
            conn.execute(
                text(
                    """
                    DELETE FROM market_snapshots
                    WHERE item_id = 900001 AND observed_at = :observed_at
                    """
                ),
                {"observed_at": BASE},
            )
    finally:
        engine.dispose()


def _counts(database_url: str) -> tuple[int, int, int]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return tuple(
                int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
                for table in (
                    "market_snapshots",
                    "screen_review_imports",
                    "order_book_observations",
                )
            )
    finally:
        engine.dispose()


async def _export():
    async with async_session_factory() as session:
        return await export_market_history_from_database(session, item_ids=[900001])


def test_database_export_is_read_only_and_byte_deterministic(
    migrated_database: str,
) -> None:
    _delete_export_rows(migrated_database)
    _seed(migrated_database)
    try:
        before = _counts(migrated_database)

        first = asyncio.run(_export())
        second = asyncio.run(_export())

        assert first.read_only_confirmed is True
        assert market_history_export_json_bytes(
            first.payload
        ) == market_history_export_json_bytes(second.payload)
        assert _counts(migrated_database) == before
    finally:
        _delete_export_rows(migrated_database)


def test_internal_review_snapshot_and_observation_ids_do_not_affect_fingerprints(
    migrated_database: str,
) -> None:
    _delete_export_rows(migrated_database)
    try:
        _seed(migrated_database, internal_offset=1, imported_at=BASE)
        first = asyncio.run(_export()).payload
        _delete_export_rows(migrated_database)
        _seed(
            migrated_database,
            internal_offset=100,
            imported_at=BASE + timedelta(days=1),
        )
        second = asyncio.run(_export()).payload

        assert first["source_fingerprint"] == second["source_fingerprint"]
        assert first["typed_history_fingerprint"] == second["typed_history_fingerprint"]
    finally:
        _delete_export_rows(migrated_database)
