"""Unit tests for the SpatialGrid OR-Tools CVRP solver and baselines."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from spatialgrid.exceptions import (
    SolverInfeasibleError,
    SpatialGridError,
    TenantIsolationError,
)
from spatialgrid.models import (
    DemandNode,
    Facility,
    GeoPoint,
    OptimizeDispatchRequest,
    VehicleSpec,
)
from spatialgrid.solver import (
    naive_llm_guess_dispatch,
    nearest_neighbor_heuristic,
    optimize_dispatch,
    routes_to_geojson,
    validate_dispatch_solution,
)


def _make_depot(org_id: str = "org-1", facility_id: str = "depot-sf") -> Facility:
    return Facility(
        facility_id=facility_id,
        org_id=org_id,
        name="SF Central Depot",
        location=GeoPoint(lat=37.7749, lon=-122.4194),
        capacity=1000,
        facility_type="depot",
    )


def _make_clustered_stops(org_id: str = "org-1") -> list[DemandNode]:
    # Interleaved north (Marin/Berkeley) and south (San Mateo/Palo Alto) stops so
    # naive round-robin interleaving crisscrosses the Bay Area while OR-Tools clusters.
    return [
        DemandNode(
            demand_id="stop-north-1",
            org_id=org_id,
            location=GeoPoint(lat=37.8715, lon=-122.2730),  # Berkeley
            demand_units=3,
        ),
        DemandNode(
            demand_id="stop-south-1",
            org_id=org_id,
            location=GeoPoint(lat=37.5630, lon=-122.3255),  # San Mateo
            demand_units=4,
        ),
        DemandNode(
            demand_id="stop-north-2",
            org_id=org_id,
            location=GeoPoint(lat=37.9060, lon=-122.5450),  # Mill Valley
            demand_units=2,
        ),
        DemandNode(
            demand_id="stop-south-2",
            org_id=org_id,
            location=GeoPoint(lat=37.4419, lon=-122.1430),  # Palo Alto
            demand_units=5,
        ),
        DemandNode(
            demand_id="stop-north-3",
            org_id=org_id,
            location=GeoPoint(lat=37.9735, lon=-122.5311),  # San Rafael
            demand_units=3,
        ),
        DemandNode(
            demand_id="stop-south-3",
            org_id=org_id,
            location=GeoPoint(lat=37.3861, lon=-122.0839),  # Mountain View
            demand_units=4,
        ),
    ]


def test_optimize_dispatch_multi_vehicle_valid() -> None:
    depot = _make_depot()
    stops = _make_clustered_stops()
    vehicles = [
        VehicleSpec(vehicle_id="veh-1", capacity=10),
        VehicleSpec(vehicle_id="veh-2", capacity=15),
    ]
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=vehicles,
        stops=stops,
        time_limit_seconds=3,
    )

    response = optimize_dispatch(request, depot)

    assert response.org_id == "org-1"
    assert response.depot_facility_id == "depot-sf"
    assert response.solver_status in ("OPTIMAL", "FEASIBLE")
    assert response.geometrically_valid is True
    assert response.capacity_valid is True
    assert response.total_demand_served == sum(s.demand_units for s in stops)
    assert response.total_distance_km > 0.0
    assert len(response.routes) == 2

    visited = [rs.demand_id for r in response.routes for rs in r.stops]
    assert sorted(visited) == sorted(s.demand_id for s in stops)
    for route in response.routes:
        assert route.total_load <= route.vehicle_capacity


def test_optimize_dispatch_respects_max_distance_km() -> None:
    depot = _make_depot()
    stops = [
        DemandNode(
            demand_id="near-1",
            org_id="org-1",
            location=GeoPoint(lat=37.7849, lon=-122.4094),
            demand_units=2,
        ),
        DemandNode(
            demand_id="far-1",
            org_id="org-1",
            location=GeoPoint(lat=37.4419, lon=-122.1430),
            demand_units=2,
        ),
    ]
    vehicles = [
        VehicleSpec(vehicle_id="short-range", capacity=10, max_distance_km=10.0),
        VehicleSpec(vehicle_id="long-range", capacity=10, max_distance_km=150.0),
    ]
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=vehicles,
        stops=stops,
        time_limit_seconds=3,
    )

    response = optimize_dispatch(request, depot)
    assert response.geometrically_valid is True
    assert response.capacity_valid is True

    by_vehicle = {r.vehicle_id: r for r in response.routes}
    if "short-range" in by_vehicle:
        assert by_vehicle["short-range"].total_distance_km <= 10.0
    assert "long-range" in by_vehicle
    assert any(s.demand_id == "far-1" for s in by_vehicle["long-range"].stops)


def test_optimize_dispatch_infeasible_capacity() -> None:
    depot = _make_depot()
    stops = _make_clustered_stops()  # total demand = 21
    vehicles = [
        VehicleSpec(vehicle_id="veh-1", capacity=10),
        VehicleSpec(vehicle_id="veh-2", capacity=10),
    ]
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=vehicles,
        stops=stops,
    )

    with pytest.raises(SolverInfeasibleError, match="exceeds total fleet capacity"):
        optimize_dispatch(request, depot)


def test_optimize_dispatch_tenant_isolation_depot_mismatch() -> None:
    depot = _make_depot(org_id="org-other")
    stops = _make_clustered_stops(org_id="org-1")
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[VehicleSpec(vehicle_id="veh-1", capacity=50)],
        stops=stops,
    )

    with pytest.raises(TenantIsolationError):
        optimize_dispatch(request, depot)


def test_optimize_dispatch_tenant_isolation_stop_mismatch() -> None:
    depot = _make_depot(org_id="org-1")
    stops = _make_clustered_stops(org_id="org-1")
    stops[2] = DemandNode(
        demand_id="rogue-stop",
        org_id="org-2",
        location=GeoPoint(lat=37.8, lon=-122.4),
        demand_units=1,
    )
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[VehicleSpec(vehicle_id="veh-1", capacity=50)],
        stops=stops,
    )

    with pytest.raises(TenantIsolationError):
        optimize_dispatch(request, depot)


def test_optimize_dispatch_depot_facility_id_mismatch() -> None:
    depot = _make_depot(facility_id="depot-oak")
    stops = _make_clustered_stops()
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[VehicleSpec(vehicle_id="veh-1", capacity=50)],
        stops=stops,
    )

    with pytest.raises(SpatialGridError):
        optimize_dispatch(request, depot)


def test_ortools_beats_naive_llm_guess_on_distance_and_validity() -> None:
    depot = _make_depot()
    # With capacities [13, 8], naive round-robin puts load 8 on veh-1 and 13 on veh-2,
    # violating veh-2's capacity of 8.
    stops = _make_clustered_stops()
    tight_vehicles = [
        VehicleSpec(vehicle_id="veh-1", capacity=13),
        VehicleSpec(vehicle_id="veh-2", capacity=8),
    ]
    tight_request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=tight_vehicles,
        stops=stops,
        time_limit_seconds=3,
    )

    ortools_resp = optimize_dispatch(tight_request, depot)
    naive_routes = naive_llm_guess_dispatch(tight_request, depot)
    naive_geom_valid, naive_cap_valid = validate_dispatch_solution(naive_routes, stops)

    assert ortools_resp.geometrically_valid is True
    assert ortools_resp.capacity_valid is True
    assert naive_geom_valid is True
    assert naive_cap_valid is False

    # Also compare on distance when capacities are equal (15 each)
    # Reorder stops so round-robin crisscrosses north and south on both vehicles
    crisscross_stops = [stops[0], stops[2], stops[1], stops[3], stops[4], stops[5]]
    equal_request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[
            VehicleSpec(vehicle_id="veh-1", capacity=15),
            VehicleSpec(vehicle_id="veh-2", capacity=15),
        ],
        stops=crisscross_stops,
        time_limit_seconds=3,
    )
    ortools_eq = optimize_dispatch(equal_request, depot)
    naive_eq = naive_llm_guess_dispatch(equal_request, depot)
    naive_eq_dist = sum(r.total_distance_km for r in naive_eq)
    assert ortools_eq.total_distance_km < naive_eq_dist

    nn_routes = nearest_neighbor_heuristic(equal_request, depot)
    nn_geom_valid, nn_cap_valid = validate_dispatch_solution(nn_routes, crisscross_stops)
    assert nn_geom_valid is True
    assert nn_cap_valid is True
    nn_dist = sum(r.total_distance_km for r in nn_routes)
    assert ortools_eq.total_distance_km <= nn_dist + 1e-6


def test_optimize_dispatch_infeasible_max_distance_km() -> None:
    depot = _make_depot()
    far_stop = DemandNode(
        demand_id="far-only",
        org_id="org-1",
        location=GeoPoint(lat=37.4419, lon=-122.1430),  # ~44 km from SF depot
        demand_units=2,
    )
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[VehicleSpec(vehicle_id="veh-short", capacity=10, max_distance_km=5.0)],
        stops=[far_stop],
        time_limit_seconds=2,
    )

    with pytest.raises(SolverInfeasibleError):
        optimize_dispatch(request, depot)


def test_optimize_dispatch_rejects_duplicate_demand_id() -> None:
    stops = [
        DemandNode(
            demand_id="dup-1",
            org_id="org-1",
            location=GeoPoint(lat=37.78, lon=-122.41),
            demand_units=2,
        ),
        DemandNode(
            demand_id="dup-1",
            org_id="org-1",
            location=GeoPoint(lat=37.79, lon=-122.42),
            demand_units=3,
        ),
    ]
    with pytest.raises(ValidationError, match="Duplicate demand_id"):
        OptimizeDispatchRequest(
            org_id="org-1",
            depot_facility_id="depot-sf",
            vehicles=[VehicleSpec(vehicle_id="veh-1", capacity=10)],
            stops=stops,
        )


def test_demand_node_rejects_inverted_time_window() -> None:
    with pytest.raises(ValidationError, match="time_window_start_min"):
        DemandNode(
            demand_id="bad-tw",
            org_id="org-1",
            location=GeoPoint(lat=37.78, lon=-122.41),
            demand_units=2,
            time_window_start_min=60,
            time_window_end_min=30,
        )


def test_optimize_dispatch_respects_time_windows() -> None:
    depot = _make_depot()
    stops = [
        DemandNode(
            demand_id="early-stop",
            org_id="org-1",
            location=GeoPoint(lat=37.7849, lon=-122.4094),  # ~1.4 km from depot
            demand_units=2,
            time_window_start_min=0,
            time_window_end_min=30,
            service_time_min=10,
        ),
        DemandNode(
            demand_id="late-stop",
            org_id="org-1",
            location=GeoPoint(lat=37.8715, lon=-122.2730),  # Berkeley, ~16.8 km
            demand_units=3,
            time_window_start_min=30,
            time_window_end_min=120,
            service_time_min=5,
        ),
    ]
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[VehicleSpec(vehicle_id="veh-1", capacity=10, avg_speed_kmh=40.0)],
        stops=stops,
        time_limit_seconds=3,
    )

    response = optimize_dispatch(request, depot)
    assert response.geometrically_valid is True
    assert response.capacity_valid is True
    assert len(response.routes) == 1

    route_stops = {s.demand_id: s for s in response.routes[0].stops}
    early = route_stops["early-stop"]
    late = route_stops["late-stop"]
    assert early.arrival_time_min is not None
    assert 0 <= early.arrival_time_min <= 30
    assert late.arrival_time_min is not None
    assert 30 <= late.arrival_time_min <= 120


def test_optimize_dispatch_infeasible_time_window() -> None:
    depot = _make_depot()
    distant_stop = DemandNode(
        demand_id="impossible-tw",
        org_id="org-1",
        location=GeoPoint(lat=37.4419, lon=-122.1430),  # Palo Alto, ~44 km (~66 min at 40 km/h)
        demand_units=2,
        time_window_start_min=0,
        time_window_end_min=1,
    )
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[VehicleSpec(vehicle_id="veh-1", capacity=10, avg_speed_kmh=40.0)],
        stops=[distant_stop],
        time_limit_seconds=2,
    )

    with pytest.raises(SolverInfeasibleError):
        optimize_dispatch(request, depot)


def test_routes_to_geojson_structure() -> None:
    depot = _make_depot()
    stops = [
        DemandNode(
            demand_id="stop-1",
            org_id="org-1",
            location=GeoPoint(lat=37.7849, lon=-122.4094),
            demand_units=2,
            time_window_start_min=0,
            time_window_end_min=60,
        ),
        DemandNode(
            demand_id="stop-2",
            org_id="org-1",
            location=GeoPoint(lat=37.5630, lon=-122.3255),
            demand_units=3,
            time_window_start_min=10,
            time_window_end_min=120,
        ),
    ]
    request = OptimizeDispatchRequest(
        org_id="org-1",
        depot_facility_id="depot-sf",
        vehicles=[VehicleSpec(vehicle_id="veh-1", capacity=10)],
        stops=stops,
        time_limit_seconds=3,
    )
    response = optimize_dispatch(request, depot)
    geojson = routes_to_geojson(response, depot)

    assert geojson["type"] == "FeatureCollection"
    features = geojson["features"]
    depot_features = [f for f in features if f["properties"].get("role") == "depot"]
    line_features = [f for f in features if f["geometry"]["type"] == "LineString"]
    stop_features = [f for f in features if f["properties"].get("role") == "stop"]

    assert len(depot_features) == 1
    assert depot_features[0]["geometry"] == {
        "type": "Point",
        "coordinates": [depot.location.lon, depot.location.lat],
    }

    assert len(line_features) == len(response.routes) == 1
    line_props = line_features[0]["properties"]
    assert line_props["vehicle_id"] == "veh-1"
    assert line_props["total_load"] == 5
    assert line_props["total_distance_km"] == response.routes[0].total_distance_km
    coords = line_features[0]["geometry"]["coordinates"]
    assert coords[0] == [depot.location.lon, depot.location.lat]
    assert coords[-1] == [depot.location.lon, depot.location.lat]
    assert len(coords) == len(response.routes[0].stops) + 2

    assert len(stop_features) == 2
    for sf in stop_features:
        assert sf["geometry"]["type"] == "Point"
        assert sf["properties"]["vehicle_id"] == "veh-1"
        assert sf["properties"]["demand_id"] in {"stop-1", "stop-2"}
        assert isinstance(sf["properties"]["cumulative_load"], int)
        assert sf["properties"]["arrival_time_min"] is not None
