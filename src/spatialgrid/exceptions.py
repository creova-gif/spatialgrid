"""Domain exceptions for SpatialGrid."""


class SpatialGridError(Exception):
    """Base exception for all SpatialGrid errors."""


class TenantIsolationError(SpatialGridError):
    """Raised when an operation violates org_id tenant isolation."""


class InvalidGeometryError(SpatialGridError):
    """Raised when coordinates, polygons, or GeoJSON payloads are invalid."""


class SolverInfeasibleError(SpatialGridError):
    """Raised when OR-Tools cannot find a feasible dispatch plan."""
