"""Shared Pydantic v2 domain models for SpatialGrid."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class GeoPoint(BaseModel):
    """WGS84 geographic coordinate."""

    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees.")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees.")


class Facility(BaseModel):
    """A physical facility (warehouse, depot, substation, hub) indexed in SpatialGrid."""

    facility_id: str = Field(..., min_length=1, max_length=128)
    org_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    location: GeoPoint
    capacity: int = Field(..., ge=0, description="Available capacity units.")
    facility_type: str = Field(default="warehouse", min_length=1, max_length=64)
    active: bool = Field(default=True)
    h3_index: str | None = Field(default=None, description="H3 cell ID at configured resolution.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DemandNode(BaseModel):
    """A customer, stop, or service demand location."""

    demand_id: str = Field(..., min_length=1, max_length=128)
    org_id: str = Field(..., min_length=1, max_length=128)
    location: GeoPoint
    demand_units: int = Field(..., ge=0)
    priority: int = Field(default=1, ge=1, le=10)
    time_window_start_min: int | None = Field(default=None, ge=0)
    time_window_end_min: int | None = Field(default=None, ge=0)
    service_time_min: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_time_window(self) -> DemandNode:
        if (
            self.time_window_start_min is not None
            and self.time_window_end_min is not None
            and self.time_window_start_min > self.time_window_end_min
        ):
            raise ValueError(
                f"time_window_start_min ({self.time_window_start_min}) must be <= "
                f"time_window_end_min ({self.time_window_end_min})"
            )
        return self


class VehicleSpec(BaseModel):
    """A dispatch vehicle starting and ending at a depot facility."""

    vehicle_id: str = Field(..., min_length=1, max_length=128)
    capacity: int = Field(..., gt=0)
    max_distance_km: float | None = Field(default=None, gt=0.0)
    avg_speed_kmh: float = Field(default=40.0, gt=0.0)
    max_duration_min: int | None = Field(default=None, gt=0)


class NearestFacilitiesRequest(BaseModel):
    """Input contract for the nearest_facilities deterministic tool."""

    org_id: str = Field(..., min_length=1)
    origin: GeoPoint
    limit: int = Field(default=5, ge=1, le=100)
    max_distance_km: float | None = Field(default=None, gt=0.0)
    min_capacity: int = Field(default=0, ge=0)
    facility_type: str | None = Field(default=None)
    only_active: bool = Field(default=True)


class FacilityMatch(BaseModel):
    """A ranked facility match with exact geodesic distance."""

    facility: Facility
    distance_km: float = Field(..., ge=0.0)
    h3_ring_distance: int | None = Field(default=None, ge=0)


class IngestGeoJSONResponse(BaseModel):
    """Output contract for ingest_geojson."""

    org_id: str
    ingested_count: int = Field(..., ge=0)
    total_org_facilities: int = Field(..., ge=0)


class NearestFacilitiesResponse(BaseModel):
    """Output contract for nearest_facilities."""

    org_id: str
    origin: GeoPoint
    matches: list[FacilityMatch]
    query_time_ms: float = Field(..., ge=0.0)


class CoverageGapRequest(BaseModel):
    """Input contract for the coverage_gap deterministic tool."""

    org_id: str = Field(..., min_length=1)
    demand_nodes: list[DemandNode] = Field(..., min_length=1, max_length=10_000)
    service_radius_km: float = Field(..., gt=0.0)
    min_facility_capacity: int = Field(default=0, ge=0)
    facility_type: str | None = Field(default=None)


class UncoveredDemand(BaseModel):
    """A demand node outside the service radius of any qualifying facility."""

    demand_node: DemandNode
    nearest_facility_id: str | None = None
    nearest_distance_km: float | None = Field(default=None, ge=0.0)


class CoverageGapResponse(BaseModel):
    """Output contract for coverage_gap."""

    org_id: str
    total_demand_nodes: int = Field(..., ge=0)
    covered_count: int = Field(..., ge=0)
    uncovered_count: int = Field(..., ge=0)
    coverage_ratio: float = Field(..., ge=0.0, le=1.0)
    uncovered_demand_units: int = Field(..., ge=0)
    uncovered_nodes: list[UncoveredDemand]
    query_time_ms: float = Field(..., ge=0.0)


class OptimizeDispatchRequest(BaseModel):
    """Input contract for the optimize_dispatch deterministic OR-Tools CVRP solver."""

    org_id: str = Field(..., min_length=1)
    depot_facility_id: str = Field(..., min_length=1)
    vehicles: list[VehicleSpec] = Field(..., min_length=1, max_length=100)
    stops: list[DemandNode] = Field(..., min_length=1, max_length=500)
    time_limit_seconds: int = Field(default=5, ge=1, le=60)

    @field_validator("stops")
    @classmethod
    def _validate_same_org_stops(cls, stops: list[DemandNode]) -> list[DemandNode]:
        if not stops:
            raise ValueError("stops must not be empty")
        seen_ids: set[str] = set()
        for stop in stops:
            if stop.demand_id in seen_ids:
                raise ValueError(f"Duplicate demand_id in stops: '{stop.demand_id}'")
            seen_ids.add(stop.demand_id)
        return stops


class RouteStop(BaseModel):
    """A single ordered stop on a vehicle route."""

    demand_id: str
    location: GeoPoint
    demand_units: int = Field(..., ge=0)
    cumulative_load: int = Field(..., ge=0)
    cumulative_distance_km: float = Field(..., ge=0.0)
    arrival_time_min: int | None = Field(default=None, ge=0)


class VehicleRoute(BaseModel):
    """A solved, geometrically valid vehicle route."""

    vehicle_id: str
    depot_facility_id: str
    stops: list[RouteStop]
    total_load: int = Field(..., ge=0)
    vehicle_capacity: int = Field(..., gt=0)
    total_distance_km: float = Field(..., ge=0.0)


class OptimizeDispatchResponse(BaseModel):
    """Output contract for optimize_dispatch."""

    org_id: str
    depot_facility_id: str
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    routes: list[VehicleRoute]
    total_distance_km: float = Field(..., ge=0.0)
    total_demand_served: int = Field(..., ge=0)
    geometrically_valid: bool
    capacity_valid: bool
    solve_time_ms: float = Field(..., ge=0.0)


class BenchmarkCaseResult(BaseModel):
    """Result for a single query in the 20-query benchmark suite."""

    case_id: str
    category: Literal["nearest_facilities", "coverage_gap", "optimize_dispatch"]
    spatialgrid_valid: bool
    baseline_valid: bool
    spatialgrid_latency_ms: float = Field(..., ge=0.0)
    optimality_gap_pct: float | None = None


class BenchmarkSummary(BaseModel):
    """Aggregated metrics from the 20-query benchmark suite."""

    total_queries: int = Field(..., ge=1)
    spatialgrid_geometric_validity_pct: float = Field(..., ge=0.0, le=100.0)
    baseline_geometric_validity_pct: float = Field(..., ge=0.0, le=100.0)
    p95_latency_ms: float = Field(..., ge=0.0)
    mean_optimality_gap_pct: float = Field(..., ge=0.0)
    max_optimality_gap_pct: float = Field(..., ge=0.0)
    cases: list[BenchmarkCaseResult]
