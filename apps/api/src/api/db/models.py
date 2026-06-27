from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("external_key <> ''", name="ck_items_external_key_not_empty"),
        Index("ix_items_external_key", "external_key"),
        Index("ix_items_category", "category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    external_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    rarity: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    snapshots: Mapped[list["MarketSnapshot"]] = relationship(back_populates="item")


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="ck_import_jobs_checksum_sha256_hex"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'duplicate')",
            name="ck_import_jobs_status_allowed",
        ),
        CheckConstraint("row_count >= 0", name="ck_import_jobs_row_count_non_negative"),
        CheckConstraint("valid_row_count >= 0", name="ck_import_jobs_valid_row_count_non_negative"),
        CheckConstraint(
            "invalid_row_count >= 0", name="ck_import_jobs_invalid_row_count_non_negative"
        ),
        Index("ix_import_jobs_checksum", "checksum"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    valid_row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    invalid_row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    snapshots: Mapped[list["MarketSnapshot"]] = relationship(back_populates="source_import_job")


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("item_id", "observed_at", name="uq_market_snapshots_item_observed_at"),
        CheckConstraint("best_ask > 0", name="ck_market_snapshots_best_ask_positive"),
        CheckConstraint("best_bid IS NULL OR best_bid >= 0", name="ck_market_snapshots_best_bid_non_negative"),
        CheckConstraint("ask_count IS NULL OR ask_count >= 0", name="ck_market_snapshots_ask_count_non_negative"),
        CheckConstraint("bid_count IS NULL OR bid_count >= 0", name="ck_market_snapshots_bid_count_non_negative"),
        CheckConstraint(
            "estimated_volume IS NULL OR estimated_volume >= 0",
            name="ck_market_snapshots_estimated_volume_non_negative",
        ),
        Index("ix_market_snapshots_item_id", "item_id"),
        Index("ix_market_snapshots_observed_at", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    best_ask: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    ask_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bid_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    source_import_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_jobs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    item: Mapped[Item] = relationship(back_populates="snapshots")
    source_import_job: Mapped[ImportJob | None] = relationship(back_populates="snapshots")
