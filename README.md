# SpatialGrid

**Neuro-symbolic spatial reasoning & physical-world data infrastructure for AI agents.**

[![CI](https://github.com/creova-gif/spatialgrid/actions/workflows/ci.yml/badge.svg)](https://github.com/creova-gif/spatialgrid/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-informational.svg)](CHANGELOG.md)
[![Typed: mypy strict](https://img.shields.io/badge/mypy-strict-blue.svg)](pyproject.toml)
[![Lint: ruff](https://img.shields.io/badge/ruff-checked-purple.svg)](pyproject.toml)

SpatialGrid fuses GeoJSON/OpenStreetMap facility data, **H3** hexagonal cells, **Shapely `STRtree`** (R-tree) spatial indexing, and **Google OR-Tools** vehicle routing into deterministic **MCP tools** so agents never hallucinate coordinates, distances, capacities, or dispatch routes.

---

## Architecture

```text
  Agent / LLM Intent
         │
         ▼
  ┌──────────────────────┐     Pydantic v2 Strict Contracts
  │   SpatialGrid MCP    │     org_id Tenant Isolation
  │   (mcp_tools/server) │──────────────────────────────────┐
  └──────────┬───────────┘                                  │
             │                                              │
     ┌───────┴────────────────┐                             │
     ▼                        ▼                             ▼
┌─────────────────┐  ┌──────────────────┐          ┌─────────────────┐
│  SpatialIndex   │  │  OR-Tools Solver │          │  20-Query Eval  │
│  H3 + STRtree   │  │  CVRP + Capacity │          │  Benchmark      │
│  nearest / gap  │  │  optimize_dispatch│         │  Suite          │
└─────────────────┘  └──────────────────┘          └─────────────────┘
```

See [ADR-001: Neuro-Symbolic Spatial Architecture](docs/adr/001-neuro-symbolic-architecture.md) and [Threat Model](docs/THREAT_MODEL.md) for design rationale and security boundaries.

---

## Features

- **Exact Spatial Indexing (`spatialgrid.index`):** Thread-safe (`RLock`) per-`org_id` indexing using Shapely `STRtree` (R-tree) and Uber H3 cells (`resolution=7` default) with exact WGS84 Haversine distances, attribute/capacity filtering, `remove_facility`/`list_facilities` lifecycle APIs, and atomic JSON snapshot persistence (`save_snapshot`/`load_snapshot`).
- **Deterministic Routing (`spatialgrid.solver`):** Solves Capacitated Vehicle Routing Problems (CVRP) with optional per-vehicle `max_distance_km`, `avg_speed_kmh`, `max_duration_min`, and per-stop time windows (`time_window_start_min`, `time_window_end_min`, `service_time_min`) via OR-Tools `GUIDED_LOCAL_SEARCH`, plus GeoJSON route export (`routes_to_geojson`).
- **MCP Server, HTTP Gateway & CLI (`spatialgrid.mcp_tools`, `spatialgrid.api`, `spatialgrid.cli`):** Exposes `healthcheck`, `ingest_geojson`, `list_facilities`, `remove_facility`, `nearest_facilities`, `coverage_gap`, and `optimize_dispatch` over MCP (`MCPServer`), plus an authenticated Starlette/Uvicorn HTTP API (`GET /` redirect, `GET /workspace`, `GET /healthz`, `GET /readyz`, `GET /v1/h3/summary`, `GET /v1/facilities`, `DELETE /v1/facilities/{id}`, and `POST /v1/*` endpoints) with per-org rate limiting (`--rate-limit-rpm`), `2 MiB` payload limits, `X-Request-ID` tracing, and SQLite WAL persistence (`--db-path`).
- **Tenant Isolation:** Every index lookup, coverage check, and dispatch solve enforces strict `org_id` scoping (`TenantIsolationError` on cross-tenant inputs).
- **Reproducible 20-Query Benchmark (`spatialgrid.benchmarks`):** Evaluates geometric validity, spatial query p95 latency, and dispatch optimality gap against an unassisted baseline.

---

## Installation

### Using `uv` (recommended)

```bash
git clone https://github.com/creova-gif/spatialgrid.git
cd spatialgrid
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

> **macOS note:** If your repo lives under `~/Documents` and macOS sets `UF_HIDDEN` on `.venv` `.pth` files, install non-editably (`uv pip install --python .venv/bin/python ".[dev]"`) or run with `PYTHONPATH=src`.

### Using `pip`

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install ".[dev]"
```

---

## Usage

### CLI

Run the end-to-end synthetic Chicago demo:

```bash
PYTHONPATH=src .venv/bin/python -m spatialgrid.cli demo
```

Run the 20-query benchmark suite (optionally exporting JSON):

```bash
PYTHONPATH=src .venv/bin/python -m spatialgrid.cli benchmark --json-out benchmark-results.json
```

Start the HTTP API gateway and open the interactive Deck.gl dispatch workspace at `http://127.0.0.1:8080/workspace`:

```bash
PYTHONPATH=src .venv/bin/python -m spatialgrid.cli serve-http --facilities-geojson examples/chicago_facilities.geojson --org-id demo-org --db-path .spatialgrid.db
```

Or run with Docker Compose:

```bash
docker compose up --build
```

### MCP Client Configuration

Add SpatialGrid to your MCP client configuration (e.g. Claude Desktop, Cursor, or Antigravity):

```json
{
  "mcpServers": {
    "spatialgrid": {
      "command": "/path/to/spatialgrid/.venv/bin/python",
      "args": ["-m", "spatialgrid.cli", "serve", "--seed-demo"],
      "env": {
        "PYTHONPATH": "/path/to/spatialgrid/src"
      }
    }
  }
}
```

### Python API

```python
from spatialgrid.index import SpatialIndex, generate_synthetic_facilities
from spatialgrid.models import GeoPoint, NearestFacilitiesRequest

index = SpatialIndex()
index.add_facilities(generate_synthetic_facilities(org_id="acme", count=25, seed=42))

response = index.nearest_facilities(
    NearestFacilitiesRequest(
        org_id="acme",
        origin=GeoPoint(lat=41.8781, lon=-87.6298),
        limit=3,
        min_capacity=100,
    )
)
for match in response.matches:
    print(match.facility.facility_id, f"{match.distance_km:.2f} km", match.facility.h3_index)
```

### HTTP API Endpoints

| Method & Path | Description |
| --- | --- |
| `GET /` | Redirects to `/workspace` |
| `GET /workspace` | Interactive Deck.gl + MapLibre dispatch workspace |
| `GET /healthz` | Liveness probe |
| `GET /readyz` | Readiness probe (verifies index & storage readiness) |
| `GET /metrics` | Prometheus text metrics exposition (`0.0.4`) |
| `GET /v1/h3/summary` | H3 hexagonal cell summary for the scoped `org_id` |
| `GET /v1/facilities` | List indexed facilities for the scoped `org_id` |
| `DELETE /v1/facilities/{id}` | Remove a facility by ID for the scoped `org_id` |
| `POST /v1/facilities/ingest` | Ingest a GeoJSON `FeatureCollection` into the index |
| `POST /v1/facilities/nearest` | Query nearest facilities by Haversine distance & capacity |
| `POST /v1/coverage-gap` | Identify demand nodes outside `service_radius_km` |
| `POST /v1/dispatch/optimize` | Solve CVRP dispatch routes (supports `?format=geojson`) |

---

## Configuration

### Environment Variables

| Variable | Description |
| --- | --- |
| `SPATIALGRID_API_KEYS` | JSON object mapping Bearer tokens to `org_id` (e.g., `'{"tok-acme":"acme"}'`). Used by `serve-http` for constant-time `Authorization: Bearer <token>` authentication and tenant binding. |

### CLI Flags

| Flag | Commands | Description |
| --- | --- | --- |
| `--require-auth` | `serve-http` | Require `SPATIALGRID_API_KEYS` to be set and enforce Bearer token authentication on `/v1/*` endpoints. |
| `--rate-limit-rpm` | `serve-http` | Optional per-`org_id` sliding-window requests-per-minute limit for `/v1/*` endpoints. |
| `--db-path` | `serve`, `serve-http` | Path to a SQLite database file for durable WAL facility persistence. |
| `--state-file` | `serve` | Path to a `SpatialIndex` JSON snapshot file to load on startup and save after initial seeding. |
| `--facilities-geojson` | `serve`, `serve-http` | Path to a GeoJSON `FeatureCollection` file to load into the index on startup. |
| `--org-id` | `serve`, `serve-http` | Default `org_id` applied to facilities loaded via `--facilities-geojson` (default: `default`). |
| `--seed-demo` | `serve`, `serve-http` | Seed the index with 25 synthetic `demo-org` facilities on startup. |
| `--host` / `--port` | `serve-http` | Bind address and port for the HTTP gateway (default: `127.0.0.1:8080`). |

---

## Benchmarks

Results from `PYTHONPATH=src .venv/bin/python -m spatialgrid.cli benchmark` (`seed=42`, Python 3.12.12 on Apple Silicon):

| Metric | SpatialGrid | Unassisted Baseline | Target |
| --- | ---: | ---: | ---: |
| Total Queries | 20 | 20 | 20 |
| Geometric Validity | **100.0%** | 0.0% | 100% |
| Spatial Query p95 Latency | **1.226 ms** | — | < 100 ms |
| Dispatch Solver Mean Latency | **1060.896 ms** | — | 1s GLS budget |
| Mean Optimality Gap | **0.00%** | — | < 2% |

---

## Project Structure

```text
spatialgrid/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/ci.yml
├── docs/
│   ├── adr/
│   │   ├── 000-template.md
│   │   └── 001-neuro-symbolic-architecture.md
│   └── THREAT_MODEL.md
├── src/spatialgrid/
│   ├── __init__.py
│   ├── cli.py
│   ├── exceptions.py
│   ├── geo_utils.py
│   ├── models.py
│   ├── py.typed
│   ├── api/
│   ├── benchmarks/
│   ├── index/
│   ├── mcp_tools/
│   ├── solver/
│   └── storage/
├── tests/
├── AGENTS.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── Dockerfile
├── LICENSE
├── PROJECT_SPEC.md
├── SECURITY.md
├── TASKS.md
├── docker-compose.yml
└── pyproject.toml
```

---

## Development & Verification

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/mypy src tests
PYTHONPATH=src .venv/bin/pytest -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on branches, ADRs, tests, and pull requests, and [SECURITY.md](SECURITY.md) for vulnerability reporting and security controls.

---

## License

MIT — see [LICENSE](LICENSE).
