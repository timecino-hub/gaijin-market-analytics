"""database foundation

Revision ID: 20260627_0001
Revises:
Create Date: 2026-06-27 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260627_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="ck_import_jobs_checksum_sha256_hex"),
        sa.CheckConstraint(
            "invalid_row_count >= 0", name="ck_import_jobs_invalid_row_count_non_negative"
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_import_jobs_row_count_non_negative"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'duplicate')",
            name="ck_import_jobs_status_allowed",
        ),
        sa.CheckConstraint(
            "valid_row_count >= 0", name="ck_import_jobs_valid_row_count_non_negative"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_jobs_checksum", "import_jobs", ["checksum"], unique=False)

    op.create_table(
        "items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("external_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("rarity", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("external_key <> ''", name="ck_items_external_key_not_empty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_key"),
    )
    op.create_index("ix_items_category", "items", ["category"], unique=False)
    op.create_index("ix_items_external_key", "items", ["external_key"], unique=False)

    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("best_ask", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("best_bid", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("ask_count", sa.Integer(), nullable=True),
        sa.Column("bid_count", sa.Integer(), nullable=True),
        sa.Column("estimated_volume", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("source_import_job_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ask_count IS NULL OR ask_count >= 0", name="ck_market_snapshots_ask_count_non_negative"),
        sa.CheckConstraint("best_ask > 0", name="ck_market_snapshots_best_ask_positive"),
        sa.CheckConstraint("best_bid IS NULL OR best_bid >= 0", name="ck_market_snapshots_best_bid_non_negative"),
        sa.CheckConstraint("bid_count IS NULL OR bid_count >= 0", name="ck_market_snapshots_bid_count_non_negative"),
        sa.CheckConstraint(
            "estimated_volume IS NULL OR estimated_volume >= 0",
            name="ck_market_snapshots_estimated_volume_non_negative",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["source_import_job_id"], ["import_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "observed_at", name="uq_market_snapshots_item_observed_at"),
    )
    op.create_index("ix_market_snapshots_item_id", "market_snapshots", ["item_id"], unique=False)
    op.create_index("ix_market_snapshots_observed_at", "market_snapshots", ["observed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_market_snapshots_observed_at", table_name="market_snapshots")
    op.drop_index("ix_market_snapshots_item_id", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_items_external_key", table_name="items")
    op.drop_index("ix_items_category", table_name="items")
    op.drop_table("items")
    op.drop_index("ix_import_jobs_checksum", table_name="import_jobs")
    op.drop_table("import_jobs")
