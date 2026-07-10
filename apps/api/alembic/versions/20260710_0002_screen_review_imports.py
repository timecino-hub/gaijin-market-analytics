"""screen review imports

Revision ID: 20260710_0002
Revises: 20260627_0001
Create Date: 2026-07-10 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260710_0002"
down_revision: Union[str, Sequence[str], None] = "20260627_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "screen_review_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("review_id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("market_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("review_status", sa.String(), nullable=False),
        sa.Column("candidate_version", sa.String(), nullable=False),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=False),
        sa.Column("total_bid_quantity", sa.Integer(), nullable=True),
        sa.Column("total_ask_quantity", sa.Integer(), nullable=True),
        sa.Column(
            "candidate_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("reviewer_note", sa.String(), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_screen_review_imports_candidate_sha256_hex",
        ),
        sa.CheckConstraint(
            "total_ask_quantity IS NULL OR total_ask_quantity >= 0",
            name="ck_screen_review_imports_ask_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "total_bid_quantity IS NULL OR total_bid_quantity >= 0",
            name="ck_screen_review_imports_bid_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "review_id <> ''",
            name="ck_screen_review_imports_review_id_not_empty",
        ),
        sa.CheckConstraint(
            "review_status IN ('confirmed', 'confirmed_with_edits')",
            name="ck_screen_review_imports_status_allowed",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["market_snapshot_id"], ["market_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_snapshot_id"),
        sa.UniqueConstraint("review_id"),
    )
    op.create_index(
        "ix_screen_review_imports_imported_at",
        "screen_review_imports",
        ["imported_at"],
        unique=False,
    )
    op.create_index(
        "ix_screen_review_imports_item_id",
        "screen_review_imports",
        ["item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_screen_review_imports_item_id", table_name="screen_review_imports")
    op.drop_index("ix_screen_review_imports_imported_at", table_name="screen_review_imports")
    op.drop_table("screen_review_imports")
