from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings, parse_cors_allowed_origins
from api.db.session import get_session
from api.routers.analysis import router as analysis_router
from api.routers.imports import router as imports_router
from api.routers.items import router as items_router
from api.routers.local_recognition import router as local_recognition_router

app = FastAPI(title="Gaijin Market Analytics API")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_allowed_origins(settings.cors_allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Accept", "Content-Type"],
)
app.include_router(imports_router)
app.include_router(items_router)
app.include_router(analysis_router)
app.include_router(local_recognition_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    known_codes = {
        "invalid_price_string",
        "invalid_quantity",
        "observed_at_timezone_required",
        "observed_at_in_future",
    }
    for error in exc.errors():
        message = str(error.get("msg", ""))
        for code in known_codes:
            if code in message:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": {"code": code, "message": _validation_message(code)}},
                )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unavailable", "dependency": "database"},
        ) from exc

    return {"status": "ready", "database": "ok"}


def main() -> None:
    import uvicorn

    run_settings = get_settings()
    uvicorn.run("api.main:app", host=run_settings.api_host, port=run_settings.api_port, reload=True)


def _validation_message(code: str) -> str:
    return {
        "invalid_price_string": "Prices must be JSON strings or null.",
        "invalid_quantity": "Quantity must be a non-negative integer or null.",
        "observed_at_timezone_required": "observed_at must include a timezone.",
        "observed_at_in_future": "observed_at cannot be more than five minutes in the future.",
    }[code]
