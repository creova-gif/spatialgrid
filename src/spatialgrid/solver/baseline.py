"""Naive and greedy heuristic dispatch baselines for benchmarking."""

from __future__ import annotations

from spatialgrid.geo_utils import haversine_km
from spatialgrid.models import (
    DemandNode,
    Facility,
    OptimizeDispatchRequest,
    RouteStop,
    VehicleRoute,
    VehicleSpec,
)


def _build_vehicle_route(
    vehicle: VehicleSpec,
    depot: Facility,
    assigned_stops: list[DemandNode],
) -> VehicleRoute:
    """Build a VehicleRoute with exact geodesic distances along depot -> stops -> depot."""
    route_stops: list[RouteStop] = []
    prev_location = depot.location
    cumulative_load = 0
    cumulative_distance_km = 0.0

    for stop in assigned_stops:
        cumulative_distance_km += haversine_km(prev_location, stop.location)
        cumulative_load += stop.demand_units
        route_stops.append(
            RouteStop(
                demand_id=stop.demand_id,
                location=stop.location,
                demand_units=stop.demand_units,
                cumulative_load=cumulative_load,
                cumulative_distance_km=round(cumulative_distance_km, 6),
            )
        )
        prev_location = stop.location

    total_distance_km = cumulative_distance_km + haversine_km(prev_location, depot.location)
    return VehicleRoute(
        vehicle_id=vehicle.vehicle_id,
        depot_facility_id=depot.facility_id,
        stops=route_stops,
        total_load=cumulative_load,
        vehicle_capacity=vehicle.capacity,
        total_distance_km=round(total_distance_km, 6),
    )


def naive_llm_guess_dispatch(
    request: OptimizeDispatchRequest,
    depot: Facility,
) -> list[VehicleRoute]:
    """Deterministic naive round-robin baseline ignoring distance and capacity."""
    buckets: list[list[DemandNode]] = [[] for _ in request.vehicles]
    num_vehicles = len(request.vehicles)
    for idx, stop in enumerate(request.stops):
        buckets[idx % num_vehicles].append(stop)

    return [
        _build_vehicle_route(vehicle, depot, assigned)
        for vehicle, assigned in zip(request.vehicles, buckets, strict=True)
        if assigned
    ]


def nearest_neighbor_heuristic(
    request: OptimizeDispatchRequest,
    depot: Facility,
) -> list[VehicleRoute]:
    """Greedy nearest-neighbor heuristic respecting vehicle capacity and max distance."""
    unvisited = list(request.stops)
    routes: list[VehicleRoute] = []

    for vehicle in request.vehicles:
        if not unvisited:
            break

        assigned: list[DemandNode] = []
        current_loc = depot.location
        remaining_capacity = vehicle.capacity
        traveled_km = 0.0

        while unvisited:
            best_idx: int | None = None
            best_dist = float("inf")

            for idx, candidate in enumerate(unvisited):
                if candidate.demand_units > remaining_capacity:
                    continue
                step_km = haversine_km(current_loc, candidate.location)
                if vehicle.max_distance_km is not None:
                    return_km = haversine_km(candidate.location, depot.location)
                    if traveled_km + step_km + return_km > vehicle.max_distance_km:
                        continue
                if step_km < best_dist:
                    best_dist = step_km
                    best_idx = idx

            if best_idx is None:
                break

            chosen = unvisited.pop(best_idx)
            assigned.append(chosen)
            traveled_km += best_dist
            remaining_capacity -= chosen.demand_units
            current_loc = chosen.location

        if assigned:
            routes.append(_build_vehicle_route(vehicle, depot, assigned))

    return routes
