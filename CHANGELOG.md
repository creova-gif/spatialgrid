# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-26

### Added
- Neuro-symbolic spatial indexing package (`spatialgrid.index`) backed by Shapely `STRtree` and Uber H3 with strict `org_id` tenant isolation, `RLock` thread safety, `remove_facility`/`list_facilities`, and atomic JSON snapshot persistence (`save_snapshot`/`load_snapshot`).
- Deterministic OR-Tools CVRP solver (`spatialgrid.solver`) with vehicle capacity, `max_distance_km`, time-window (`time_window_start_min`/`time_window_end_min`), `max_duration_min`, `service_time_min` constraints, GeoJSON export (`routes_to_geojson`), duplicate `demand_id` validation, post-solve route validation, and baseline heuristics.
- MCP server (`spatialgrid.mcp_tools`) exposing `healthcheck`, `ingest_geojson`, `list_facilities`, `remove_facility`, `nearest_facilities`, `coverage_gap`, and `optimize_dispatch` with structured logging.
- Durable SQLite WAL facility store (`spatialgrid.storage.SQLiteFacilityStore`) with `(org_id, facility_id)` composite primary key and `--db-path` CLI integration.
- Authenticated Starlette/Uvicorn HTTP API gateway (`spatialgrid.api.create_http_app`, `spatialgrid serve-http`) with Bearer token `org_id` binding, `/readyz`, per-org sliding-window rate limiting, `2 MiB` payload guard, `X-Request-ID` correlation, `?format=geojson` dispatch export, and JSON error mapping.
- Multi-stage `Dockerfile`, `.dockerignore`, and `gitleaks` secret-scanning job in CI.
- CLI (`spatialgrid.cli`) supporting `serve`, `serve-http`, `demo`, and `benchmark`.
- 20-query deterministic benchmark suite (`spatialgrid.benchmarks`) and 59 unit/integration tests (`ruff`, `mypy --strict`, `pytest`).
- GitHub templates, CI workflow, MIT `LICENSE`, `CONTRIBUTING.md`, and `docs/adr/001-neuro-symbolic-architecture.md`.
