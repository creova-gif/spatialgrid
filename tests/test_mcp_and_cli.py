"""Tests for SpatialGrid MCP server, deterministic tool functions, and CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from spatialgrid import cli
from spatialgrid.exceptions import SpatialGridError
from spatialgrid.index import SpatialIndex, generate_synthetic_facilities
from spatialgrid.mcp_tools import (
    create_mcp_server,
    tool_coverage_gap,
    tool_healthcheck,
    tool_nearest_facilities,
    tool_optimize_dispatch,
)


def _seeded_index(org_id: str = "demo-org") -> SpatialIndex:
    index = SpatialIndex()
    index.add_facilities(generate_synthetic_facilities(org_id, count=25, seed=42))
    return index


def test_create_mcp_server_registers_tools() -> None:
    server = create_mcp_server(seed_demo=True)
    assert isinstance(server, MCPServer)
    tools = asyncio.run(server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "healthcheck",
        "nearest_facilities",
        "coverage_gap",
        "optimize_dispatch",
    }


def test_tool_healthcheck() -> None:
    result = tool_healthcheck()
    assert result["status"] == "ok"
    assert result["service"] == "spatialgrid"


def test_tool_nearest_facilities() -> None:
    index = _seeded_index("demo-org")
    result = tool_nearest_facilities(
        index=index,
        org_id="demo-org",
        lat=41.8781,
        lon=-87.6298,
        limit=3,
    )
    assert result["org_id"] == "demo-org"
    assert len(result["matches"]) == 3
    distances = [m["distance_km"] for m in result["matches"]]
    assert distances == sorted(distances)


def test_tool_coverage_gap() -> None:
    index = _seeded_index("demo-org")
    demand_nodes: list[dict[str, Any]] = [
        {
            "demand_id": "d-near",
            "org_id": "demo-org",
            "location": {"lat": 41.8781, "lon": -87.6298},
            "demand_units": 10,
            "priority": 1,
        },
        {
            "demand_id": "d-far",
            "org_id": "demo-org",
            "location": {"lat": 42.3000, "lon": -88.2000},
            "demand_units": 15,
            "priority": 2,
        },
    ]
    result = tool_coverage_gap(
        index=index,
        org_id="demo-org",
        demand_nodes=demand_nodes,
        service_radius_km=15.0,
    )
    assert result["total_demand_nodes"] == 2
    assert result["uncovered_count"] >= 1
    uncovered_ids = {item["demand_node"]["demand_id"] for item in result["uncovered_nodes"]}
    assert "d-far" in uncovered_ids


def test_tool_optimize_dispatch_and_missing_depot() -> None:
    index = _seeded_index("demo-org")
    vehicles: list[dict[str, Any]] = [
        {"vehicle_id": "v-1", "capacity": 30},
        {"vehicle_id": "v-2", "capacity": 30},
    ]
    stops: list[dict[str, Any]] = [
        {
            "demand_id": "s-1",
            "org_id": "demo-org",
            "location": {"lat": 41.885, "lon": -87.635},
            "demand_units": 12,
        },
        {
            "demand_id": "s-2",
            "org_id": "demo-org",
            "location": {"lat": 41.862, "lon": -87.645},
            "demand_units": 15,
        },
    ]

    result = tool_optimize_dispatch(
        index=index,
        org_id="demo-org",
        depot_facility_id="demo-org-fac-000",
        vehicles=vehicles,
        stops=stops,
        time_limit_seconds=5,
    )
    assert result["geometrically_valid"] is True
    assert result["capacity_valid"] is True
    assert result["total_demand_served"] == 27

    with pytest.raises(SpatialGridError, match="not found"):
        tool_optimize_dispatch(
            index=index,
            org_id="demo-org",
            depot_facility_id="missing-depot",
            vehicles=vehicles,
            stops=stops,
        )


def test_cli_demo(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["demo"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["org_id"] == "demo-org"
    assert payload["facilities_indexed"] == 25
    assert "nearest_facilities" in payload
    assert "coverage_gap" in payload
    assert "optimize_dispatch" in payload


def test_cli_benchmark(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_out = tmp_path / "bench" / "summary.json"
    exit_code = cli.main(["benchmark", "--seed", "42", "--json-out", str(json_out)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "SpatialGrid" in captured.out
    assert json_out.exists()
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert data["total_queries"] >= 1
