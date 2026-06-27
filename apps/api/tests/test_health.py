import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from api.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ok_when_database_is_available(test_database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")

    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_ready_returns_503_when_database_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenSession:
        async def execute(self, statement: object) -> None:
            raise SQLAlchemyError("database unavailable")

    async def broken_session():
        yield BrokenSession()

    app.dependency_overrides.clear()
    from api.db.session import get_session

    app.dependency_overrides[get_session] = broken_session
    client = TestClient(app)

    response = client.get("/ready")

    app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"status": "unavailable", "dependency": "database"}
    }
