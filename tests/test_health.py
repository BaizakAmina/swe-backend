import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.main import create_app


def test_health(app_client: TestClient) -> None:
    assert app_client.get("/health").json() == {"status": "ok"}
    assert app_client.get("/api/v1/health").status_code == 200


def test_unknown_route_uses_error_format(app_client: TestClient) -> None:
    resp = app_client.get("/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "http_404"


def test_app_error_is_rendered() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/boom")
    def boom() -> None:
        raise AppError("boom", "Something broke", 418)

    app.include_router(router)
    resp = TestClient(app).get("/boom")
    assert resp.status_code == 418
    assert resp.json() == {"error": {"code": "boom", "message": "Something broke"}}


@pytest.mark.db
def test_health_db(client: TestClient) -> None:
    assert client.get("/health/db").json()["database"] == "ok"
