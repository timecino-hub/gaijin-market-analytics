from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings, parse_cors_allowed_origins
from api.db.session import get_session
from api.routers.imports import router as imports_router
from api.routers.items import router as items_router

app = FastAPI(title="Gaijin Market Analytics API")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_allowed_origins(settings.cors_allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)
app.include_router(imports_router)
app.include_router(items_router)


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

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
