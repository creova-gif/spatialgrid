"""Deterministic OR-Tools Capacitated Vehicle Routing Problem (CVRP) solver."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from spatialgrid.exceptions import (
    SolverInfeasibleError,
    SpatialGridError,
    TenantIsolationError,
)
from spatialgrid.geo_utils import haversine_km
from spatialgrid.models import (
    DemandNode,
    Facility,
    GeoPoint,
    OptimizeDispatchRequest,
    OptimizeDispatchResponse,
    RouteStop,
    VehicleRoute,
)

_UNBOUNDED_DISTANCE_METERS = 10**12


def validate_dispatch_solution(
    routes: list[VehicleRoute],
    stops: list[DemandNode],
) -> tuple[bool, bool]:
    """Validate geometric completeness and capacity constraints of dispatch routes.

    Returns a tuple of ``(geometrically_valid, capacity_valid)``.
    """
    expected_ids = {stop.demand_id for stop in stops}
    visited_ids: list[str] = []
    capacity_valid = True
    geometry_ok = True

    for route in routes:
        if route.total_load > route.vehicle_capacity:
            capacity_valid = False
        prev_distance = 0.0
        for rstop in route.stops:
            visited_ids.append(rstop.demand_id)
            if rstop.cumulative_load > route.vehicle_capacity:
                capacity_valid = False
            if rstop.cumulative_distance_km < prev_distance:
                geometry_ok = False
            prev_distance = rstop.cumulative_distance_km
        if route.total_distance_km < prev_distance:
            geometry_ok = False

    all_visited_once = len(visited_ids) == len(stops) and set(visited_ids) == expected_ids
    geometrically_valid = geometry_ok and all_visited_once
    return geometrically_valid, capacity_valid


def _build_distance_matrix_meters(locations: list[GeoPoint]) -> list[list[int]]:
    """Build an integer-scaled (meters) great-circle distance matrix."""
    n = len(locations)
    matrix: list[list[int]] = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            meters = int(round(haversine_km(locations[i], locations[j]) * 1000.0))
            matrix[i][j] = meters
            matrix[j][i] = meters
    return matrix


def optimize_dispatch(
    request: OptimizeDispatchRequest,
    depot: Facility,
) -> OptimizeDispatchResponse:
    """Solve a Capacitated Vehicle Routing Problem (CVRP) using OR-Tools."""
    start_time = time.perf_counter()

    if depot.org_id != request.org_id:
        raise TenantIsolationError(
            f"Depot org_id '{depot.org_id}' does not match request org_id '{request.org_id}'."
        )
    for stop in request.stops:
        if stop.org_id != request.org_id:
            raise TenantIsolationError(
                f"Stop '{stop.demand_id}' org_id '{stop.org_id}' "
                f"does not match request org_id '{request.org_id}'."
            )

    if depot.facility_id != request.depot_facility_id:
        raise SpatialGridError(
            f"Depot facility_id '{depot.facility_id}' does not match "
            f"request depot_facility_id '{request.depot_facility_id}'."
        )

    total_capacity = sum(v.capacity for v in request.vehicles)
    total_demand = sum(s.demand_units for s in request.stops)
    if total_demand > total_capacity:
        raise SolverInfeasibleError(
            f"Total stop demand ({total_demand}) exceeds total fleet capacity ({total_capacity})."
        )

    locations: list[GeoPoint] = [depot.location, *(s.location for s in request.stops)]
    distance_matrix = _build_distance_matrix_meters(locations)
    demands: list[int] = [0, *(s.demand_units for s in request.stops)]

    num_vehicles = len(request.vehicles)
    manager = pywrapcp.RoutingIndexManager(len(locations), num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = int(manager.IndexToNode(from_index))
        to_node = int(manager.IndexToNode(to_index))
        return distance_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    def demand_callback(from_index: int) -> int:
        from_node = int(manager.IndexToNode(from_index))
        return demands[from_node]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    vehicle_capacities = [v.capacity for v in request.vehicles]
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        vehicle_capacities,
        True,
        "Capacity",
    )

    if any(v.max_distance_km is not None for v in request.vehicles):
        vehicle_max_meters = [
            int(round(v.max_distance_km * 1000.0))
            if v.max_distance_km is not None
            else _UNBOUNDED_DISTANCE_METERS
            for v in request.vehicles
        ]
        routing.AddDimensionWithVehicleCapacity(
            transit_callback_index,
            0,
            vehicle_max_meters,
            True,
            "Distance",
        )

    use_time_dimension = any(
        s.time_window_start_min is not None
        or s.time_window_end_min is not None
        or s.service_time_min > 0
        for s in request.stops
    ) or any(v.max_duration_min is not None for v in request.vehicles)

    time_dimension: Any = None
    if use_time_dimension:
        service_times = [0, *(s.service_time_min for s in request.stops)]

        def _make_time_callback(speed_kmh: float) -> Callable[[int, int], int]:
            def time_callback(from_index: int, to_index: int) -> int:
                from_node = int(manager.IndexToNode(from_index))
                to_node = int(manager.IndexToNode(to_index))
                dist_km = distance_matrix[from_node][to_node] / 1000.0
                travel_min = int(round((dist_km / speed_kmh) * 60.0))
                return travel_min + service_times[from_node]

            return time_callback

        time_callback_indices = [
            routing.RegisterTransitCallback(_make_time_callback(v.avg_speed_kmh))
            for v in request.vehicles
        ]
        max_horizon = max(
            10_000,
            *(s.time_window_end_min or 0 for s in request.stops),
            *(v.max_duration_min or 0 for v in request.vehicles),
        )
        routing.AddDimensionWithVehicleTransits(
            time_callback_indices,
            1440,
            max_horizon,
            True,
            "Time",
        )
        time_dimension = routing.GetDimensionOrDie("Time")

        for i, stop in enumerate(request.stops):
            if stop.time_window_start_min is not None or stop.time_window_end_min is not None:
                start = stop.time_window_start_min if stop.time_window_start_min is not None else 0
                end = (
                    stop.time_window_end_min
                    if stop.time_window_end_min is not None
                    else max_horizon
                )
                time_dimension.CumulVar(manager.NodeToIndex(i + 1)).SetRange(start, end)

        for v_idx, v in enumerate(request.vehicles):
            if v.max_duration_min is not None:
                time_dimension.CumulVar(routing.End(v_idx)).SetMax(v.max_duration_min)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.seconds = request.time_limit_seconds

    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        raise SolverInfeasibleError(
            "OR-Tools could not find a feasible dispatch solution within constraints."
        )

    routes: list[VehicleRoute] = []
    for vehicle_idx, vehicle in enumerate(request.vehicles):
        index = int(routing.Start(vehicle_idx))
        route_stops: list[RouteStop] = []
        prev_location = depot.location
        cumulative_load = 0
        cumulative_distance_km = 0.0

        while not routing.IsEnd(index):
            node_idx = int(manager.IndexToNode(index))
            if node_idx > 0:
                stop = request.stops[node_idx - 1]
                cumulative_distance_km += haversine_km(prev_location, stop.location)
                cumulative_load += stop.demand_units
                arrival_time_min = (
                    int(solution.Min(time_dimension.CumulVar(index)))
                    if time_dimension is not None
                    else None
                )
                route_stops.append(
                    RouteStop(
                        demand_id=stop.demand_id,
                        location=stop.location,
                        demand_units=stop.demand_units,
                        cumulative_load=cumulative_load,
                        cumulative_distance_km=round(cumulative_distance_km, 6),
                        arrival_time_min=arrival_time_min,
                    )
                )
                prev_location = stop.location
            index = int(solution.Value(routing.NextVar(index)))

        if not route_stops:
            continue

        total_route_distance_km = cumulative_distance_km + haversine_km(
            prev_location, depot.location
        )
        routes.append(
            VehicleRoute(
                vehicle_id=vehicle.vehicle_id,
                depot_facility_id=depot.facility_id,
                stops=route_stops,
                total_load=cumulative_load,
                vehicle_capacity=vehicle.capacity,
                total_distance_km=round(total_route_distance_km, 6),
            )
        )

    geometrically_valid, capacity_valid = validate_dispatch_solution(routes, request.stops)
    if not geometrically_valid or not capacity_valid:
        raise SolverInfeasibleError(
            "Solved routes failed post-solve geometric or capacity validation."
        )

    optimal_code = int(getattr(pywrapcp.RoutingModel, "ROUTING_OPTIMAL", 2))
    solver_status: Literal["OPTIMAL", "FEASIBLE"] = (
        "OPTIMAL" if int(routing.status()) == optimal_code else "FEASIBLE"
    )
    total_distance_km = round(sum(r.total_distance_km for r in routes), 6)
    total_demand_served = sum(r.total_load for r in routes)
    solve_time_ms = round((time.perf_counter() - start_time) * 1000.0, 3)

    return OptimizeDispatchResponse(
        org_id=request.org_id,
        depot_facility_id=request.depot_facility_id,
        solver_status=solver_status,
        routes=routes,
        total_distance_km=total_distance_km,
        total_demand_served=total_demand_served,
        geometrically_valid=geometrically_valid,
        capacity_valid=capacity_valid,
        solve_time_ms=solve_time_ms,
    )


def routes_to_geojson(
    response: OptimizeDispatchResponse,
    depot: Facility,
) -> dict[str, Any]:
    """Convert solved dispatch routes and depot into a GeoJSON FeatureCollection."""
    depot_coord = [depot.location.lon, depot.location.lat]
    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": depot_coord,
            },
            "properties": {
                "role": "depot",
                "facility_id": depot.facility_id,
                "name": depot.name,
            },
        }
    ]

    for route in response.routes:
        line_coords = [
            depot_coord,
            *([stop.location.lon, stop.location.lat] for stop in route.stops),
            depot_coord,
        ]
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": line_coords,
                },
                "properties": {
                    "role": "route",
                    "vehicle_id": route.vehicle_id,
                    "total_load": route.total_load,
                    "total_distance_km": route.total_distance_km,
                },
            }
        )
        for stop in route.stops:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [stop.location.lon, stop.location.lat],
                    },
                    "properties": {
                        "role": "stop",
                        "vehicle_id": route.vehicle_id,
                        "demand_id": stop.demand_id,
                        "cumulative_load": stop.cumulative_load,
                        "arrival_time_min": stop.arrival_time_min,
                    },
                }
            )

    return {
        "type": "FeatureCollection",
        "features": features,
    }
