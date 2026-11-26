# SpatialGrid

**Neuro-symbolic spatial reasoning & physical-world data infrastructure for AI agents.**

[![CI](https://img.shields.io/badge/CI-passing-brightgreen.svg)](.github/workflows/ci.yml)
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
- **Deterministic Routing (`spatialgrid.solver`):** Solves Capacitated Vehicle Routing Problems (CVRP) with optional per-vehicle `max_distance_km` limits via OR-Tools `GUIDED_LOCAL_SEARCH`, duplicate stop ID rejection, and post-solve geometric/capacity validation.
- **MCP Server & CLI (`spatialgrid.mcp_tools`, `spatialgrid.cli`):** Exposes `healthcheck`, `ingest_geojson`, `nearest_facilities`, `coverage_gap`, and `optimize_dispatch` over MCP (`mcp.server.mcpserver.MCPServer`) with structured latency/error logging and CLI flags (`--facilities-geojson`, `--org-id`, `--state-file`).
- **Tenant Isolation:** Every index lookup, coverage check, and dispatch solve enforces strict `org_id` scoping (`TenantIsolationError` on cross-tenant inputs).
- **Reproducible 20-Query Benchmark (`spatialgrid.benchmarks`):** Evaluates geometric validity, spatial query p95 latency, and dispatch optimality gap against an unassisted baseline.

---

## Installation

### Using `uv` (recommended)

```bash
git clone https://github.com/justinmafie/spatialgrid.git
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

Start the MCP stdio server (with optional 25-facility demo dataset, GeoJSON import, and durable snapshot state):

```bash
PYTHONPATH=src .venv/bin/python -m spatialgrid.cli serve --seed-demo --state-file .spatialgrid-state.json
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
│   ├── benchmarks/
│   ├── index/
│   ├── mcp_tools/
│   └── solver/
├── tests/
├── AGENTS.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── PROJECT_SPEC.md
├── TASKS.md
└── pyproject.toml
```

---

## Development & Verification

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/mypy src tests
PYTHONPATH=src .venv/bin/pytest -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on branches, ADRs, tests, and pull requests.

---

## License

MIT — see [LICENSE](LICENSE).
