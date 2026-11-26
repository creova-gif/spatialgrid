"""OR-Tools CVRP solver and heuristic baselines for SpatialGrid."""

from spatialgrid.solver.baseline import (
    naive_llm_guess_dispatch,
    nearest_neighbor_heuristic,
)
from spatialgrid.solver.dispatch import (
    optimize_dispatch,
    routes_to_geojson,
    validate_dispatch_solution,
)

__all__ = [
    "naive_llm_guess_dispatch",
    "nearest_neighbor_heuristic",
    "optimize_dispatch",
    "routes_to_geojson",
    "validate_dispatch_solution",
]
