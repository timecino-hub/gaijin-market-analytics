from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ImportJobStatus = Literal["pending", "processing", "completed", "failed", "duplicate"]


class ImportIssue(BaseModel):
    row_number: int
    field: str
    error_code: str
    message: str


class CsvMarketDataRow(BaseModel):
    row_number: int
    item_key: str
    item_name: str
    category: str
    observed_at: datetime
    best_ask: Decimal
    best_bid: Decimal | None
    ask_count: int | None
    bid_count: int | None
    estimated_volume: Decimal | None


class ImportErrorReport(BaseModel):
    errors: list[ImportIssue] = Field(default_factory=list)
    warnings: list[ImportIssue] = Field(default_factory=list)
    duplicate_of_job_id: int | None = None


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: int
    status: ImportJobStatus
    filename: str
    checksum: str
    row_count: int
    valid_row_count: int
    invalid_row_count: int
    duplicate_of_job_id: int | None = None


class ImportJobDetailResponse(ImportJobResponse):
    source_type: str
    started_at: datetime
    finished_at: datetime | None
    error_report: ImportErrorReport
