# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-26

### Added
- Neuro-symbolic spatial indexing package (`spatialgrid.index`) backed by Shapely `STRtree` and Uber H3 with strict `org_id` tenant isolation, `RLock` thread safety, `remove_facility`/`list_facilities`, and atomic JSON snapshot persistence (`save_snapshot`/`load_snapshot`).
- Deterministic OR-Tools CVRP solver (`spatialgrid.solver`) with vehicle capacity, `max_distance_km` constraints, duplicate `demand_id` validation, post-solve route validation, and baseline heuristics.
- MCP server (`spatialgrid.mcp_tools`) exposing `healthcheck`, `ingest_geojson`, `nearest_facilities`, `coverage_gap`, and `optimize_dispatch` with structured logging.
- CLI (`spatialgrid.cli`) supporting `serve` (`--seed-demo`, `--facilities-geojson`, `--org-id`, `--state-file`), `demo`, and `benchmark`.
- 20-query deterministic benchmark suite (`spatialgrid.benchmarks`) and 44 unit/integration tests (`ruff`, `mypy --strict`, `pytest`).
- GitHub templates, CI workflow, MIT `LICENSE`, `CONTRIBUTING.md`, and `docs/adr/001-neuro-symbolic-architecture.md`.
