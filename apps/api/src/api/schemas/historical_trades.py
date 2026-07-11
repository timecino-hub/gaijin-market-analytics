from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


PRICE_SCALE = 10_000
MAX_POINTS_PER_SERIES = 20_000
MAX_PRICE_RAW = 10_000_000_000_000
MAX_REPORTED_VOLUME = 2_000_000_000


class HistoricalTradePointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unix_seconds: int
    price_raw: int
    reported_trade_volume: int

    @field_validator("unix_seconds")
    @classmethod
    def validate_timestamp(cls, value: int) -> int:
        if value < 1_500_000_000 or value > 4_102_444_800:
            raise ValueError("historical_trade_timestamp_out_of_range")
        return value

    @field_validator("price_raw")
    @classmethod
    def validate_price(cls, value: int) -> int:
        if value <= 0 or value > MAX_PRICE_RAW:
            raise ValueError("historical_trade_price_out_of_range")
        return value

    @field_validator("reported_trade_volume")
    @classmethod
    def validate_volume(cls, value: int) -> int:
        if value <= 0 or value > MAX_REPORTED_VOLUME:
            raise ValueError("historical_trade_volume_out_of_range")
        return value


class HistoricalTradeSeriesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    one_hour: list[HistoricalTradePointInput] = Field(alias="1h", max_length=MAX_POINTS_PER_SERIES)
    one_day: list[HistoricalTradePointInput] = Field(alias="1d", max_length=MAX_POINTS_PER_SERIES)

    @field_validator("one_hour", "one_day", mode="before")
    @classmethod
    def convert_wire_tuples(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        converted: list[object] = []
        for point in value:
            if isinstance(point, (list, tuple)) and len(point) == 3:
                converted.append(
                    {
                        "unix_seconds": point[0],
                        "price_raw": point[1],
                        "reported_trade_volume": point[2],
                    }
                )
            else:
                converted.append(point)
        return converted

    @model_validator(mode="after")
    def validate_series(self) -> "HistoricalTradeSeriesInput":
        if not self.one_hour and not self.one_day:
            raise ValueError("historical_trade_series_empty")
        self._validate_granularity(self.one_hour, 3600, "1h")
        self._validate_granularity(self.one_day, 86400, "1d")
        return self

    @staticmethod
    def _validate_granularity(
        points: list[HistoricalTradePointInput], duration: int, label: str
    ) -> None:
        timestamps = [point.unix_seconds for point in points]
        if len(timestamps) != len(set(timestamps)):
            raise ValueError(f"historical_trade_duplicate_{label}_timestamp")
        if timestamps != sorted(timestamps):
            raise ValueError(f"historical_trade_{label}_not_sorted")
        if any(timestamp % duration != 0 for timestamp in timestamps):
            raise ValueError(f"historical_trade_{label}_timestamp_not_aligned")


class HistoricalTradeImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["gaijin_trade_history_v1"]
    item_key: str = Field(min_length=1, max_length=512)
    source_url: str = Field(min_length=1, max_length=2048)
    captured_at: datetime
    series: HistoricalTradeSeriesInput

    @field_validator("item_key")
    @classmethod
    def clean_item_key(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("historical_trade_item_key_required")
        return cleaned

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("historical_trade_captured_at_timezone_required")
        normalized = value.astimezone(UTC)
        if normalized > datetime.now(UTC).replace(microsecond=0):
            # Small client clock differences are handled by the service; reject obvious future data here.
            from datetime import timedelta
            if normalized > datetime.now(UTC) + timedelta(minutes=5):
                raise ValueError("historical_trade_captured_at_in_future")
        return normalized


class HistoricalTradeImportResponse(BaseModel):
    import_id: int
    item_id: int
    item_key: str
    source_series_sha256: str
    point_count_1h: int
    point_count_1d: int
    inserted_count: int
    updated_count: int
    unchanged_count: int
    overlap_day_count: int
    overlap_mismatch_count: int
    deduplicated: bool
    imported_at: datetime


class HistoricalTradeBucketResponse(BaseModel):
    id: int
    item_id: int
    granularity: Literal["1h", "1d"]
    bucket_start_utc: datetime
    bucket_duration_seconds: int
    vwap_price_raw: int
    price_scale: Literal[10000]
    reported_vwap_price: Decimal
    reported_trade_volume: int
    price_semantics: Literal["bucket_volume_weighted_average_trade_price"]
    volume_semantics: Literal["reported_trade_volume_unknown_unit"]
    source_schema_version: str
    first_seen_at: datetime
    last_seen_at: datetime

    @field_serializer("reported_vwap_price")
    def serialize_price(self, value: Decimal) -> str:
        return format(value, "f")


class HistoricalTradeBucketListResponse(BaseModel):
    item_id: int
    granularity: Literal["1h", "1d"]
    buckets: list[HistoricalTradeBucketResponse]
    total: int
