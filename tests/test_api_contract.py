from fastapi.testclient import TestClient

EXPECTED_PATHS = [
    "/api/v1/auth/register",
    "/api/v1/events",
    "/api/v1/events/{event_id}/ticket-types",
    "/api/v1/orders",
    "/api/v1/orders/{order_id}/pay",
    "/api/v1/events/{event_id}/activate-paid-sales",
    "/api/v1/me/tickets",
    "/api/v1/tickets/{ticket_id}/pdf",
    "/api/v1/checkin",
    "/api/v1/checkin/validate",
    "/api/v1/me/assigned-events",
    "/api/v1/events/{event_id}/campaigns",
    "/api/v1/promo/validate",
    "/api/v1/events/{event_id}/seatmap",
    "/api/v1/events/{event_id}/analytics",
    "/api/v1/events/{event_id}/timeline",
    "/api/v1/support/cases",
    "/api/v1/admin/events/{event_id}/suspend",
    "/api/v1/events/{event_id}/calendar.ics",
]


def test_openapi_contains_full_contract(app_client: TestClient) -> None:
    spec = app_client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    missing = [p for p in EXPECTED_PATHS if p not in paths]
    assert not missing, missing


def test_campaign_resolve_is_not_shadowed_by_id_route(app_client: TestClient) -> None:
    resp = app_client.get("/api/v1/campaigns/resolve", params={"token": "x"})
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "not_implemented"
