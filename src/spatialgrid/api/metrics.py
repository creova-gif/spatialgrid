"""Thread-safe Prometheus text-format metrics registry for SpatialGrid."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


def _escape_label_value(value: str) -> str:
    """Escape backslashes, double quotes, and newlines for Prometheus label values."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    """Thread-safe in-memory HTTP and index metrics registry."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_counts: dict[tuple[str, str, int], int] = defaultdict(int)
        self._duration_sum_ms: dict[str, float] = defaultdict(float)
        self._duration_count: dict[str, int] = defaultdict(int)

    def observe_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """Record a completed HTTP request observation."""
        with self._lock:
            self._request_counts[(method, path, status_code)] += 1
            self._duration_sum_ms[path] += duration_ms
            self._duration_count[path] += 1

    def render_prometheus(self, orgs_indexed: int, facilities_total: int) -> str:
        """Render all recorded metrics in Prometheus text exposition format."""
        with self._lock:
            request_counts = sorted(self._request_counts.items())
            duration_sums = sorted(self._duration_sum_ms.items())
            duration_counts = sorted(self._duration_count.items())

        lines: list[str] = [
            "# HELP spatialgrid_http_requests_total Total HTTP requests handled by SpatialGrid.",
            "# TYPE spatialgrid_http_requests_total counter",
        ]
        for (method, path, status_code), count in request_counts:
            m = _escape_label_value(method)
            p = _escape_label_value(path)
            labels = f'method="{m}",path="{p}",status="{status_code}"'
            lines.append(f"spatialgrid_http_requests_total{{{labels}}} {count}")

        lines.extend(
            [
                "# HELP spatialgrid_http_request_duration_ms_sum "
                "Total HTTP request duration in milliseconds.",
                "# TYPE spatialgrid_http_request_duration_ms_sum counter",
            ]
        )
        for path, total_ms in duration_sums:
            p = _escape_label_value(path)
            lines.append(f'spatialgrid_http_request_duration_ms_sum{{path="{p}"}} {total_ms:.3f}')

        lines.extend(
            [
                "# HELP spatialgrid_http_request_duration_ms_count "
                "Total HTTP requests timed in milliseconds.",
                "# TYPE spatialgrid_http_request_duration_ms_count counter",
            ]
        )
        for path, count in duration_counts:
            p = _escape_label_value(path)
            lines.append(f'spatialgrid_http_request_duration_ms_count{{path="{p}"}} {count}')

        lines.extend(
            [
                "# HELP spatialgrid_orgs_indexed Number of organizations currently indexed.",
                "# TYPE spatialgrid_orgs_indexed gauge",
                f"spatialgrid_orgs_indexed {orgs_indexed}",
                "# HELP spatialgrid_facilities_indexed_total "
                "Total number of facilities currently indexed.",
                "# TYPE spatialgrid_facilities_indexed_total gauge",
                f"spatialgrid_facilities_indexed_total {facilities_total}",
            ]
        )
        return "\n".join(lines) + "\n"
