"""manual order book imports

Revision ID: 20260716_0005
Revises: 20260711_0004
Create Date: 2026-07-16 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260716_0005"
down_revision: Union[str, Sequence[str], None] = "20260711_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manual_order_book_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_job_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("capture_id", sa.String(length=36), nullable=False),
        sa.Column("source_schema_version", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("capture_method", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_response_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_capture_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_offset_minutes", sa.Integer(), nullable=False),
        sa.Column("page_origin", sa.String(length=255), nullable=False),
        sa.Column("page_path", sa.String(length=2048), nullable=False),
        sa.Column("literal_external_key", sa.String(), nullable=False),
        sa.Column("decoded_external_key", sa.String(), nullable=False),
        sa.Column("request_method", sa.String(length=8), nullable=False),
        sa.Column("request_origin", sa.String(length=255), nullable=False),
        sa.Column("request_path", sa.String(length=255), nullable=False),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column("price_semantics", sa.String(length=64), nullable=False),
        sa.Column("buy_level_count", sa.Integer(), nullable=False),
        sa.Column("sell_level_count", sa.Integer(), nullable=False),
        sa.Column("buy_depth", sa.BigInteger(), nullable=False),
        sa.Column("sell_depth", sa.BigInteger(), nullable=False),
        sa.Column("best_buy_price_raw", sa.BigInteger(), nullable=False),
        sa.Column("best_buy_quantity", sa.BigInteger(), nullable=False),
        sa.Column("best_sell_price_raw", sa.BigInteger(), nullable=False),
        sa.Column("best_sell_quantity", sa.BigInteger(), nullable=False),
        sa.Column("capture_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capture_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_manual_order_book_imports_capture_uuid4",
        ),
        sa.CheckConstraint(
            "source_type = 'manual_response_json'",
            name="ck_manual_order_book_imports_source_type",
        ),
        sa.CheckConstraint(
            "capture_method = 'passive_page_response_intercept'",
            name="ck_manual_order_book_imports_capture_method",
        ),
        sa.CheckConstraint(
            "review_status = 'confirmed_by_user'",
            name="ck_manual_order_book_imports_review_status",
        ),
        sa.CheckConstraint(
            "price_semantics = 'gaijin_market_response_price_raw_unscaled'",
            name="ck_manual_order_book_imports_price_semantics",
        ),
        sa.CheckConstraint(
            "source_file_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_manual_order_book_imports_file_sha256",
        ),
        sa.CheckConstraint(
            "raw_response_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_manual_order_book_imports_raw_sha256",
        ),
        sa.CheckConstraint(
            "normalized_capture_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_manual_order_book_imports_fingerprint",
        ),
        sa.CheckConstraint(
            "timezone_offset_minutes BETWEEN -840 AND 840",
            name="ck_manual_order_book_imports_timezone_offset",
        ),
        sa.CheckConstraint(
            "buy_level_count BETWEEN 1 AND 500 AND sell_level_count BETWEEN 1 AND 500",
            name="ck_manual_order_book_imports_level_counts",
        ),
        sa.CheckConstraint(
            "buy_depth BETWEEN 0 AND 9007199254740991 AND sell_depth BETWEEN 0 AND 9007199254740991",
            name="ck_manual_order_book_imports_depths",
        ),
        sa.CheckConstraint(
            "best_buy_price_raw > 0 AND best_sell_price_raw > 0",
            name="ck_manual_order_book_imports_best_prices",
        ),
        sa.CheckConstraint(
            "best_buy_quantity > 0 AND best_sell_quantity > 0",
            name="ck_manual_order_book_imports_best_quantities",
        ),
        sa.CheckConstraint(
            "page_origin = 'https://trade.gaijin.net'",
            name="ck_manual_order_book_imports_page_origin",
        ),
        sa.CheckConstraint(
            "request_method = 'POST' AND request_origin = 'https://market-proxy.gaijin.net' AND request_path = '/web'",
            name="ck_manual_order_book_imports_request_identity",
        ),
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_job_id"),
        sa.UniqueConstraint("capture_id", name="uq_manual_order_book_imports_capture_id"),
        sa.UniqueConstraint(
            "normalized_capture_fingerprint",
            name="uq_manual_order_book_imports_fingerprint",
        ),
        sa.UniqueConstraint(
            "source_file_sha256",
            name="uq_manual_order_book_imports_file_sha256",
        ),
    )
    op.create_index(
        "ix_manual_order_book_imports_item_id",
        "manual_order_book_imports",
        ["item_id"],
    )
    op.create_index(
        "ix_manual_order_book_imports_captured_at",
        "manual_order_book_imports",
        ["captured_at"],
    )

    op.create_table(
        "manual_order_book_levels",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_import_id", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("level_index", sa.Integer(), nullable=False),
        sa.Column("price_raw", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "side IN ('BUY', 'SELL')",
            name="ck_manual_order_book_levels_side",
        ),
        sa.CheckConstraint("level_index >= 0", name="ck_manual_order_book_levels_index"),
        sa.CheckConstraint("price_raw > 0", name="ck_manual_order_book_levels_price"),
        sa.CheckConstraint("quantity > 0", name="ck_manual_order_book_levels_quantity"),
        sa.ForeignKeyConstraint(
            ["source_import_id"],
            ["manual_order_book_imports.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_import_id",
            "side",
            "level_index",
            name="uq_manual_order_book_levels_import_side_index",
        ),
    )
    op.create_index(
        "ix_manual_order_book_levels_source_import_id",
        "manual_order_book_levels",
        ["source_import_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_manual_order_book_levels_source_import_id",
        table_name="manual_order_book_levels",
    )
    op.drop_table("manual_order_book_levels")
    op.drop_index(
        "ix_manual_order_book_imports_captured_at",
        table_name="manual_order_book_imports",
    )
    op.drop_index(
        "ix_manual_order_book_imports_item_id",
        table_name="manual_order_book_imports",
    )
    op.drop_table("manual_order_book_imports")
