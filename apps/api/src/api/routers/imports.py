import hashlib
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ImportJob
from api.db.session import get_session
from api.importers.csv_market_data import CsvContractError, parse_csv_market_data
from api.schemas.imports import ImportErrorReport, ImportJobDetailResponse, ImportJobResponse
from api.services.csv_import import CsvImportService, ImportJobNotFoundError

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

MAX_CSV_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "text/plain",
    "application/octet-stream",
}


@router.post("/csv", response_model=ImportJobResponse, status_code=status.HTTP_201_CREATED)
async def import_csv(
    file: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    filename = _validate_upload_metadata(file)
    content = await _read_limited_upload(file)
    checksum = hashlib.sha256(content).hexdigest()
    service = CsvImportService(session)

    try:
        parsed = parse_csv_market_data(content)
    except CsvContractError as exc:
        job = await service.create_failed_job_for_contract_error(
            filename=filename, checksum=checksum, error=exc
        )
        return _job_response(job)

    job = await service.import_parsed_csv(filename=filename, checksum=checksum, parsed=parsed)
    return _job_response(job)


@router.get("/{job_id}", response_model=ImportJobDetailResponse)
async def get_import_job(
    job_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobDetailResponse:
    service = CsvImportService(session)
    try:
        job = await service.get_job(job_id)
    except ImportJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "import_job_not_found", "message": "Import job was not found."},
        ) from exc

    return _job_detail_response(job)


def _validate_upload_metadata(file: UploadFile) -> str:
    filename = PurePath(file.filename or "").name
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "missing_filename", "message": "A CSV filename is required."},
        )

    if PurePath(filename).suffix.lower() != ".csv":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "invalid_extension", "message": "Only .csv files are allowed."},
        )

    if file.content_type and file.content_type.lower() not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error_code": "invalid_mime_type", "message": "Upload must be a CSV file."},
        )

    return filename


async def _read_limited_upload(file: UploadFile) -> bytes:
    content = await file.read(MAX_CSV_UPLOAD_BYTES + 1)
    if len(content) > MAX_CSV_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "error_code": "file_too_large",
                "message": "CSV uploads are limited to 10 MB.",
            },
        )
    return content


def _job_response(job: ImportJob) -> ImportJobResponse:
    report = _error_report(job)
    return ImportJobResponse(
        job_id=job.id,
        status=job.status,
        filename=job.filename,
        checksum=job.checksum,
        row_count=job.row_count,
        valid_row_count=job.valid_row_count,
        invalid_row_count=job.invalid_row_count,
        duplicate_of_job_id=report.duplicate_of_job_id,
    )


def _job_detail_response(job: ImportJob) -> ImportJobDetailResponse:
    report = _error_report(job)
    return ImportJobDetailResponse(
        **_job_response(job).model_dump(),
        source_type=job.source_type,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_report=report,
    )


def _error_report(job: ImportJob) -> ImportErrorReport:
    if not job.error_report:
        return ImportErrorReport()
    return ImportErrorReport.model_validate(job.error_report)
