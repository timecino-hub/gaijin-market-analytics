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
    historical_trade_imports: Mapped[list["HistoricalTradeImport"]] = relationship(
        back_populates="item"
    )
    historical_trade_buckets: Mapped[list["HistoricalTradeBucket"]] = relationship(
        back_populates="item"
    )
    manual_order_book_imports: Mapped[list["ManualOrderBookImport"]] = relationship(
        back_populates="item"
    )


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
    manual_order_book_imports: Mapped[list["ManualOrderBookImport"]] = relationship(
        back_populates="import_job"
    )


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
    order_book_observation: Mapped["OrderBookObservation | None"] = relationship(
        back_populates="market_snapshot",
        uselist=False,
    )


class ScreenReviewImport(Base):
    __tablename__ = "screen_review_imports"
    __table_args__ = (
        CheckConstraint("review_id <> ''", name="ck_screen_review_imports_review_id_not_empty"),
        CheckConstraint(
            "review_status IN ('confirmed', 'confirmed_with_edits')",
            name="ck_screen_review_imports_status_allowed",
        ),
        CheckConstraint(
            "candidate_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_screen_review_imports_candidate_sha256_hex",
        ),
        CheckConstraint(
            "total_ask_quantity IS NULL OR total_ask_quantity >= 0",
            name="ck_screen_review_imports_ask_quantity_non_negative",
        ),
        CheckConstraint(
            "total_bid_quantity IS NULL OR total_bid_quantity >= 0",
            name="ck_screen_review_imports_bid_quantity_non_negative",
        ),
        Index("ix_screen_review_imports_item_id", "item_id"),
        Index("ix_screen_review_imports_imported_at", "imported_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    market_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=False, unique=True
    )
    review_status: Mapped[str] = mapped_column(String, nullable=False)
    candidate_version: Mapped[str] = mapped_column(String, nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    total_bid_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ask_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reviewer_note: Mapped[str | None] = mapped_column(String, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    order_book_observation: Mapped["OrderBookObservation | None"] = relationship(
        back_populates="screen_review_import",
        uselist=False,
    )


class OrderBookObservation(Base):
    __tablename__ = "order_book_observations"
    __table_args__ = (
        CheckConstraint(
            "observed_bid_quantity IS NULL OR observed_bid_quantity >= 0",
            name="ck_order_book_observations_bid_quantity_non_negative",
        ),
        CheckConstraint(
            "observed_ask_quantity IS NULL OR observed_ask_quantity >= 0",
            name="ck_order_book_observations_ask_quantity_non_negative",
        ),
        CheckConstraint(
            "quantity_semantics = 'screenshot_display_quantity'",
            name="ck_order_book_observations_quantity_semantics",
        ),
        CheckConstraint(
            "source_type = 'screen_review'",
            name="ck_order_book_observations_source_type",
        ),
        CheckConstraint(
            "source_version <> ''",
            name="ck_order_book_observations_source_version_not_empty",
        ),
        CheckConstraint(
            "review_status IN ('confirmed', 'confirmed_with_edits')",
            name="ck_order_book_observations_review_status_allowed",
        ),
        Index("ix_order_book_observations_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("market_snapshots.id"), nullable=False, unique=True
    )
    screen_review_import_id: Mapped[int] = mapped_column(
        ForeignKey("screen_review_imports.id"), nullable=False, unique=True
    )
    observed_bid_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_ask_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity_semantics: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_version: Mapped[str] = mapped_column(String, nullable=False)
    review_status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    market_snapshot: Mapped[MarketSnapshot] = relationship(
        back_populates="order_book_observation"
    )
    screen_review_import: Mapped[ScreenReviewImport] = relationship(
        back_populates="order_book_observation"
    )


class ManualOrderBookImport(Base):
    __tablename__ = "manual_order_book_imports"
    __table_args__ = (
        CheckConstraint(
            "capture_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_manual_order_book_imports_capture_uuid4",
        ),
        CheckConstraint(
            "source_type = 'manual_response_json'",
            name="ck_manual_order_book_imports_source_type",
        ),
        CheckConstraint(
            "capture_method = 'passive_page_response_intercept'",
            name="ck_manual_order_book_imports_capture_method",
        ),
        CheckConstraint(
            "review_status = 'confirmed_by_user'",
            name="ck_manual_order_book_imports_review_status",
        ),
        CheckConstraint(
            "price_semantics = 'gaijin_market_response_price_raw_unscaled'",
            name="ck_manual_order_book_imports_price_semantics",
        ),
        CheckConstraint(
            "source_file_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_manual_order_book_imports_file_sha256",
        ),
        CheckConstraint(
            "raw_response_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_manual_order_book_imports_raw_sha256",
        ),
        CheckConstraint(
            "normalized_capture_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_manual_order_book_imports_fingerprint",
        ),
        CheckConstraint(
            "timezone_offset_minutes BETWEEN -840 AND 840",
            name="ck_manual_order_book_imports_timezone_offset",
        ),
        CheckConstraint(
            "buy_level_count BETWEEN 1 AND 500 AND sell_level_count BETWEEN 1 AND 500",
            name="ck_manual_order_book_imports_level_counts",
        ),
        CheckConstraint(
            "buy_depth BETWEEN 0 AND 9007199254740991 AND sell_depth BETWEEN 0 AND 9007199254740991",
            name="ck_manual_order_book_imports_depths",
        ),
        CheckConstraint(
            "best_buy_price_raw > 0 AND best_sell_price_raw > 0",
            name="ck_manual_order_book_imports_best_prices",
        ),
        CheckConstraint(
            "best_buy_quantity > 0 AND best_sell_quantity > 0",
            name="ck_manual_order_book_imports_best_quantities",
        ),
        CheckConstraint(
            "page_origin = 'https://trade.gaijin.net'",
            name="ck_manual_order_book_imports_page_origin",
        ),
        CheckConstraint(
            "request_method = 'POST' AND request_origin = 'https://market-proxy.gaijin.net' AND request_path = '/web'",
            name="ck_manual_order_book_imports_request_identity",
        ),
        Index("ix_manual_order_book_imports_item_id", "item_id"),
        Index("ix_manual_order_book_imports_captured_at", "captured_at"),
        UniqueConstraint("capture_id", name="uq_manual_order_book_imports_capture_id"),
        UniqueConstraint(
            "normalized_capture_fingerprint",
            name="uq_manual_order_book_imports_fingerprint",
        ),
        UniqueConstraint(
            "source_file_sha256",
            name="uq_manual_order_book_imports_file_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    import_job_id: Mapped[int] = mapped_column(
        ForeignKey("import_jobs.id"), nullable=False, unique=True
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    capture_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_method: Mapped[str] = mapped_column(String(64), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_capture_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone_offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_origin: Mapped[str] = mapped_column(String(255), nullable=False)
    page_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    literal_external_key: Mapped[str] = mapped_column(String, nullable=False)
    decoded_external_key: Mapped[str] = mapped_column(String, nullable=False)
    request_method: Mapped[str] = mapped_column(String(8), nullable=False)
    request_origin: Mapped[str] = mapped_column(String(255), nullable=False)
    request_path: Mapped[str] = mapped_column(String(255), nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False)
    price_semantics: Mapped[str] = mapped_column(String(64), nullable=False)
    buy_level_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_level_count: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_depth: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_depth: Mapped[int] = mapped_column(BigInteger, nullable=False)
    best_buy_price_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    best_buy_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    best_sell_price_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    best_sell_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capture_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    import_job: Mapped[ImportJob] = relationship(back_populates="manual_order_book_imports")
    item: Mapped[Item] = relationship(back_populates="manual_order_book_imports")
    levels: Mapped[list["ManualOrderBookLevel"]] = relationship(
        back_populates="source_import",
        cascade="all, delete-orphan",
        order_by="ManualOrderBookLevel.side, ManualOrderBookLevel.level_index",
    )


class ManualOrderBookLevel(Base):
    __tablename__ = "manual_order_book_levels"
    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_manual_order_book_levels_side"),
        CheckConstraint("level_index >= 0", name="ck_manual_order_book_levels_index"),
        CheckConstraint("price_raw > 0", name="ck_manual_order_book_levels_price"),
        CheckConstraint("quantity > 0", name="ck_manual_order_book_levels_quantity"),
        UniqueConstraint(
            "source_import_id",
            "side",
            "level_index",
            name="uq_manual_order_book_levels_import_side_index",
        ),
        Index("ix_manual_order_book_levels_source_import_id", "source_import_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_import_id: Mapped[int] = mapped_column(
        ForeignKey("manual_order_book_imports.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    level_index: Mapped[int] = mapped_column(Integer, nullable=False)
    price_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)

    source_import: Mapped[ManualOrderBookImport] = relationship(back_populates="levels")


class HistoricalTradeImport(Base):
    __tablename__ = "historical_trade_imports"
    __table_args__ = (
        CheckConstraint("source_series_sha256 ~ '^[0-9a-f]{64}$'", name="ck_historical_trade_imports_sha256"),
        CheckConstraint("source_schema_version <> ''", name="ck_historical_trade_imports_schema_version"),
        CheckConstraint("point_count_1h >= 0", name="ck_historical_trade_imports_1h_count_non_negative"),
        CheckConstraint("point_count_1d >= 0", name="ck_historical_trade_imports_1d_count_non_negative"),
        CheckConstraint("inserted_count >= 0", name="ck_historical_trade_imports_inserted_non_negative"),
        CheckConstraint("updated_count >= 0", name="ck_historical_trade_imports_updated_non_negative"),
        CheckConstraint("unchanged_count >= 0", name="ck_historical_trade_imports_unchanged_non_negative"),
        Index("ix_historical_trade_imports_item_id", "item_id"),
        Index("ix_historical_trade_imports_imported_at", "imported_at"),
        UniqueConstraint("item_id", "source_series_sha256", name="uq_historical_trade_imports_item_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    pairing_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url_safe: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extension_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_series_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    point_count_1h: Mapped[int] = mapped_column(Integer, nullable=False)
    point_count_1d: Mapped[int] = mapped_column(Integer, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    overlap_day_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    overlap_mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    item: Mapped[Item] = relationship(back_populates="historical_trade_imports")
    buckets: Mapped[list["HistoricalTradeBucket"]] = relationship(back_populates="source_import")


class HistoricalTradeBucket(Base):
    __tablename__ = "historical_trade_buckets"
    __table_args__ = (
        UniqueConstraint(
            "item_id", "granularity", "bucket_start_utc",
            name="uq_historical_trade_buckets_item_granularity_start",
        ),
        CheckConstraint("granularity IN ('1h', '1d')", name="ck_historical_trade_buckets_granularity"),
        CheckConstraint("bucket_duration_seconds IN (3600, 86400)", name="ck_historical_trade_buckets_duration"),
        CheckConstraint("vwap_price_raw > 0", name="ck_historical_trade_buckets_price_raw_positive"),
        CheckConstraint("price_scale = 10000", name="ck_historical_trade_buckets_price_scale"),
        CheckConstraint("reported_vwap_price > 0", name="ck_historical_trade_buckets_price_positive"),
        CheckConstraint("reported_trade_volume > 0", name="ck_historical_trade_buckets_volume_positive"),
        CheckConstraint(
            "price_semantics = 'bucket_volume_weighted_average_trade_price'",
            name="ck_historical_trade_buckets_price_semantics",
        ),
        CheckConstraint(
            "volume_semantics = 'reported_trade_volume_unknown_unit'",
            name="ck_historical_trade_buckets_volume_semantics",
        ),
        Index("ix_historical_trade_buckets_item_start", "item_id", "bucket_start_utc"),
        Index("ix_historical_trade_buckets_granularity_start", "granularity", "bucket_start_utc"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    source_import_id: Mapped[int] = mapped_column(
        ForeignKey("historical_trade_imports.id"), nullable=False
    )
    granularity: Mapped[str] = mapped_column(String(2), nullable=False)
    bucket_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    vwap_price_raw: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_scale: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10000")
    reported_vwap_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    reported_trade_volume: Mapped[int] = mapped_column(Integer, nullable=False)
    price_semantics: Mapped[str] = mapped_column(String, nullable=False)
    volume_semantics: Mapped[str] = mapped_column(String, nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    item: Mapped[Item] = relationship(back_populates="historical_trade_buckets")
    source_import: Mapped[HistoricalTradeImport] = relationship(back_populates="buckets")
