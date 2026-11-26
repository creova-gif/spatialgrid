# Changelog

All notable changes to SpatialGrid will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-26

### Added
- **Domain Models (`spatialgrid.models`):** Strict Pydantic v2 contracts for `GeoPoint`, `Facility`, `DemandNode`, `VehicleSpec`, `NearestFacilitiesRequest`/`Response`, `CoverageGapRequest`/`Response`, `OptimizeDispatchRequest`/`Response`, and `BenchmarkSummary`.
- **Spatial Indexing (`spatialgrid.index`):** Tenant-isolated `SpatialIndex` combining H3 cell indexing (`h3`) and `shapely.STRtree` R-tree bounding-box queries with exact WGS84 `haversine_km` ranking, GeoJSON loader (`load_facilities_geojson`), and deterministic synthetic dataset generator (`generate_synthetic_facilities`).
- **CVRP Dispatch Solver (`spatialgrid.solver`):** Deterministic Google OR-Tools CVRP solver (`optimize_dispatch`) with capacity and max-distance constraints, post-solve geometric/capacity verification (`validate_dispatch_solution`), and naive/greedy baselines (`naive_llm_guess_dispatch`, `nearest_neighbor_heuristic`).
- **MCP Server & CLI (`spatialgrid.mcp_tools`, `spatialgrid.cli`):** Deterministic MCP tool exposure for `nearest_facilities`, `coverage_gap`, and `optimize_dispatch`, plus `spatialgrid` CLI commands.
- **Benchmark Suite (`spatialgrid.benchmarks`):** Deterministic 20-query benchmark suite (`run_benchmark_suite`) and Markdown formatter (`format_benchmark_markdown`) comparing SpatialGrid against naive baselines.
- **Documentation & CI:** ADR-001 (`docs/adr/001-neuro-symbolic-architecture.md`), MIT `LICENSE`, `CONTRIBUTING.md`, GitHub issue/PR templates, and `.github/workflows/ci.yml`.
