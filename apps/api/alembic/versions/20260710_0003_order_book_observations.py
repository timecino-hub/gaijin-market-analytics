"""order book observations

Revision ID: 20260710_0003
Revises: 20260710_0002
Create Date: 2026-07-10 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260710_0003"
down_revision: Union[str, Sequence[str], None] = "20260710_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


QUANTITY_SEMANTICS = "screenshot_display_quantity"
SOURCE_TYPE = "screen_review"


def upgrade() -> None:
    op.create_table(
        "order_book_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("market_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("screen_review_import_id", sa.BigInteger(), nullable=False),
        sa.Column("observed_bid_quantity", sa.Integer(), nullable=True),
        sa.Column("observed_ask_quantity", sa.Integer(), nullable=True),
        sa.Column("quantity_semantics", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_version", sa.String(), nullable=False),
        sa.Column("review_status", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "observed_ask_quantity IS NULL OR observed_ask_quantity >= 0",
            name="ck_order_book_observations_ask_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "observed_bid_quantity IS NULL OR observed_bid_quantity >= 0",
            name="ck_order_book_observations_bid_quantity_non_negative",
        ),
        sa.CheckConstraint(
            f"quantity_semantics = '{QUANTITY_SEMANTICS}'",
            name="ck_order_book_observations_quantity_semantics",
        ),
        sa.CheckConstraint(
            "review_status IN ('confirmed', 'confirmed_with_edits')",
            name="ck_order_book_observations_review_status_allowed",
        ),
        sa.CheckConstraint(
            f"source_type = '{SOURCE_TYPE}'",
            name="ck_order_book_observations_source_type",
        ),
        sa.CheckConstraint(
            "source_version <> ''",
            name="ck_order_book_observations_source_version_not_empty",
        ),
        sa.ForeignKeyConstraint(["market_snapshot_id"], ["market_snapshots.id"]),
        sa.ForeignKeyConstraint(["screen_review_import_id"], ["screen_review_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_snapshot_id"),
        sa.UniqueConstraint("screen_review_import_id"),
    )
    op.create_index(
        "ix_order_book_observations_created_at",
        "order_book_observations",
        ["created_at"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO order_book_observations (
                market_snapshot_id,
                screen_review_import_id,
                observed_bid_quantity,
                observed_ask_quantity,
                quantity_semantics,
                source_type,
                source_version,
                review_status,
                created_at
            )
            SELECT
                market_snapshot_id,
                id,
                total_bid_quantity,
                total_ask_quantity,
                :quantity_semantics,
                :source_type,
                candidate_version,
                review_status,
                imported_at
            FROM screen_review_imports
            """
        ).bindparams(
            quantity_semantics=QUANTITY_SEMANTICS,
            source_type=SOURCE_TYPE,
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_order_book_observations_created_at",
        table_name="order_book_observations",
    )
    op.drop_table("order_book_observations")
