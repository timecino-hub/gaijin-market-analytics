"""historical trade imports and buckets

Revision ID: 20260711_0004
Revises: 20260710_0003
Create Date: 2026-07-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260711_0004"
down_revision: Union[str, Sequence[str], None] = "20260710_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "historical_trade_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("pairing_id", sa.String(length=64), nullable=False),
        sa.Column("source_url_safe", sa.String(length=2048), nullable=False),
        sa.Column("source_schema_version", sa.String(length=64), nullable=False),
        sa.Column("extension_version", sa.String(length=64), nullable=True),
        sa.Column("source_captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_series_sha256", sa.String(length=64), nullable=False),
        sa.Column("point_count_1h", sa.Integer(), nullable=False),
        sa.Column("point_count_1d", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("overlap_day_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("overlap_mismatch_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_series_sha256 ~ '^[0-9a-f]{64}$'", name="ck_historical_trade_imports_sha256"),
        sa.CheckConstraint("source_schema_version <> ''", name="ck_historical_trade_imports_schema_version"),
        sa.CheckConstraint("point_count_1h >= 0", name="ck_historical_trade_imports_1h_count_non_negative"),
        sa.CheckConstraint("point_count_1d >= 0", name="ck_historical_trade_imports_1d_count_non_negative"),
        sa.CheckConstraint("inserted_count >= 0", name="ck_historical_trade_imports_inserted_non_negative"),
        sa.CheckConstraint("updated_count >= 0", name="ck_historical_trade_imports_updated_non_negative"),
        sa.CheckConstraint("unchanged_count >= 0", name="ck_historical_trade_imports_unchanged_non_negative"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "source_series_sha256", name="uq_historical_trade_imports_item_hash"),
    )
    op.create_index("ix_historical_trade_imports_item_id", "historical_trade_imports", ["item_id"])
    op.create_index("ix_historical_trade_imports_imported_at", "historical_trade_imports", ["imported_at"])

    op.create_table(
        "historical_trade_buckets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("source_import_id", sa.BigInteger(), nullable=False),
        sa.Column("granularity", sa.String(length=2), nullable=False),
        sa.Column("bucket_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("vwap_price_raw", sa.BigInteger(), nullable=False),
        sa.Column("price_scale", sa.Integer(), server_default="10000", nullable=False),
        sa.Column("reported_vwap_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("reported_trade_volume", sa.Integer(), nullable=False),
        sa.Column("price_semantics", sa.String(), nullable=False),
        sa.Column("volume_semantics", sa.String(), nullable=False),
        sa.Column("source_schema_version", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("granularity IN ('1h', '1d')", name="ck_historical_trade_buckets_granularity"),
        sa.CheckConstraint("bucket_duration_seconds IN (3600, 86400)", name="ck_historical_trade_buckets_duration"),
        sa.CheckConstraint("vwap_price_raw > 0", name="ck_historical_trade_buckets_price_raw_positive"),
        sa.CheckConstraint("price_scale = 10000", name="ck_historical_trade_buckets_price_scale"),
        sa.CheckConstraint("reported_vwap_price > 0", name="ck_historical_trade_buckets_price_positive"),
        sa.CheckConstraint("reported_trade_volume > 0", name="ck_historical_trade_buckets_volume_positive"),
        sa.CheckConstraint(
            "price_semantics = 'bucket_volume_weighted_average_trade_price'",
            name="ck_historical_trade_buckets_price_semantics",
        ),
        sa.CheckConstraint(
            "volume_semantics = 'reported_trade_volume_unknown_unit'",
            name="ck_historical_trade_buckets_volume_semantics",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["source_import_id"], ["historical_trade_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id", "granularity", "bucket_start_utc",
            name="uq_historical_trade_buckets_item_granularity_start",
        ),
    )
    op.create_index(
        "ix_historical_trade_buckets_item_start",
        "historical_trade_buckets",
        ["item_id", "bucket_start_utc"],
    )
    op.create_index(
        "ix_historical_trade_buckets_granularity_start",
        "historical_trade_buckets",
        ["granularity", "bucket_start_utc"],
    )


def downgrade() -> None:
    op.drop_index("ix_historical_trade_buckets_granularity_start", table_name="historical_trade_buckets")
    op.drop_index("ix_historical_trade_buckets_item_start", table_name="historical_trade_buckets")
    op.drop_table("historical_trade_buckets")
    op.drop_index("ix_historical_trade_imports_imported_at", table_name="historical_trade_imports")
    op.drop_index("ix_historical_trade_imports_item_id", table_name="historical_trade_imports")
    op.drop_table("historical_trade_imports")
