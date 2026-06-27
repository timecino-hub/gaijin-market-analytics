import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from api.schemas.imports import CsvMarketDataRow, ImportIssue


CSV_V1_REQUIRED_FIELDS = (
    "item_key",
    "item_name",
    "category",
    "observed_at",
    "best_ask",
    "best_bid",
    "ask_count",
    "bid_count",
    "estimated_volume",
)


@dataclass(frozen=True)
class CsvParseResult:
    rows: list[CsvMarketDataRow] = field(default_factory=list)
    errors: list[ImportIssue] = field(default_factory=list)
    row_count: int = 0


class CsvContractError(ValueError):
    def __init__(self, code: str, message: str, field: str = "file") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def parse_csv_market_data(content: bytes) -> CsvParseResult:
    if not content:
        raise CsvContractError("empty_file", "CSV file is empty.")

    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvContractError("decode_error", "CSV file must be valid UTF-8.") from exc

    if not decoded.strip():
        raise CsvContractError("empty_file", "CSV file is empty.")

    reader = csv.DictReader(StringIO(decoded), skipinitialspace=True)
    if reader.fieldnames is None:
        raise CsvContractError("missing_header", "CSV header row is required.")

    headers = {header.strip() for header in reader.fieldnames if header is not None}
    missing = [field for field in CSV_V1_REQUIRED_FIELDS if field not in headers]
    if missing:
        raise CsvContractError(
            "missing_header",
            f"CSV header is missing required fields: {', '.join(missing)}.",
            field="header",
        )

    parsed_rows: list[CsvMarketDataRow] = []
    errors: list[ImportIssue] = []
    row_count = 0

    for row_number, raw_row in enumerate(reader, start=2):
        row_count += 1
        normalized = {
            key.strip(): (value.strip() if isinstance(value, str) else value)
            for key, value in raw_row.items()
            if key is not None
        }
        row, row_errors = _parse_row(row_number, normalized)
        if row_errors:
            errors.extend(row_errors)
            continue
        if row is not None:
            parsed_rows.append(row)

    if row_count == 0:
        raise CsvContractError("empty_file", "CSV file has a header but no data rows.")

    return CsvParseResult(rows=parsed_rows, errors=errors, row_count=row_count)


def _parse_row(
    row_number: int, raw_row: dict[str, str | None]
) -> tuple[CsvMarketDataRow | None, list[ImportIssue]]:
    errors: list[ImportIssue] = []

    item_key = _required_text(raw_row, "item_key", row_number, errors)
    item_name = _required_text(raw_row, "item_name", row_number, errors)
    category = _required_text(raw_row, "category", row_number, errors)
    observed_at = _parse_observed_at(raw_row.get("observed_at"), row_number, errors)
    best_ask = _parse_decimal(
        raw_row.get("best_ask"),
        "best_ask",
        row_number,
        errors,
        required=True,
        must_be_positive=True,
    )
    best_bid = _parse_decimal(
        raw_row.get("best_bid"),
        "best_bid",
        row_number,
        errors,
        required=False,
        must_be_non_negative=True,
    )
    ask_count = _parse_int(raw_row.get("ask_count"), "ask_count", row_number, errors)
    bid_count = _parse_int(raw_row.get("bid_count"), "bid_count", row_number, errors)
    estimated_volume = _parse_decimal(
        raw_row.get("estimated_volume"),
        "estimated_volume",
        row_number,
        errors,
        required=False,
        must_be_non_negative=True,
    )

    if errors:
        return None, errors

    return (
        CsvMarketDataRow(
            row_number=row_number,
            item_key=item_key or "",
            item_name=item_name or "",
            category=category or "",
            observed_at=observed_at or datetime.min.replace(tzinfo=UTC),
            best_ask=best_ask or Decimal("0"),
            best_bid=best_bid,
            ask_count=ask_count,
            bid_count=bid_count,
            estimated_volume=estimated_volume,
        ),
        [],
    )


def _required_text(
    raw_row: dict[str, str | None], field: str, row_number: int, errors: list[ImportIssue]
) -> str | None:
    value = raw_row.get(field)
    if value is None or value == "":
        errors.append(
            ImportIssue(
                row_number=row_number,
                field=field,
                error_code="required",
                message=f"{field} is required.",
            )
        )
        return None
    return value


def _parse_observed_at(
    value: str | None, row_number: int, errors: list[ImportIssue]
) -> datetime | None:
    if value is None or value == "":
        errors.append(
            ImportIssue(
                row_number=row_number,
                field="observed_at",
                error_code="required",
                message="observed_at is required.",
            )
        )
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        errors.append(
            ImportIssue(
                row_number=row_number,
                field="observed_at",
                error_code="invalid_datetime",
                message="observed_at must be ISO-8601.",
            )
        )
        return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(
            ImportIssue(
                row_number=row_number,
                field="observed_at",
                error_code="timezone_required",
                message="observed_at must include a timezone.",
            )
        )
        return None

    return parsed.astimezone(UTC)


def _parse_decimal(
    value: str | None,
    field: str,
    row_number: int,
    errors: list[ImportIssue],
    *,
    required: bool,
    must_be_positive: bool = False,
    must_be_non_negative: bool = False,
) -> Decimal | None:
    if value is None or value == "":
        if required:
            errors.append(
                ImportIssue(
                    row_number=row_number,
                    field=field,
                    error_code="required",
                    message=f"{field} is required.",
                )
            )
        return None

    try:
        parsed = Decimal(value)
    except InvalidOperation:
        errors.append(
            ImportIssue(
                row_number=row_number,
                field=field,
                error_code="invalid_decimal",
                message=f"{field} must be a Decimal value.",
            )
        )
        return None

    if not parsed.is_finite():
        errors.append(
            ImportIssue(
                row_number=row_number,
                field=field,
                error_code="invalid_decimal",
                message=f"{field} must be a finite Decimal value.",
            )
        )
        return None

    if must_be_positive and parsed <= 0:
        errors.append(
            ImportIssue(
                row_number=row_number,
                field=field,
                error_code="must_be_positive",
                message=f"{field} must be greater than 0.",
            )
        )
    elif must_be_non_negative and parsed < 0:
        errors.append(
            ImportIssue(
                row_number=row_number,
                field=field,
                error_code="must_be_non_negative",
                message=f"{field} must be greater than or equal to 0.",
            )
        )

    return parsed


def _parse_int(
    value: str | None, field: str, row_number: int, errors: list[ImportIssue]
) -> int | None:
    if value is None or value == "":
        return None

    try:
        parsed = int(value)
    except ValueError:
        errors.append(
            ImportIssue(
                row_number=row_number,
                field=field,
                error_code="invalid_integer",
                message=f"{field} must be an integer.",
            )
        )
        return None

    if parsed < 0:
        errors.append(
            ImportIssue(
                row_number=row_number,
                field=field,
                error_code="must_be_non_negative",
                message=f"{field} must be greater than or equal to 0.",
            )
        )

    return parsed
