"""Tests for the SpatialGrid Starlette HTTP API gateway."""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from spatialgrid.api import create_http_app
from spatialgrid.index import SpatialIndex, generate_synthetic_facilities


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
