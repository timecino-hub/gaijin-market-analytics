from decimal import Decimal

from sqlalchemy import DateTime, Numeric

from api.db.models import ImportJob, Item, MarketSnapshot


def test_item_model_columns_and_constraints() -> None:
    table = Item.__table__

    assert table.c.id.primary_key
    assert table.c.external_key.unique
    assert not table.c.external_key.nullable
    assert not table.c.name.nullable
    assert not table.c.category.nullable
    assert not table.c.created_at.nullable
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone

    constraint_names = {constraint.name for constraint in table.constraints}
    assert "ck_items_external_key_not_empty" in constraint_names


def test_import_job_model_columns_and_constraints() -> None:
    table = ImportJob.__table__

    assert table.c.checksum.type.length == 64
    assert not table.c.status.nullable
    assert not table.c.started_at.nullable
    assert table.c.error_report.nullable

    constraint_names = {constraint.name for constraint in table.constraints}
    assert "ck_import_jobs_checksum_sha256_hex" in constraint_names
    assert "ck_import_jobs_status_allowed" in constraint_names
    assert "ck_import_jobs_row_count_non_negative" in constraint_names
    assert "ck_import_jobs_valid_row_count_non_negative" in constraint_names
    assert "ck_import_jobs_invalid_row_count_non_negative" in constraint_names


def test_market_snapshot_model_uses_decimal_numeric_and_nullable_estimated_volume() -> None:
    table = MarketSnapshot.__table__

    assert isinstance(table.c.best_ask.type, Numeric)
    assert table.c.best_ask.type.precision == 18
    assert table.c.best_ask.type.scale == 6
    assert MarketSnapshot(best_ask=Decimal("1.230000")).best_ask == Decimal("1.230000")
    assert table.c.estimated_volume.nullable
    assert table.c.observed_at.type.timezone

    constraint_names = {constraint.name for constraint in table.constraints}
    assert "uq_market_snapshots_item_observed_at" in constraint_names
    assert "ck_market_snapshots_best_ask_positive" in constraint_names
    assert "ck_market_snapshots_best_bid_non_negative" in constraint_names
    assert "ck_market_snapshots_estimated_volume_non_negative" in constraint_names
