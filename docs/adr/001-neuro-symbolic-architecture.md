# ADR-001: Neuro-Symbolic Spatial Reasoning Architecture
- **Status:** Accepted
- **Owner:** Justin Mafie

## Context
Large Language Models (LLMs) excel at interpreting unstructured natural-language operational intent (e.g., *"find the 3 nearest cold-storage depots with at least 200 units of capacity within 15 km"* or *"dispatch 3 trucks to 12 stops without exceeding vehicle capacity"*), but they are unreliable when asked to perform raw geometric calculations, great-circle distance ranking, or combinatorial routing directly in token space. Unassisted LLM spatial reasoning frequently hallucinates coordinates, violates capacity or radius constraints, produces crossing or incomplete routes, and offers no deterministic auditability.

SpatialGrid targets logistics, fleet dispatch, warehouse automation, and utility operations planners who require 100% geometrically valid routes, strict capacity enforcement, multi-tenant isolation, and sub-100ms spatial query latency.

## Decision
Adopt a **neuro-symbolic architecture** that separates natural-language intent translation from deterministic spatial and combinatorial execution:

1. **Symbolic Input/Output Contracts (Pydantic v2):** Every tool invocation (`nearest_facilities`, `coverage_gap`, `optimize_dispatch`) is validated at the boundary using strict Pydantic v2 schemas (`src/spatialgrid/models.py`) with coordinate bounds, non-negative capacity/demand invariants, and explicit `org_id` tenant scoping.
2. **Hierarchical & R-tree Spatial Indexing (`h3` + `shapely.STRtree`):** Facility locations are indexed per organization into isolated `STRtree` spatial indices alongside H3 cell IDs (`DEFAULT_H3_RESOLUTION = 7`). Candidate bounding-box filtering is followed by exact WGS84 great-circle (`haversine_km`) ranking and attribute filtering.
3. **Deterministic Combinatorial Optimization (`ortools`):** Vehicle routing (`optimize_dispatch`) is solved via Google OR-Tools CVRP (`PATH_CHEAPEST_ARC` + `GUIDED_LOCAL_SEARCH`) over an exact integer-meter great-circle distance matrix, followed by post-solve geometric and capacity verification (`validate_dispatch_solution`).
4. **MCP Tool & CLI Surface:** Deterministic tools are exposed over Model Context Protocol (MCP) and the `spatialgrid` CLI so any LLM agent can delegate spatial math without touching raw solver internals.

## Alternatives Considered
1. **Pure End-to-End LLM / VLM Spatial Reasoning:** Prompting an LLM directly with facility coordinates and stop lists to output JSON routes. Rejected because raw LLM guessing violates capacity constraints, misranks geodesic distances, and cannot guarantee route completeness.
2. **External PostGIS / Managed Cloud Routing API Only:** Requiring a running PostgreSQL/PostGIS instance or paid third-party routing API for core indexing and CVRP. Rejected for the core engine because an in-process H3 + STRtree + OR-Tools kernel enables zero-dependency local/CI verification, deterministic benchmarks, and sub-100ms spatial queries while keeping storage adapters pluggable.
3. **Greedy Nearest-Neighbor Heuristics Only:** Using a simple greedy heuristic instead of OR-Tools for dispatch. Rejected because greedy routing leaves significant optimality gaps on clustered multi-vehicle problems and struggles with tight capacity/distance constraints.

## Consequences & Failure Modes
- **Positive Consequences:**
  - 100% geometric and capacity validity on all solved dispatch and spatial queries, verified post-solve.
  - Strict tenant isolation (`TenantIsolationError`) enforced at both index and solver boundaries.
  - Sub-100ms p95 latency for `nearest_facilities` and `coverage_gap` spatial queries.
  - Reproducible, seeded benchmark suite (`run_benchmark_suite`) proving accuracy and latency against naive baselines.
- **Failure Modes & Mitigations:**
  - *Infeasible constraints:* When total stop demand exceeds fleet capacity or `max_distance_km` is unreachable, OR-Tools cannot produce a valid route. Mitigated by explicit pre-solve capacity checks and `SolverInfeasibleError` with actionable diagnostics.
  - *Solver time budget vs. latency:* OR-Tools `GUIDED_LOCAL_SEARCH` runs up to `time_limit_seconds` (minimum 1s). Mitigated by bounding `time_limit_seconds` per request and separating fast spatial query latency metrics from combinatorial solve latency.
  - *Great-circle vs. road-network distance:* `haversine_km` computes exact geodesic distance rather than road-network travel time; future iterations can swap the distance matrix builder for OSRM/Valhalla road matrices without changing the Pydantic tool contracts.
