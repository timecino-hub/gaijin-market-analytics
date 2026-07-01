from fastapi.testclient import TestClient


def test_cors_allows_configured_local_web_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/items",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in response.headers


def test_cors_allows_configured_local_web_origin_to_post_csv(client: TestClient) -> None:
    response = client.options(
        "/api/v1/imports/csv",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


def test_cors_allows_configured_local_web_origin_to_patch_reviews(client: TestClient) -> None:
    response = client.options(
        "/api/v1/local-recognition/reviews/example",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


def test_cors_allows_configured_local_web_origin_to_delete_reviews(client: TestClient) -> None:
    response = client.options(
        "/api/v1/local-recognition/reviews/example",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "DELETE",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "DELETE" in response.headers["access-control-allow-methods"]
    assert "access-control-allow-credentials" not in response.headers


def test_cors_does_not_allow_unconfigured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/items",
        headers={
            "Origin": "http://example.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_does_not_allow_unconfigured_origin_to_post_csv(client: TestClient) -> None:
    response = client.options(
        "/api/v1/imports/csv",
        headers={
            "Origin": "http://example.invalid",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
