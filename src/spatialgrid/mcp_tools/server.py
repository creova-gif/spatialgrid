"""Deterministic MCP tool implementations and MCPServer factory for SpatialGrid."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer

from spatialgrid.exceptions import SpatialGridError
from spatialgrid.index import SpatialIndex, generate_synthetic_facilities, load_facilities_geojson
from spatialgrid.models import (
    CoverageGapRequest,
    DemandNode,
    GeoPoint,
    IngestGeoJSONResponse,
    NearestFacilitiesRequest,
    OptimizeDispatchRequest,
    VehicleSpec,
)
from spatialgrid.solver import optimize_dispatch

logger = logging.getLogger("spatialgrid.mcp")


@contextmanager
def _log_tool_call(tool: str, org_id: str | None = None) -> Iterator[None]:
    """Emit structured observability logs for MCP tool invocations."""
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
        logger.warning(
            "tool=%s org_id=%s duration_ms=%.3f error_class=%s",
            tool,
            org_id or "-",
            duration_ms,
            type(exc).__name__,
            extra={
                "tool": tool,
                "org_id": org_id,
                "duration_ms": duration_ms,
                "error_class": type(exc).__name__,
            },
        )
        raise
    duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
    logger.info(
        "tool=%s org_id=%s duration_ms=%.3f",
        tool,
        org_id or "-",
        duration_ms,
        extra={
            "tool": tool,
            "org_id": org_id,
            "duration_ms": duration_ms,
        },
    )


def tool_healthcheck() -> dict[str, str]:
    """Return service health status for SpatialGrid."""
    with _log_tool_call("healthcheck"):
        return {"status": "ok", "service": "spatialgrid"}


def tool_nearest_facilities(
    index: SpatialIndex,
    org_id: str,
    lat: float,
    lon: float,
    limit: int = 5,
    max_distance_km: float | None = None,
    min_capacity: int = 0,
    facility_type: str | None = None,
) -> dict[str, Any]:
    """Find the nearest qualifying facilities to a geographic point."""
    with _log_tool_call("nearest_facilities", org_id=org_id):
        request = NearestFacilitiesRequest(
            org_id=org_id,
            origin=GeoPoint(lat=lat, lon=lon),
            limit=limit,
            max_distance_km=max_distance_km,
            min_capacity=min_capacity,
            facility_type=facility_type,
        )
        return index.nearest_facilities(request).model_dump()


def tool_coverage_gap(
    index: SpatialIndex,
    org_id: str,
    demand_nodes: list[dict[str, Any]],
    service_radius_km: float,
    min_facility_capacity: int = 0,
    facility_type: str | None = None,
) -> dict[str, Any]:
    """Identify demand nodes outside the service radius of qualifying facilities."""
    with _log_tool_call("coverage_gap", org_id=org_id):
        parsed_nodes = [DemandNode.model_validate(node) for node in demand_nodes]
        request = CoverageGapRequest(
            org_id=org_id,
            demand_nodes=parsed_nodes,
            service_radius_km=service_radius_km,
            min_facility_capacity=min_facility_capacity,
            facility_type=facility_type,
        )
        return index.coverage_gap(request).model_dump()


def tool_optimize_dispatch(
    index: SpatialIndex,
    org_id: str,
    depot_facility_id: str,
    vehicles: list[dict[str, Any]],
    stops: list[dict[str, Any]],
    time_limit_seconds: int = 5,
) -> dict[str, Any]:
    """Solve a Capacitated Vehicle Routing Problem (CVRP) from a depot facility."""
    with _log_tool_call("optimize_dispatch", org_id=org_id):
        depot = index.get_facility(org_id, depot_facility_id)
        if depot is None:
            raise SpatialGridError(
                f"Depot facility '{depot_facility_id}' not found for org '{org_id}'."
            )

        parsed_vehicles = [VehicleSpec.model_validate(vehicle) for vehicle in vehicles]
        parsed_stops = [DemandNode.model_validate(stop) for stop in stops]
        request = OptimizeDispatchRequest(
            org_id=org_id,
            depot_facility_id=depot_facility_id,
            vehicles=parsed_vehicles,
            stops=parsed_stops,
            time_limit_seconds=time_limit_seconds,
        )
        return optimize_dispatch(request, depot).model_dump()


def tool_ingest_geojson(
    index: SpatialIndex,
    org_id: str,
    geojson: dict[str, Any],
) -> dict[str, Any]:
    """Parse a GeoJSON FeatureCollection and index its facilities for an organization."""
    with _log_tool_call("ingest_geojson", org_id=org_id):
        facilities = load_facilities_geojson(geojson, default_org_id=org_id)
        ingested_count = index.add_facilities(facilities)
        total_org_facilities = index.org_facility_counts().get(org_id, 0)
        return IngestGeoJSONResponse(
            org_id=org_id,
            ingested_count=ingested_count,
            total_org_facilities=total_org_facilities,
        ).model_dump()


def tool_list_facilities(
    index: SpatialIndex,
    org_id: str,
    only_active: bool = False,
) -> dict[str, Any]:
    """List indexed facilities for an organization."""
    with _log_tool_call("list_facilities", org_id=org_id):
        return {
            "org_id": org_id,
            "facilities": [
                f.model_dump() for f in index.list_facilities(org_id, only_active=only_active)
            ],
        }


def tool_remove_facility(
    index: SpatialIndex,
    org_id: str,
    facility_id: str,
) -> dict[str, Any]:
    """Remove a facility from the spatial index for an organization."""
    with _log_tool_call("remove_facility", org_id=org_id):
        return {
            "org_id": org_id,
            "facility_id": facility_id,
            "removed": index.remove_facility(org_id, facility_id),
        }


def create_mcp_server(
    index: SpatialIndex | None = None,
    seed_demo: bool = False,
) -> MCPServer:
    """Create and configure the SpatialGrid MCPServer with all deterministic spatial tools."""
    active_index = index if index is not None else SpatialIndex()
    if seed_demo:
        active_index.add_facilities(
            generate_synthetic_facilities("demo-org", count=25, seed=42)
        )

    mcp = MCPServer("spatialgrid")

    @mcp.tool()
    def healthcheck() -> dict[str, str]:
        """Check SpatialGrid server health."""
        return tool_healthcheck()

    @mcp.tool()
    def nearest_facilities(
        org_id: str,
        lat: float,
        lon: float,
        limit: int = 5,
        max_distance_km: float | None = None,
        min_capacity: int = 0,
        facility_type: str | None = None,
    ) -> dict[str, Any]:
        """Find nearest facilities to (lat, lon) within optional distance/capacity/type filters."""
        return tool_nearest_facilities(
            index=active_index,
            org_id=org_id,
            lat=lat,
            lon=lon,
            limit=limit,
            max_distance_km=max_distance_km,
            min_capacity=min_capacity,
            facility_type=facility_type,
        )

    @mcp.tool()
    def coverage_gap(
        org_id: str,
        demand_nodes: list[dict[str, Any]],
        service_radius_km: float,
        min_facility_capacity: int = 0,
        facility_type: str | None = None,
    ) -> dict[str, Any]:
        """Compute uncovered demand nodes outside service_radius_km of qualifying facilities."""
        return tool_coverage_gap(
            index=active_index,
            org_id=org_id,
            demand_nodes=demand_nodes,
            service_radius_km=service_radius_km,
            min_facility_capacity=min_facility_capacity,
            facility_type=facility_type,
        )

    @mcp.tool()
    def optimize_dispatch(
        org_id: str,
        depot_facility_id: str,
        vehicles: list[dict[str, Any]],
        stops: list[dict[str, Any]],
        time_limit_seconds: int = 5,
    ) -> dict[str, Any]:
        """Solve capacitated vehicle routing (CVRP) for stops starting/ending at a depot."""
        return tool_optimize_dispatch(
            index=active_index,
            org_id=org_id,
            depot_facility_id=depot_facility_id,
            vehicles=vehicles,
            stops=stops,
            time_limit_seconds=time_limit_seconds,
        )

    @mcp.tool()
    def ingest_geojson(
        org_id: str,
        geojson: dict[str, Any],
    ) -> dict[str, Any]:
        """Ingest a GeoJSON FeatureCollection of Point facilities into the spatial index."""
        return tool_ingest_geojson(
            index=active_index,
            org_id=org_id,
            geojson=geojson,
        )

    @mcp.tool()
    def list_facilities(
        org_id: str,
        only_active: bool = False,
    ) -> dict[str, Any]:
        """List indexed facilities for an organization."""
        return tool_list_facilities(
            index=active_index,
            org_id=org_id,
            only_active=only_active,
        )

    @mcp.tool()
    def remove_facility(
        org_id: str,
        facility_id: str,
    ) -> dict[str, Any]:
        """Remove a facility from the spatial index for an organization."""
        return tool_remove_facility(
            index=active_index,
            org_id=org_id,
            facility_id=facility_id,
        )

    return mcp
