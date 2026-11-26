"""Command-line interface for SpatialGrid."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from spatialgrid.benchmarks import format_benchmark_markdown, run_benchmark_suite
from spatialgrid.index import SpatialIndex, generate_synthetic_facilities
from spatialgrid.mcp_tools import (
    create_mcp_server,
    tool_coverage_gap,
    tool_nearest_facilities,
    tool_optimize_dispatch,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spatialgrid",
        description="Neuro-symbolic spatial reasoning engine CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the SpatialGrid MCP server.")
    serve_parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="Seed the server index with 25 synthetic demo-org facilities.",
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Run the 20-query deterministic SpatialGrid benchmark suite.",
    )
    benchmark_parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write the benchmark summary JSON.",
    )
    benchmark_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for benchmark generation (default: 42).",
    )

    subparsers.add_parser(
        "demo",
        help=(
            "Run an end-to-end demo across nearest_facilities, coverage_gap, and optimize_dispatch."
        ),
    )
    return parser


def _run_demo() -> dict[str, Any]:
    org_id = "demo-org"
    index = SpatialIndex()
    facilities = generate_synthetic_facilities(org_id, count=25, seed=42)
    index.add_facilities(facilities)

    nearest_res = tool_nearest_facilities(
        index=index,
        org_id=org_id,
        lat=41.8781,
        lon=-87.6298,
        limit=3,
    )

    demand_nodes: list[dict[str, Any]] = [
        {
            "demand_id": "dem-001",
            "org_id": org_id,
            "location": {"lat": 41.885, "lon": -87.635},
            "demand_units": 12,
            "priority": 2,
        },
        {
            "demand_id": "dem-002",
            "org_id": org_id,
            "location": {"lat": 41.862, "lon": -87.645},
            "demand_units": 18,
            "priority": 1,
        },
        {
            "demand_id": "dem-003",
            "org_id": org_id,
            "location": {"lat": 42.150, "lon": -87.950},
            "demand_units": 10,
            "priority": 3,
        },
    ]
    coverage_res = tool_coverage_gap(
        index=index,
        org_id=org_id,
        demand_nodes=demand_nodes,
        service_radius_km=5.0,
    )

    depot_id = facilities[0].facility_id
    vehicles: list[dict[str, Any]] = [
        {"vehicle_id": "veh-1", "capacity": 25},
        {"vehicle_id": "veh-2", "capacity": 25},
    ]
    dispatch_res = tool_optimize_dispatch(
        index=index,
        org_id=org_id,
        depot_facility_id=depot_id,
        vehicles=vehicles,
        stops=demand_nodes,
        time_limit_seconds=5,
    )

    return {
        "org_id": org_id,
        "facilities_indexed": len(facilities),
        "nearest_facilities": nearest_res,
        "coverage_gap": coverage_res,
        "optimize_dispatch": dispatch_res,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the SpatialGrid CLI."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "serve":
        server = create_mcp_server(seed_demo=bool(args.seed_demo))
        server.run()
        return 0

    if args.command == "benchmark":
        summary = run_benchmark_suite(seed=int(args.seed))
        print(format_benchmark_markdown(summary))
        if args.json_out is not None:
            out_path: Path = args.json_out
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        return 0

    if args.command == "demo":
        demo_summary = _run_demo()
        print(json.dumps(demo_summary, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
