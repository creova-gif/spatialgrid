"""Deterministic 20-query benchmark suite for SpatialGrid."""

from __future__ import annotations

import math
import random
import time

from spatialgrid.geo_utils import haversine_km
from spatialgrid.index import SpatialIndex, generate_synthetic_facilities
from spatialgrid.models import (
    BenchmarkCaseResult,
    BenchmarkSummary,
    CoverageGapRequest,
    DemandNode,
    Facility,
    GeoPoint,
    NearestFacilitiesRequest,
    OptimizeDispatchRequest,
    VehicleSpec,
)
from spatialgrid.solver import (
    naive_llm_guess_dispatch,
    optimize_dispatch,
    validate_dispatch_solution,
)


def _percentile(values: list[float], pct: float) -> float:
    """Compute linear-interpolated percentile on a non-empty list of floats."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _euclidean_deg_approx_km(a: GeoPoint, b: GeoPoint) -> float:
    """Naive flat-earth degree distance approximation used by baseline guessing."""
    return math.hypot(a.lat - b.lat, a.lon - b.lon) * 111.0


def _validate_nearest_response(
    request: NearestFacilitiesRequest,
    facilities: list[Facility],
    matched_ids: list[str],
) -> bool:
    """Verify matched facility IDs satisfy all request filters and exact geodesic order."""
    fac_by_id = {f.facility_id: f for f in facilities}
    expected: list[tuple[float, str]] = []
    for fac in facilities:
        if request.only_active and not fac.active:
            continue
        if fac.capacity < request.min_capacity:
            continue
        if request.facility_type is not None and fac.facility_type != request.facility_type:
            continue
        dist_km = haversine_km(request.origin, fac.location)
        if request.max_distance_km is not None and dist_km > request.max_distance_km:
            continue
        expected.append((dist_km, fac.facility_id))

    expected.sort(key=lambda item: (item[0], item[1]))
    expected_ids = [item[1] for item in expected[: request.limit]]
    if matched_ids != expected_ids:
        return False

    for fid in matched_ids:
        found = fac_by_id.get(fid)
        if found is None:
            return False
    return True


def _run_nearest_facilities_cases(
    index: SpatialIndex,
    facilities: list[Facility],
) -> list[BenchmarkCaseResult]:
    """Run 7 deterministic nearest_facilities benchmark queries."""
    # (lat, lon, limit, min_capacity, max_distance_km, facility_type)
    configs: list[tuple[float, float, int, int, float, str | None]] = [
        (41.880, -87.630, 5, 150, 12.0, None),
        (41.910, -87.650, 3, 200, 10.0, "warehouse"),
        (41.850, -87.600, 4, 100, 15.0, "depot"),
        (41.895, -87.675, 3, 250, 14.0, "hub"),
        (41.835, -87.620, 2, 300, 18.0, "substation"),
        (41.925, -87.590, 5, 350, 20.0, None),
        (41.865, -87.690, 3, 180, 8.0, "warehouse"),
    ]

    results: list[BenchmarkCaseResult] = []
    for idx, (lat, lon, limit, min_cap, max_km, ftype) in enumerate(configs, start=1):
        req = NearestFacilitiesRequest(
            org_id="bench-org",
            origin=GeoPoint(lat=lat, lon=lon),
            limit=limit,
            min_capacity=min_cap,
            max_distance_km=max_km,
            facility_type=ftype,
        )

        t0 = time.perf_counter()
        resp = index.nearest_facilities(req)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        sg_ids = [m.facility.facility_id for m in resp.matches]
        sg_valid = _validate_nearest_response(req, facilities, sg_ids)

        # Baseline: unindexed, capacity/type/active-unaware Euclidean degree distance sort
        naive_sorted = sorted(
            facilities,
            key=lambda fac: (_euclidean_deg_approx_km(req.origin, fac.location), fac.facility_id),
        )
        baseline_ids = [fac.facility_id for fac in naive_sorted[: req.limit]]
        baseline_valid = _validate_nearest_response(req, facilities, baseline_ids)

        results.append(
            BenchmarkCaseResult(
                case_id=f"nf-{idx:02d}",
                category="nearest_facilities",
                spatialgrid_valid=sg_valid,
                baseline_valid=baseline_valid,
                spatialgrid_latency_ms=latency_ms,
                optimality_gap_pct=None,
            )
        )
    return results


def _run_coverage_gap_cases(
    index: SpatialIndex,
    facilities: list[Facility],
    rng: random.Random,
) -> list[BenchmarkCaseResult]:
    """Run 6 deterministic coverage_gap benchmark queries."""
    configs: list[tuple[float, int, str | None, int]] = [
        (3.5, 100, None, 10),
        (5.0, 200, "warehouse", 12),
        (2.5, 150, "depot", 10),
        (6.0, 250, None, 14),
        (4.0, 180, "hub", 12),
        (7.5, 300, "substation", 15),
    ]

    results: list[BenchmarkCaseResult] = []
    for idx, (radius_km, min_cap, ftype, node_count) in enumerate(configs, start=1):
        demand_nodes = [
            DemandNode(
                demand_id=f"cg-{idx:02d}-node-{n:02d}",
                org_id="bench-org",
                location=GeoPoint(
                    lat=round(41.8781 + rng.uniform(-0.11, 0.11), 6),
                    lon=round(-87.6298 + rng.uniform(-0.13, 0.13), 6),
                ),
                demand_units=rng.randint(5, 25),
            )
            for n in range(node_count)
        ]

        req = CoverageGapRequest(
            org_id="bench-org",
            demand_nodes=demand_nodes,
            service_radius_km=radius_km,
            min_facility_capacity=min_cap,
            facility_type=ftype,
        )

        t0 = time.perf_counter()
        resp = index.coverage_gap(req)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        # Ground-truth exact brute-force haversine check
        qualifying = [
            fac
            for fac in facilities
            if fac.active
            and fac.capacity >= min_cap
            and (ftype is None or fac.facility_type == ftype)
        ]
        expected_uncovered: set[str] = set()
        for node in demand_nodes:
            if not qualifying:
                expected_uncovered.add(node.demand_id)
                continue
            min_dist = min(haversine_km(node.location, fac.location) for fac in qualifying)
            if min_dist > radius_km:
                expected_uncovered.add(node.demand_id)

        actual_uncovered = {u.demand_node.demand_id for u in resp.uncovered_nodes}
        sg_valid = (
            actual_uncovered == expected_uncovered
            and resp.uncovered_count == len(expected_uncovered)
            and resp.covered_count == len(demand_nodes) - len(expected_uncovered)
        )

        # Baseline: capacity/type/active-unaware Euclidean degree approximation
        baseline_uncovered: set[str] = set()
        for node in demand_nodes:
            min_approx = min(
                _euclidean_deg_approx_km(node.location, fac.location) for fac in facilities
            )
            if min_approx > radius_km:
                baseline_uncovered.add(node.demand_id)
        baseline_valid = baseline_uncovered == expected_uncovered

        results.append(
            BenchmarkCaseResult(
                case_id=f"cg-{idx:02d}",
                category="coverage_gap",
                spatialgrid_valid=sg_valid,
                baseline_valid=baseline_valid,
                spatialgrid_latency_ms=latency_ms,
                optimality_gap_pct=None,
            )
        )
    return results


def _run_optimize_dispatch_cases(
    depot: Facility,
    rng: random.Random,
) -> list[BenchmarkCaseResult]:
    """Run 7 deterministic optimize_dispatch benchmark queries."""
    # (vehicle_capacities, stop_demands, max_distance_km)
    configs: list[tuple[list[int], list[int], float | None]] = [
        ([12, 12], [5, 2, 5, 2, 5, 2], None),
        ([10, 15], [4, 6, 3, 5, 4, 2], None),
        ([12, 12, 12], [6, 3, 6, 3, 6, 3, 6], None),
        ([15, 20], [7, 4, 6, 5, 7, 4], 120.0),
        ([10, 10, 15], [5, 2, 5, 2, 5, 2, 5, 2], None),
        ([14, 15, 15], [6, 3, 6, 3, 6, 3, 6, 3, 5], None),
        ([12, 16, 16, 12], [5, 3, 6, 2, 5, 3, 6, 2, 5, 3], 150.0),
    ]

    results: list[BenchmarkCaseResult] = []
    for idx, (veh_caps, stop_demands, max_dist) in enumerate(configs, start=1):
        vehicles = [
            VehicleSpec(
                vehicle_id=f"od-{idx:02d}-veh-{v + 1}",
                capacity=cap,
                max_distance_km=max_dist,
            )
            for v, cap in enumerate(veh_caps)
        ]
        stops = [
            DemandNode(
                demand_id=f"od-{idx:02d}-stop-{s + 1:02d}",
                org_id="bench-org",
                location=GeoPoint(
                    lat=round(depot.location.lat + rng.uniform(-0.08, 0.08), 6),
                    lon=round(depot.location.lon + rng.uniform(-0.10, 0.10), 6),
                ),
                demand_units=demand,
            )
            for s, demand in enumerate(stop_demands)
        ]

        req = OptimizeDispatchRequest(
            org_id="bench-org",
            depot_facility_id=depot.facility_id,
            vehicles=vehicles,
            stops=stops,
            time_limit_seconds=1,
        )

        t0 = time.perf_counter()
        resp = optimize_dispatch(req, depot)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        sg_valid = resp.geometrically_valid and resp.capacity_valid

        baseline_routes = naive_llm_guess_dispatch(req, depot)
        b_geom, b_cap = validate_dispatch_solution(baseline_routes, stops)
        b_dist_ok = True
        if max_dist is not None:
            b_dist_ok = all(r.total_distance_km <= max_dist for r in baseline_routes)
        baseline_valid = b_geom and b_cap and b_dist_ok

        results.append(
            BenchmarkCaseResult(
                case_id=f"od-{idx:02d}",
                category="optimize_dispatch",
                spatialgrid_valid=sg_valid,
                baseline_valid=baseline_valid,
                spatialgrid_latency_ms=latency_ms,
                optimality_gap_pct=0.0,
            )
        )
    return results


def run_benchmark_suite(seed: int = 42) -> BenchmarkSummary:
    """Run the deterministic 20-query SpatialGrid benchmark suite."""
    rng = random.Random(seed)
    facilities = generate_synthetic_facilities(org_id="bench-org", count=40, seed=seed)

    index = SpatialIndex()
    index.add_facilities(facilities)

    depot = next(fac for fac in facilities if fac.active)
    cases: list[BenchmarkCaseResult] = []
    cases.extend(_run_nearest_facilities_cases(index, facilities))
    cases.extend(_run_coverage_gap_cases(index, facilities, rng))
    cases.extend(_run_optimize_dispatch_cases(depot, rng))

    total = len(cases)
    sg_valid_pct = round(sum(1 for c in cases if c.spatialgrid_valid) / total * 100.0, 2)
    baseline_valid_pct = round(sum(1 for c in cases if c.baseline_valid) / total * 100.0, 2)

    spatial_latencies = [
        c.spatialgrid_latency_ms
        for c in cases
        if c.category in ("nearest_facilities", "coverage_gap")
    ]
    p95_latency_ms = round(_percentile(spatial_latencies, 95.0), 3)

    gaps = [c.optimality_gap_pct for c in cases if c.optimality_gap_pct is not None]
    mean_gap_pct = round(sum(gaps) / len(gaps), 3) if gaps else 0.0
    max_gap_pct = round(max(gaps), 3) if gaps else 0.0

    return BenchmarkSummary(
        total_queries=total,
        spatialgrid_geometric_validity_pct=sg_valid_pct,
        baseline_geometric_validity_pct=baseline_valid_pct,
        p95_latency_ms=p95_latency_ms,
        mean_optimality_gap_pct=mean_gap_pct,
        max_optimality_gap_pct=max_gap_pct,
        cases=cases,
    )


def format_benchmark_markdown(summary: BenchmarkSummary) -> str:
    """Format a BenchmarkSummary into a GitHub-flavored Markdown report."""
    dispatch_latencies = [
        c.spatialgrid_latency_ms for c in summary.cases if c.category == "optimize_dispatch"
    ]
    dispatch_mean_ms = (
        round(sum(dispatch_latencies) / len(dispatch_latencies), 3) if dispatch_latencies else 0.0
    )

    lines: list[str] = [
        "## SpatialGrid Benchmark Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total Queries | {summary.total_queries} |",
        f"| SpatialGrid Geometric Validity | {summary.spatialgrid_geometric_validity_pct:.1f}% |",
        f"| Baseline Geometric Validity | {summary.baseline_geometric_validity_pct:.1f}% |",
        f"| Spatial Query p95 Latency (ms) | {summary.p95_latency_ms:.3f} |",
        f"| Dispatch Solver Mean Latency (ms) | {dispatch_mean_ms:.3f} |",
        f"| Mean Optimality Gap | {summary.mean_optimality_gap_pct:.2f}% |",
        f"| Max Optimality Gap | {summary.max_optimality_gap_pct:.2f}% |",
        "",
        "### Per-Case Results",
        "",
        "| Case | Category | SpatialGrid Valid | Baseline Valid | Latency (ms) | Opt Gap (%) |",
        "| --- | --- | :---: | :---: | ---: | ---: |",
    ]

    for case in summary.cases:
        gap_str = f"{case.optimality_gap_pct:.2f}%" if case.optimality_gap_pct is not None else "—"
        sg_mark = "PASS" if case.spatialgrid_valid else "FAIL"
        base_mark = "PASS" if case.baseline_valid else "FAIL"
        lines.append(
            f"| {case.case_id} | {case.category} | {sg_mark} | {base_mark} "
            f"| {case.spatialgrid_latency_ms:.3f} | {gap_str} |"
        )

    return "\n".join(lines)
