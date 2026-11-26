"""Tests for the SpatialGrid Starlette HTTP API gateway."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from spatialgrid.api import create_http_app
from spatialgrid.cli import _load_api_keys_from_env
from spatialgrid.index import SpatialIndex, generate_synthetic_facilities, load_facilities_geojson


def _sample_geojson() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "acme-depot-1",
                "geometry": {"type": "Point", "coordinates": [-87.6298, 41.8781]},
                "properties": {
                    "name": "Chicago Central Depot",
                    "capacity": 200,
                    "facility_type": "depot",
                },
            },
            {
                "type": "Feature",
                "id": "acme-hub-2",
                "geometry": {"type": "Point", "coordinates": [-87.6500, 41.8900]},
                "properties": {
                    "name": "West Loop Hub",
                    "capacity": 150,
                    "facility_type": "hub",
                },
            },
        ],
    }


def test_healthz() -> None:
    client = TestClient(create_http_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "spatialgrid"}


def test_auth_missing_invalid_and_cross_tenant_mismatch() -> None:
    app = create_http_app(api_keys={"tok-acme": "acme"})
    client = TestClient(app)

    # /healthz remains unauthenticated
    assert client.get("/healthz").status_code == 200

    ingest_body = {"org_id": "acme", "geojson": _sample_geojson()}

    # Missing Authorization header -> 401
    resp_missing = client.post("/v1/facilities/ingest", json=ingest_body)
    assert resp_missing.status_code == 401

    # Invalid Bearer token -> 401
    resp_bad_tok = client.post(
        "/v1/facilities/ingest",
        json=ingest_body,
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp_bad_tok.status_code == 401

    # Cross-tenant org_id mismatch -> 403
    resp_cross = client.post(
        "/v1/facilities/ingest",
        json={"org_id": "other-org", "geojson": _sample_geojson()},
        headers={"Authorization": "Bearer tok-acme"},
    )
    assert resp_cross.status_code == 403

    # Valid token + matching org_id -> 200
    resp_ok = client.post(
        "/v1/facilities/ingest",
        json=ingest_body,
        headers={"Authorization": "Bearer tok-acme"},
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["ingested_count"] == 2


def test_end_to_end_http_flow() -> None:
    client = TestClient(create_http_app())

    # 1. Ingest facilities
    ingest_resp = client.post(
        "/v1/facilities/ingest",
        json={"org_id": "acme", "geojson": _sample_geojson()},
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json() == {
        "org_id": "acme",
        "ingested_count": 2,
        "total_org_facilities": 2,
    }

    # 2. Nearest facilities
    nearest_resp = client.post(
        "/v1/facilities/nearest",
        json={
            "org_id": "acme",
            "origin": {"lat": 41.8781, "lon": -87.6298},
            "limit": 2,
        },
    )
    assert nearest_resp.status_code == 200
    nearest_data = nearest_resp.json()
    assert nearest_data["org_id"] == "acme"
    assert len(nearest_data["matches"]) == 2
    assert nearest_data["matches"][0]["facility"]["facility_id"] == "acme-depot-1"

    # 3. Coverage gap
    stops = [
        {
            "demand_id": "stop-near",
            "org_id": "acme",
            "location": {"lat": 41.8800, "lon": -87.6320},
            "demand_units": 10,
            "priority": 1,
        },
        {
            "demand_id": "stop-far",
            "org_id": "acme",
            "location": {"lat": 42.3000, "lon": -88.2000},
            "demand_units": 15,
            "priority": 2,
        },
    ]
    gap_resp = client.post(
        "/v1/coverage-gap",
        json={
            "org_id": "acme",
            "demand_nodes": stops,
            "service_radius_km": 10.0,
        },
    )
    assert gap_resp.status_code == 200
    gap_data = gap_resp.json()
    assert gap_data["total_demand_nodes"] == 2
    assert gap_data["uncovered_count"] == 1
    assert gap_data["uncovered_nodes"][0]["demand_node"]["demand_id"] == "stop-far"

    # 4. Optimize dispatch
    dispatch_resp = client.post(
        "/v1/dispatch/optimize",
        json={
            "org_id": "acme",
            "depot_facility_id": "acme-depot-1",
            "vehicles": [
                {"vehicle_id": "veh-1", "capacity": 20},
                {"vehicle_id": "veh-2", "capacity": 20},
            ],
            "stops": stops,
            "time_limit_seconds": 5,
        },
    )
    assert dispatch_resp.status_code == 200
    dispatch_data = dispatch_resp.json()
    assert dispatch_data["org_id"] == "acme"
    assert dispatch_data["depot_facility_id"] == "acme-depot-1"
    assert dispatch_data["geometrically_valid"] is True
    assert dispatch_data["capacity_valid"] is True
    assert dispatch_data["total_demand_served"] == 25


def test_validation_error_and_missing_depot_and_invalid_json() -> None:
    index = SpatialIndex()
    index.add_facilities(generate_synthetic_facilities("demo-org", count=5, seed=42))
    client = TestClient(create_http_app(index=index))

    # 422 on invalid Pydantic payload (lat out of bounds)
    bad_nearest = client.post(
        "/v1/facilities/nearest",
        json={"org_id": "demo-org", "origin": {"lat": 123.0, "lon": -87.6}},
    )
    assert bad_nearest.status_code == 422

    # 404 on missing depot
    missing_depot = client.post(
        "/v1/dispatch/optimize",
        json={
            "org_id": "demo-org",
            "depot_facility_id": "nonexistent-depot",
            "vehicles": [{"vehicle_id": "v1", "capacity": 50}],
            "stops": [
                {
                    "demand_id": "s1",
                    "org_id": "demo-org",
                    "location": {"lat": 41.88, "lon": -87.63},
                    "demand_units": 10,
                }
            ],
        },
    )
    assert missing_depot.status_code == 404

    # 400 on invalid JSON
    bad_json = client.post(
        "/v1/facilities/nearest",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert bad_json.status_code == 400


def test_list_delete_facilities_request_id_and_payload_limit() -> None:
    app = create_http_app(api_keys={"tok-acme": "acme"}, max_body_bytes=128)
    client = TestClient(app)

    # Oversized body -> 413 PayloadTooLarge
    oversized = client.post(
        "/v1/facilities/ingest",
        json={"org_id": "acme", "geojson": _sample_geojson()},
        headers={"Authorization": "Bearer tok-acme", "X-Request-ID": "req-oversize"},
    )
    assert oversized.status_code == 413
    assert oversized.headers["x-request-id"] == "req-oversize"

    # Seed directly into a fresh app with normal limit to test GET/DELETE
    index = SpatialIndex()
    index.add_facilities(generate_synthetic_facilities("acme", count=3, seed=42))
    client2 = TestClient(create_http_app(index=index, api_keys={"tok-acme": "acme"}))

    list_resp = client2.get("/v1/facilities", headers={"Authorization": "Bearer tok-acme"})
    assert list_resp.status_code == 200
    facilities = list_resp.json()["facilities"]
    assert len(facilities) == 3
    assert "x-request-id" in list_resp.headers

    # Cross-tenant list -> 403
    cross_list = client2.get(
        "/v1/facilities?org_id=other",
        headers={"Authorization": "Bearer tok-acme"},
    )
    assert cross_list.status_code == 403

    # Delete existing -> 200; delete again -> 404
    fid = facilities[0]["facility_id"]
    del_resp = client2.delete(
        f"/v1/facilities/{fid}",
        headers={"Authorization": "Bearer tok-acme"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["removed"] is True

    del_missing = client2.delete(
        f"/v1/facilities/{fid}",
        headers={"Authorization": "Bearer tok-acme"},
    )
    assert del_missing.status_code == 404


def test_load_api_keys_from_env_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPATIALGRID_API_KEYS", raising=False)
    assert _load_api_keys_from_env(require_auth=False) is None
    with pytest.raises(ValueError, match="SPATIALGRID_API_KEYS"):
        _load_api_keys_from_env(require_auth=True)

    monkeypatch.setenv("SPATIALGRID_API_KEYS", '{"tok-1": "org-1"}')
    assert _load_api_keys_from_env(require_auth=True) == {"tok-1": "org-1"}


def test_readyz() -> None:
    empty_client = TestClient(create_http_app())
    empty_resp = empty_client.get("/readyz")
    assert empty_resp.status_code == 200
    assert empty_resp.json() == {"status": "ready", "orgs_indexed": 0}

    index = SpatialIndex()
    index.add_facilities(generate_synthetic_facilities("org-a", count=2, seed=1))
    index.add_facilities(generate_synthetic_facilities("org-b", count=2, seed=2))
    seeded_client = TestClient(create_http_app(index=index))
    seeded_resp = seeded_client.get("/readyz")
    assert seeded_resp.status_code == 200
    assert seeded_resp.json() == {"status": "ready", "orgs_indexed": 2}


def test_rate_limit_rpm_enforced() -> None:
    index = SpatialIndex()
    index.add_facilities(generate_synthetic_facilities("acme", count=2, seed=42))
    client = TestClient(
        create_http_app(index=index, api_keys={"tok-acme": "acme"}, rate_limit_rpm=2)
    )
    headers = {"Authorization": "Bearer tok-acme"}

    resp1 = client.get("/v1/facilities", headers=headers)
    assert resp1.status_code == 200

    resp2 = client.get("/v1/facilities", headers=headers)
    assert resp2.status_code == 200

    resp3 = client.get("/v1/facilities", headers=headers)
    assert resp3.status_code == 429
    assert resp3.headers.get("retry-after") == "60"

    # Probes (/healthz, /readyz) are not rate limited
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_dispatch_optimize_geojson_format() -> None:
    client = TestClient(create_http_app())
    ingest_resp = client.post(
        "/v1/facilities/ingest",
        json={"org_id": "acme", "geojson": _sample_geojson()},
    )
    assert ingest_resp.status_code == 200

    stops = [
        {
            "demand_id": "stop-1",
            "org_id": "acme",
            "location": {"lat": 41.8850, "lon": -87.6350},
            "demand_units": 10,
            "priority": 1,
        },
        {
            "demand_id": "stop-2",
            "org_id": "acme",
            "location": {"lat": 41.8950, "lon": -87.6450},
            "demand_units": 15,
            "priority": 2,
        },
    ]

    resp = client.post(
        "/v1/dispatch/optimize?format=geojson",
        json={
            "org_id": "acme",
            "depot_facility_id": "acme-depot-1",
            "vehicles": [{"vehicle_id": "veh-1", "capacity": 50}],
            "stops": stops,
            "time_limit_seconds": 5,
        },
    )
    assert resp.status_code == 200
    geojson = resp.json()
    assert geojson["type"] == "FeatureCollection"
    geom_types = {feature["geometry"]["type"] for feature in geojson["features"]}
    assert "LineString" in geom_types
    assert "Point" in geom_types


def test_workspace_html_and_example_geojson() -> None:
    client = TestClient(create_http_app())

    root_resp = client.get("/", follow_redirects=False)
    assert root_resp.status_code == 302
    assert root_resp.headers["location"] == "/workspace"

    ws_resp = client.get("/workspace")
    assert ws_resp.status_code == 200
    assert "text/html" in ws_resp.headers["content-type"]
    assert "SpatialGrid Workspace" in ws_resp.text
    assert "deck.gl" in ws_resp.text
    for control_label in (
        "Load Facilities",
        "Nearest Facilities",
        "Run Coverage Gap",
        "Run Sample Dispatch (GeoJSON)",
        "Ingest GeoJSON",
    ):
        assert control_label in ws_resp.text

    example_path = Path(__file__).resolve().parents[1] / "examples" / "chicago_facilities.geojson"
    payload = json.loads(example_path.read_text(encoding="utf-8"))
    facilities = load_facilities_geojson(payload, default_org_id="demo-org")
    assert len(facilities) == 4


def test_h3_summary_aggregation_and_tenant_isolation() -> None:
    index = SpatialIndex()
    facilities = load_facilities_geojson(_sample_geojson(), default_org_id="acme")
    index.add_facilities(facilities)
    client = TestClient(create_http_app(index=index, api_keys={"tok-acme": "acme"}))

    resp = client.get("/v1/h3/summary", headers={"Authorization": "Bearer tok-acme"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["org_id"] == "acme"
    cells = body["cells"]
    assert [c["h3_index"] for c in cells] == sorted(c["h3_index"] for c in cells)
    assert sum(c["facility_count"] for c in cells) == 2
    assert sum(c["total_capacity"] for c in cells) == 350

    cross_resp = client.get(
        "/v1/h3/summary?org_id=other-org",
        headers={"Authorization": "Bearer tok-acme"},
    )
    assert cross_resp.status_code == 403


def test_metrics_prometheus_endpoint() -> None:
    client = TestClient(create_http_app())

    ingest_resp = client.post(
        "/v1/facilities/ingest",
        json={"org_id": "acme", "geojson": _sample_geojson()},
    )
    assert ingest_resp.status_code == 200

    nearest_resp = client.post(
        "/v1/facilities/nearest",
        json={
            "org_id": "acme",
            "origin": {"lat": 41.8781, "lon": -87.6298},
            "limit": 1,
        },
    )
    assert nearest_resp.status_code == 200

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert "text/plain" in metrics_resp.headers["content-type"]
    body = metrics_resp.text
    assert "spatialgrid_http_requests_total" in body
    assert "spatialgrid_http_request_duration_ms_sum" in body
    assert "spatialgrid_http_request_duration_ms_count" in body
    assert "spatialgrid_orgs_indexed 1" in body
    assert "spatialgrid_facilities_indexed_total 2" in body
