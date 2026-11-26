"""Unit tests for the SpatialGrid 20-query benchmark suite."""

from __future__ import annotations

from spatialgrid.benchmarks import format_benchmark_markdown, run_benchmark_suite


def test_benchmark_suite_metrics_and_determinism() -> None:
    summary = run_benchmark_suite(seed=42)

    assert summary.total_queries == 20
    assert len(summary.cases) == 20
    assert summary.spatialgrid_geometric_validity_pct == 100.0
    assert summary.baseline_geometric_validity_pct < summary.spatialgrid_geometric_validity_pct
    assert summary.p95_latency_ms < 500.0
    assert summary.mean_optimality_gap_pct <= 2.0
    assert summary.max_optimality_gap_pct <= 2.0

    categories = [case.category for case in summary.cases]
    assert categories.count("nearest_facilities") == 7
    assert categories.count("coverage_gap") == 6
    assert categories.count("optimize_dispatch") == 7

    summary_repeat = run_benchmark_suite(seed=42)
    assert [c.spatialgrid_valid for c in summary.cases] == [
        c.spatialgrid_valid for c in summary_repeat.cases
    ]
    assert [c.baseline_valid for c in summary.cases] == [
        c.baseline_valid for c in summary_repeat.cases
    ]


def test_format_benchmark_markdown_table() -> None:
    summary = run_benchmark_suite(seed=42)
    markdown = format_benchmark_markdown(summary)

    assert "| Metric | Value |" in markdown
    assert "| Case | Category | SpatialGrid Valid | Baseline Valid |" in markdown
    assert "nf-01" in markdown
    assert "cg-01" in markdown
    assert "od-01" in markdown
    assert "100.0%" in markdown
