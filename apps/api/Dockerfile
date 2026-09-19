FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY packages/analytics /app/packages/analytics
COPY apps/api /app/apps/api
WORKDIR /app/apps/api
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run uvicorn api.main:app --host 0.0.0.0 --port 8000"]
