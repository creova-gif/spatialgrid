# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- OpenAPI `3.1.0` specification endpoint (`GET /openapi.json`) generated from Pydantic v2 models (`spatialgrid.api.openapi`).
- WCAG 2.1 AA accessibility landmarks (`aria-label`, `aria-live="polite"`, `:focus-visible`), KPI summary bar, and Deck.gl map click-to-query in `/workspace`.
- Security response headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on `/v1/*`).
- Bounded OR-Tools solver concurrency (`max_concurrent_solves=4` via `anyio.CapacityLimiter`).

## [0.2.0] - 2026-11-26

### Added
- Non-blocking `anyio.to_thread.run_sync` execution for CPU/IO-bound HTTP endpoints (`/v1/facilities/ingest`, `/v1/facilities/nearest`, `/v1/coverage-gap`, `/v1/dispatch/optimize`) so OR-Tools solves never block the asyncio event loop.
- Thread-safe Prometheus metrics registry (`spatialgrid.api.metrics.MetricsRegistry`) exposed at `GET /metrics` (`text/plain; version=0.0.4`).
- `h3_grid_disk` and `SpatialIndex.facilities_in_h3_ring` for fast H3 neighborhood queries.
- Request DoS/memory bounds on `CoverageGapRequest.demand_nodes` (`max_length=10000`), `OptimizeDispatchRequest.stops` (`max_length=500`), and `OptimizeDispatchRequest.vehicles` (`max_length=100`).

## [0.1.0] - 2026-11-26

### Added
- Neuro-symbolic spatial indexing package (`spatialgrid.index`) backed by Shapely `STRtree` and Uber H3 with strict `org_id` tenant isolation, `RLock` thread safety, `remove_facility`/`list_facilities`, and atomic JSON snapshot persistence (`save_snapshot`/`load_snapshot`).
- Deterministic OR-Tools CVRP solver (`spatialgrid.solver`) with vehicle capacity, `max_distance_km`, time-window (`time_window_start_min`/`time_window_end_min`), `max_duration_min`, `service_time_min` constraints, GeoJSON export (`routes_to_geojson`), duplicate `demand_id` validation, post-solve route validation, and baseline heuristics.
- MCP server (`spatialgrid.mcp_tools`) exposing `healthcheck`, `ingest_geojson`, `list_facilities`, `remove_facility`, `nearest_facilities`, `coverage_gap`, and `optimize_dispatch` with structured logging.
- Durable SQLite WAL facility store (`spatialgrid.storage.SQLiteFacilityStore`) with `(org_id, facility_id)` composite primary key and `--db-path` CLI integration.
- Authenticated Starlette/Uvicorn HTTP API gateway (`spatialgrid.api.create_http_app`, `spatialgrid serve-http`) with Bearer token `org_id` binding, `GET /` redirect to `/workspace`, `/readyz`, `GET /v1/h3/summary`, per-org sliding-window rate limiting, `2 MiB` payload guard, `X-Request-ID` correlation, `?format=geojson` dispatch export, and JSON error mapping.
- Interactive Deck.gl + MapLibre dispatch workspace at `GET /workspace` with upgraded interactive controls, `docker-compose.yml`, and `examples/chicago_facilities.geojson`.
- Multi-stage `Dockerfile`, `.dockerignore`, `SECURITY.md`, PEP 561 `src/spatialgrid/py.typed` marker, and `gitleaks` secret-scanning job in CI.
- CLI (`spatialgrid.cli`) supporting `serve`, `serve-http`, `demo`, and `benchmark`.
- 20-query deterministic benchmark suite (`spatialgrid.benchmarks`) and 60 unit/integration tests (`ruff`, `mypy --strict`, `pytest`).
- GitHub templates, CI workflow, MIT `LICENSE`, `CONTRIBUTING.md`, and `docs/adr/001-neuro-symbolic-architecture.md`.
