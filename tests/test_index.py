"""Unit tests for SpatialGrid H3 + STRtree spatial index and GeoJSON loader."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest

from spatialgrid.exceptions import InvalidGeometryError, TenantIsolationError
from spatialgrid.geo_utils import haversine_km
from spatialgrid.index import (
    SpatialIndex,
    generate_synthetic_facilities,
    h3_grid_distance,
    load_facilities_geojson,
    point_to_h3,
)
from spatialgrid.models import (
    CoverageGapRequest,
    DemandNode,
    Facility,
    GeoPoint,
    NearestFacilitiesRequest,
)


def _make_facility(
    facility_id: str,
    org_id: str,
    lat: float,
    lon: float,
    *,
    capacity: int = 100,
    facility_type: str = "warehouse",
    active: bool = True,
) -> Facility:
    return Facility(
        facility_id=facility_id,
        org_id=org_id,
        name=f"Name {facility_id}",
        location=GeoPoint(lat=lat, lon=lon),
        capacity=capacity,
        facility_type=facility_type,
        active=active,
    )


def test_point_to_h3_and_grid_distance() -> None:
    chicago = GeoPoint(lat=41.8781, lon=-87.6298)
    nearby = GeoPoint(lat=41.8850, lon=-87.6250)
    tokyo = GeoPoint(lat=35.6762, lon=139.6503)

    cell_a = point_to_h3(chicago)
    cell_b = point_to_h3(nearby)
    cell_tokyo = point_to_h3(tokyo)

    assert cell_a == point_to_h3(chicago)
    assert h3_grid_distance(cell_a, cell_a) == 0
    dist_ab = h3_grid_distance(cell_a, cell_b)
    assert dist_ab is not None and dist_ab >= 0
    assert h3_grid_distance(cell_a, cell_tokyo) is None
    assert h3_grid_distance("not-an-h3-cell", cell_a) is None


def test_load_facilities_geojson_valid() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "fac-1",
                "geometry": {"type": "Point", "coordinates": [-87.6298, 41.8781]},
                "properties": {
                    "name": "Loop Hub",
                    "capacity": 250,
                    "facility_type": "hub",
                    "active": True,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-87.65, 41.90]},
                "properties": {
                    "facility_id": "fac-2",
                    "org_id": "org-override",
                    "name": "North Depot",
                    "capacity": 80,
                    "facility_type": "depot",
                    "active": False,
                },
            },
        ],
    }

    facilities = load_facilities_geojson(payload, default_org_id="org-default")
    assert len(facilities) == 2

    assert facilities[0].facility_id == "fac-1"
    assert facilities[0].org_id == "org-default"
    assert facilities[0].location == GeoPoint(lat=41.8781, lon=-87.6298)
    assert facilities[0].capacity == 250
    assert facilities[0].facility_type == "hub"
    assert facilities[0].h3_index is not None

    assert facilities[1].facility_id == "fac-2"
    assert facilities[1].org_id == "org-override"
    assert facilities[1].active is False


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"type": "Feature", "features": []},
        {"type": "FeatureCollection", "features": "not-a-list"},
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "poly-1",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                    },
                    "properties": {},
                }
            ],
        },
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "bad-coords",
                    "geometry": {"type": "Point", "coordinates": [200.0, 41.8]},
                    "properties": {},
                }
            ],
        },
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-87.6, 41.8]},
                    "properties": {"name": "Missing ID"},
                }
            ],
        },
    ],
)
def test_load_facilities_geojson_invalid(bad_payload: dict[str, object]) -> None:
    with pytest.raises(InvalidGeometryError):
        load_facilities_geojson(bad_payload, default_org_id="org-a")


def test_generate_synthetic_facilities_deterministic() -> None:
    first = generate_synthetic_facilities("org-bench", count=10, seed=123)
    second = generate_synthetic_facilities("org-bench", count=10, seed=123)
    assert first == second
    assert len(first) == 10
    assert all(fac.org_id == "org-bench" and fac.h3_index is not None for fac in first)


def test_nearest_facilities_filtering_ranking_and_h3_ring() -> None:
    index = SpatialIndex()
    origin = GeoPoint(lat=41.8781, lon=-87.6298)
    facilities = [
        _make_facility("f-inactive", "org-a", 41.8782, -87.6299, capacity=500, active=False),
        _make_facility("f-lowcap", "org-a", 41.8790, -87.6300, capacity=20, facility_type="depot"),
        _make_facility("f-close", "org-a", 41.8850, -87.6300, capacity=150, facility_type="depot"),
        _make_facility("f-mid", "org-a", 41.9100, -87.6300, capacity=200, facility_type="depot"),
        _make_facility("f-far", "org-a", 42.1000, -87.6300, capacity=300, facility_type="depot"),
        _make_facility("f-type", "org-a", 41.8800, -87.6290, capacity=300, facility_type="hub"),
    ]
    assert index.add_facilities(facilities) == 6

    indexed_close = index.get_facility("org-a", "f-close")
    assert indexed_close is not None
    assert indexed_close.h3_index == point_to_h3(indexed_close.location)

    response = index.nearest_facilities(
        NearestFacilitiesRequest(
            org_id="org-a",
            origin=origin,
            limit=5,
            max_distance_km=10.0,
            min_capacity=100,
            facility_type="depot",
            only_active=True,
        )
    )

    assert [m.facility.facility_id for m in response.matches] == ["f-close", "f-mid"]
    assert response.matches[0].distance_km < response.matches[1].distance_km
    assert response.matches[0].distance_km == pytest.approx(
        haversine_km(origin, indexed_close.location)
    )
    assert response.matches[0].h3_ring_distance is not None
    assert response.query_time_ms >= 0.0


def test_coverage_gap_accuracy() -> None:
    index = SpatialIndex()
    index.add_facilities(
        [
            _make_facility("f-1", "org-cov", 41.8781, -87.6298, capacity=200, active=True),
            _make_facility("f-low", "org-cov", 41.9500, -87.6500, capacity=10, active=True),
            _make_facility("f-off", "org-cov", 41.9500, -87.6500, capacity=500, active=False),
        ]
    )

    nodes = [
        DemandNode(
            demand_id="d-covered",
            org_id="org-cov",
            location=GeoPoint(lat=41.8800, lon=-87.6300),
            demand_units=40,
        ),
        DemandNode(
            demand_id="d-uncovered",
            org_id="org-cov",
            location=GeoPoint(lat=41.9500, lon=-87.6500),
            demand_units=60,
        ),
    ]

    result = index.coverage_gap(
        CoverageGapRequest(
            org_id="org-cov",
            demand_nodes=nodes,
            service_radius_km=2.0,
            min_facility_capacity=50,
        )
    )

    assert result.total_demand_nodes == 2
    assert result.covered_count == 1
    assert result.uncovered_count == 1
    assert result.coverage_ratio == pytest.approx(0.5)
    assert result.uncovered_demand_units == 60
    assert len(result.uncovered_nodes) == 1
    assert result.uncovered_nodes[0].demand_node.demand_id == "d-uncovered"
    assert result.uncovered_nodes[0].nearest_facility_id == "f-1"
    assert result.uncovered_nodes[0].nearest_distance_km is not None
    assert result.uncovered_nodes[0].nearest_distance_km > 2.0


def test_coverage_gap_no_qualifying_facilities() -> None:
    index = SpatialIndex()
    index.add_facilities(
        [_make_facility("f-small", "org-empty", 41.8781, -87.6298, capacity=10)]
    )
    nodes = [
        DemandNode(
            demand_id="d-1",
            org_id="org-empty",
            location=GeoPoint(lat=41.8781, lon=-87.6298),
            demand_units=25,
        )
    ]

    result = index.coverage_gap(
        CoverageGapRequest(
            org_id="org-empty",
            demand_nodes=nodes,
            service_radius_km=5.0,
            min_facility_capacity=100,
        )
    )
    assert result.covered_count == 0
    assert result.uncovered_count == 1
    assert result.coverage_ratio == 0.0
    assert result.uncovered_demand_units == 25
    assert result.uncovered_nodes[0].nearest_facility_id is None
    assert result.uncovered_nodes[0].nearest_distance_km is None


def test_tenant_isolation_strict() -> None:
    index = SpatialIndex()
    index.add_facilities(
        [
            _make_facility("f-alpha", "org-alpha", 41.8781, -87.6298, capacity=300),
            _make_facility("f-beta", "org-beta", 41.8785, -87.6299, capacity=300),
        ]
    )

    assert index.get_facility("org-alpha", "f-beta") is None
    assert index.get_facility("org-gamma", "f-alpha") is None

    nearest_alpha = index.nearest_facilities(
        NearestFacilitiesRequest(
            org_id="org-alpha",
            origin=GeoPoint(lat=41.8781, lon=-87.6298),
            limit=10,
        )
    )
    assert [m.facility.facility_id for m in nearest_alpha.matches] == ["f-alpha"]

    nearest_gamma = index.nearest_facilities(
        NearestFacilitiesRequest(
            org_id="org-gamma",
            origin=GeoPoint(lat=41.8781, lon=-87.6298),
            limit=10,
        )
    )
    assert nearest_gamma.matches == []

    with pytest.raises(TenantIsolationError):
        index.coverage_gap(
            CoverageGapRequest(
                org_id="org-alpha",
                demand_nodes=[
                    DemandNode(
                        demand_id="d-cross",
                        org_id="org-beta",
                        location=GeoPoint(lat=41.8781, lon=-87.6298),
                        demand_units=10,
                    )
                ],
                service_radius_km=5.0,
            )
        )


def test_remove_and_list_facilities() -> None:
    index = SpatialIndex()
    index.add_facilities(
        [
            _make_facility("f-b", "org-1", 41.88, -87.63, active=True),
            _make_facility("f-a", "org-1", 41.89, -87.64, active=False),
            _make_facility("f-c", "org-1", 41.90, -87.65, active=True),
            _make_facility("f-other", "org-2", 41.91, -87.66, active=True),
        ]
    )

    assert [f.facility_id for f in index.list_facilities("org-1")] == ["f-a", "f-b", "f-c"]
    assert [f.facility_id for f in index.list_facilities("org-1", only_active=True)] == [
        "f-b",
        "f-c",
    ]
    assert index.list_facilities("org-missing") == []

    # Remove missing facility / wrong org returns False
    assert index.remove_facility("org-1", "f-nonexistent") is False
    assert index.remove_facility("org-1", "f-other") is False
    assert index.remove_facility("org-missing", "f-a") is False

    # Remove existing facility returns True and updates counts + nearest queries
    assert index.remove_facility("org-1", "f-b") is True
    assert index.get_facility("org-1", "f-b") is None
    assert [f.facility_id for f in index.list_facilities("org-1")] == ["f-a", "f-c"]
    assert index.org_facility_counts() == {"org-1": 2, "org-2": 1}

    # Removing last facility for an org cleans up org entry
    assert index.remove_facility("org-2", "f-other") is True
    assert index.org_facility_counts() == {"org-1": 2}
    assert index.list_facilities("org-2") == []


def test_coverage_gap_all_qualifying_reuses_org_tree() -> None:
    index = SpatialIndex()
    index.add_facilities(
        [
            _make_facility("f-1", "org-all", 41.8781, -87.6298, capacity=200, active=True),
            _make_facility("f-2", "org-all", 41.9500, -87.6500, capacity=200, active=True),
        ]
    )
    nodes = [
        DemandNode(
            demand_id="d-1",
            org_id="org-all",
            location=GeoPoint(lat=41.8790, lon=-87.6300),
            demand_units=10,
        ),
        DemandNode(
            demand_id="d-2",
            org_id="org-all",
            location=GeoPoint(lat=42.1000, lon=-87.7000),
            demand_units=20,
        ),
    ]
    result = index.coverage_gap(
        CoverageGapRequest(
            org_id="org-all",
            demand_nodes=nodes,
            service_radius_km=2.0,
            min_facility_capacity=0,
        )
    )
    assert result.covered_count == 1
    assert result.uncovered_count == 1
    assert result.uncovered_nodes[0].nearest_facility_id == "f-2"


def test_snapshot_save_load_round_trip(tmp_path: Path) -> None:
    index = SpatialIndex(h3_resolution=7)
    index.add_facilities(
        [
            _make_facility("f-1", "org-a", 41.8781, -87.6298, capacity=100),
            _make_facility("f-2", "org-a", 41.8900, -87.6350, capacity=200, active=False),
            _make_facility("f-3", "org-b", 40.7128, -74.0060, capacity=300),
        ]
    )

    snapshot_path = tmp_path / "nested" / "snapshot.json"
    saved_count = index.save_snapshot(snapshot_path)
    assert saved_count == 3
    assert snapshot_path.exists()

    loaded = SpatialIndex.load_snapshot(snapshot_path)
    assert loaded.org_facility_counts() == {"org-a": 2, "org-b": 1}

    orig_f1 = index.get_facility("org-a", "f-1")
    loaded_f1 = loaded.get_facility("org-a", "f-1")
    assert orig_f1 is not None and loaded_f1 is not None
    assert loaded_f1 == orig_f1
    assert loaded_f1.h3_index == point_to_h3(loaded_f1.location, resolution=7)

    # Tenant isolation preserved after load
    assert loaded.get_facility("org-a", "f-3") is None
    nearest_b = loaded.nearest_facilities(
        NearestFacilitiesRequest(
            org_id="org-b",
            origin=GeoPoint(lat=40.7128, lon=-74.0060),
            limit=5,
        )
    )
    assert [m.facility.facility_id for m in nearest_b.matches] == ["f-3"]


@pytest.mark.parametrize(
    "content",
    [
        "{not valid json",
        json.dumps([1, 2, 3]),
        json.dumps({"version": 99, "h3_resolution": 8, "facilities": []}),
        json.dumps({"version": 1, "h3_resolution": "bad", "facilities": []}),
        json.dumps({"version": 1, "h3_resolution": 8, "facilities": "not-a-list"}),
        json.dumps(
            {
                "version": 1,
                "h3_resolution": 8,
                "facilities": [{"facility_id": "bad-fac"}],
            }
        ),
    ],
)
def test_load_snapshot_invalid_raises(tmp_path: Path, content: str) -> None:
    bad_path = tmp_path / "bad_snapshot.json"
    bad_path.write_text(content, encoding="utf-8")
    with pytest.raises(InvalidGeometryError):
        SpatialIndex.load_snapshot(bad_path)


def test_load_snapshot_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidGeometryError):
        SpatialIndex.load_snapshot(tmp_path / "does_not_exist.json")


def test_concurrent_add_and_nearest_stress() -> None:
    index = SpatialIndex()
    seed_facilities = generate_synthetic_facilities("org-stress", count=20, seed=42)
    index.add_facilities(seed_facilities)

    origin = GeoPoint(lat=41.8781, lon=-87.6298)
    errors: list[BaseException] = []

    def writer(worker_id: int) -> int:
        batch = [
            _make_facility(
                f"w{worker_id}-{i}",
                "org-stress",
                41.85 + (worker_id * 0.005) + (i * 0.001),
                -87.65 + (i * 0.001),
                capacity=100 + i,
            )
            for i in range(10)
        ]
        return index.add_facilities(batch)

    def reader() -> int:
        resp = index.nearest_facilities(
            NearestFacilitiesRequest(org_id="org-stress", origin=origin, limit=5)
        )
        assert len(resp.matches) >= 1
        return len(resp.matches)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures: list[concurrent.futures.Future[int]] = []
        for idx in range(16):
            if idx % 2 == 0:
                futures.append(executor.submit(writer, idx))
            else:
                futures.append(executor.submit(reader))

        for fut in futures:
            try:
                fut.result()
            except BaseException as exc:
                errors.append(exc)

    assert not errors
    # 20 seed + 8 writer batches * 10 facilities = 100
    assert index.org_facility_counts()["org-stress"] == 100
