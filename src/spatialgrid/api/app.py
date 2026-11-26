"""Starlette HTTP API gateway with Bearer token auth, tenant enforcement, and size limits."""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any

import anyio
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from spatialgrid.api.metrics import MetricsRegistry
from spatialgrid.api.workspace import WORKSPACE_HTML
from spatialgrid.exceptions import (
    InvalidGeometryError,
    SolverInfeasibleError,
    SpatialGridError,
    TenantIsolationError,
)
from spatialgrid.index import SpatialIndex
from spatialgrid.mcp_tools import tool_ingest_geojson, tool_list_facilities, tool_remove_facility
from spatialgrid.models import (
    CoverageGapRequest,
    NearestFacilitiesRequest,
    OptimizeDispatchRequest,
)
from spatialgrid.solver import optimize_dispatch, routes_to_geojson

logger = logging.getLogger("spatialgrid.api")

DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB


class _IngestFacilitiesPayload(BaseModel):
    """Input contract for POST /v1/facilities/ingest."""

    org_id: str = Field(..., min_length=1)
    geojson: dict[str, Any]


class _SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter keyed by org_id."""

    def __init__(self, limit_rpm: int, window_seconds: float = 60.0) -> None:
        self._limit = limit_rpm
        self._window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Return True if the request is within the rate limit, False otherwise."""
        if self._limit <= 0:
            return False
        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            timestamps = self._buckets[key]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self._limit:
                return False
            timestamps.append(now)
            return True


def _authenticate_request(
    request: Request,
    api_keys: dict[str, str],
) -> tuple[str | None, JSONResponse | None]:
    """Validate Authorization: Bearer <token> using constant-time comparison."""
    auth_header = request.headers.get("authorization")
    if not auth_header:
        return None, JSONResponse({"error": "Missing Authorization header."}, status_code=401)

    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None, JSONResponse({"error": "Invalid Authorization header."}, status_code=401)

    matched_org: str | None = None
    for candidate_key, org_id in api_keys.items():
        if hmac.compare_digest(token, candidate_key):
            matched_org = org_id

    if matched_org is None:
        return None, JSONResponse({"error": "Invalid API token."}, status_code=401)
    return matched_org, None


async def _parse_json_body(
    request: Request,
    max_body_bytes: int,
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Parse bounded request body as a JSON object, returning 413/400 on failure."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_body_bytes:
                return None, JSONResponse(
                    {
                        "error": f"Request body exceeds {max_body_bytes} bytes limit.",
                        "error_type": "PayloadTooLarge",
                    },
                    status_code=413,
                )
        except ValueError:
            pass

    body_bytes = await request.body()
    if len(body_bytes) > max_body_bytes:
        return None, JSONResponse(
            {
                "error": f"Request body exceeds {max_body_bytes} bytes limit.",
                "error_type": "PayloadTooLarge",
            },
            status_code=413,
        )

    try:
        raw = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return None, JSONResponse(
            {"error": f"Invalid JSON body: {exc}", "error_type": "InvalidJSON"},
            status_code=400,
        )
    if not isinstance(raw, dict):
        return None, JSONResponse(
            {"error": "JSON body must be an object.", "error_type": "InvalidJSON"},
            status_code=400,
        )
    return raw, None


def _error_response(exc: Exception) -> JSONResponse:
    """Map domain and validation exceptions to HTTP status codes."""
    if isinstance(exc, ValidationError):
        return JSONResponse(
            {"error": "Validation error.", "details": exc.errors()},
            status_code=422,
        )
    if isinstance(exc, TenantIsolationError):
        return JSONResponse(
            {"error": str(exc), "error_type": type(exc).__name__},
            status_code=403,
        )
    if isinstance(exc, SolverInfeasibleError):
        return JSONResponse(
            {"error": str(exc), "error_type": type(exc).__name__},
            status_code=422,
        )
    if isinstance(exc, (InvalidGeometryError, SpatialGridError)):
        return JSONResponse(
            {"error": str(exc), "error_type": type(exc).__name__},
            status_code=400,
        )
    raise exc


def _check_org_match(authed_org: str | None, target_org: str | None) -> JSONResponse | None:
    """Verify authenticated org_id matches the requested org_id."""
    if authed_org is not None and target_org is not None and target_org != authed_org:
        return JSONResponse(
            {
                "error": (
                    f"Authenticated org '{authed_org}' does not match "
                    f"requested org_id '{target_org}'."
                ),
                "error_type": "TenantIsolationError",
            },
            status_code=403,
        )
    return None


def create_http_app(
    index: SpatialIndex | None = None,
    api_keys: dict[str, str] | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    on_shutdown: Callable[[], None] | None = None,
    rate_limit_rpm: int | None = None,
) -> Starlette:
    """Create the SpatialGrid Starlette HTTP API application."""
    active_index = index if index is not None else SpatialIndex()
    metrics = MetricsRegistry()
    rate_limiter = (
        _SlidingWindowRateLimiter(rate_limit_rpm) if rate_limit_rpm is not None else None
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if on_shutdown is not None:
                on_shutdown()

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "spatialgrid"}, status_code=200)

    async def root_redirect(_request: Request) -> RedirectResponse:
        return RedirectResponse(url="/workspace", status_code=302)

    async def workspace(_request: Request) -> HTMLResponse:
        return HTMLResponse(WORKSPACE_HTML, status_code=200)

    async def readyz(_request: Request) -> JSONResponse:
        return JSONResponse(
            {"status": "ready", "orgs_indexed": len(active_index.org_facility_counts())},
            status_code=200,
        )

    async def metrics_endpoint(_request: Request) -> PlainTextResponse:
        counts = active_index.org_facility_counts()
        return PlainTextResponse(
            metrics.render_prometheus(
                orgs_indexed=len(counts),
                facilities_total=sum(counts.values()),
            ),
            status_code=200,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    def _with_observability(
        handler: Callable[[Request, str | None], Awaitable[JSONResponse]],
    ) -> Callable[[Request], Awaitable[JSONResponse]]:
        async def wrapped(request: Request) -> JSONResponse:
            request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
            start = time.perf_counter()
            authed_org: str | None = None
            if api_keys is not None:
                authed_org, auth_err = _authenticate_request(request, api_keys)
                if auth_err is not None:
                    auth_err.headers["x-request-id"] = request_id
                    duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
                    metrics.observe_request(
                        request.method,
                        request.url.path,
                        auth_err.status_code,
                        duration_ms,
                    )
                    return auth_err

            if rate_limiter is not None and not rate_limiter.allow(authed_org or "anonymous"):
                rate_err = JSONResponse(
                    {
                        "error": "Rate limit exceeded.",
                        "error_type": "RateLimitExceeded",
                    },
                    status_code=429,
                    headers={"Retry-After": "60", "x-request-id": request_id},
                )
                duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
                metrics.observe_request(
                    request.method,
                    request.url.path,
                    rate_err.status_code,
                    duration_ms,
                )
                return rate_err

            response = await handler(request, authed_org)
            duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
            response.headers["x-request-id"] = request_id
            metrics.observe_request(
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            logger.info(
                "http_request method=%s path=%s status=%d duration_ms=%.3f org_id=%s request_id=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                authed_org or "anonymous",
                request_id,
            )
            return response

        return wrapped

    def _v1_post(
        handler: Callable[[dict[str, Any], Request], JSONResponse],
    ) -> Callable[[Request], Awaitable[JSONResponse]]:
        async def _inner(request: Request, authed_org: str | None) -> JSONResponse:
            payload, parse_err = await _parse_json_body(request, max_body_bytes=max_body_bytes)
            if parse_err is not None or payload is None:
                assert parse_err is not None
                return parse_err

            payload_org = payload.get("org_id")
            if isinstance(payload_org, str):
                mismatch = _check_org_match(authed_org, payload_org)
                if mismatch is not None:
                    return mismatch

            try:
                return await anyio.to_thread.run_sync(handler, payload, request)
            except (ValidationError, SpatialGridError) as exc:
                return _error_response(exc)

        return _with_observability(_inner)

    async def _handle_list_facilities(request: Request, authed_org: str | None) -> JSONResponse:
        org_id = request.query_params.get("org_id") or authed_org
        if not org_id:
            return JSONResponse(
                {"error": "Missing required query parameter 'org_id'."},
                status_code=400,
            )
        mismatch = _check_org_match(authed_org, org_id)
        if mismatch is not None:
            return mismatch
        only_active = request.query_params.get("only_active", "false").lower() == "true"
        result = tool_list_facilities(active_index, org_id=org_id, only_active=only_active)
        return JSONResponse(result, status_code=200)

    async def _handle_delete_facility(request: Request, authed_org: str | None) -> JSONResponse:
        facility_id = str(request.path_params["facility_id"])
        org_id = request.query_params.get("org_id") or authed_org
        if not org_id:
            return JSONResponse(
                {"error": "Missing required query parameter 'org_id'."},
                status_code=400,
            )
        mismatch = _check_org_match(authed_org, org_id)
        if mismatch is not None:
            return mismatch
        result = tool_remove_facility(active_index, org_id=org_id, facility_id=facility_id)
        if not result["removed"]:
            return JSONResponse(
                {
                    "error": f"Facility '{facility_id}' not found for org '{org_id}'.",
                    "error_type": "NotFound",
                },
                status_code=404,
            )
        return JSONResponse(result, status_code=200)

    def _handle_ingest(payload: dict[str, Any], _request: Request) -> JSONResponse:
        req = _IngestFacilitiesPayload.model_validate(payload)
        result = tool_ingest_geojson(active_index, req.org_id, req.geojson)
        return JSONResponse(result, status_code=200)

    def _handle_nearest(payload: dict[str, Any], _request: Request) -> JSONResponse:
        req = NearestFacilitiesRequest.model_validate(payload)
        result = active_index.nearest_facilities(req)
        return JSONResponse(result.model_dump(), status_code=200)

    def _handle_coverage_gap(payload: dict[str, Any], _request: Request) -> JSONResponse:
        req = CoverageGapRequest.model_validate(payload)
        result = active_index.coverage_gap(req)
        return JSONResponse(result.model_dump(), status_code=200)

    def _handle_optimize_dispatch(payload: dict[str, Any], request: Request) -> JSONResponse:
        req = OptimizeDispatchRequest.model_validate(payload)
        depot = active_index.get_facility(req.org_id, req.depot_facility_id)
        if depot is None:
            return JSONResponse(
                {
                    "error": (
                        f"Depot facility '{req.depot_facility_id}' not found "
                        f"for org '{req.org_id}'."
                    ),
                    "error_type": "NotFound",
                },
                status_code=404,
            )
        result = optimize_dispatch(req, depot)
        if request.query_params.get("format") == "geojson":
            return JSONResponse(routes_to_geojson(result, depot), status_code=200)
        return JSONResponse(result.model_dump(), status_code=200)

    async def _handle_h3_summary(request: Request, authed_org: str | None) -> JSONResponse:
        org_id = request.query_params.get("org_id") or authed_org
        if not org_id:
            return JSONResponse(
                {"error": "Missing required query parameter 'org_id'."},
                status_code=400,
            )
        mismatch = _check_org_match(authed_org, org_id)
        if mismatch is not None:
            return mismatch
        facilities = active_index.list_facilities(org_id)
        agg: dict[str, dict[str, int]] = {}
        for fac in facilities:
            cell = fac.h3_index or "unknown"
            entry = agg.setdefault(cell, {"facility_count": 0, "total_capacity": 0})
            entry["facility_count"] += 1
            entry["total_capacity"] += fac.capacity
        cells = [
            {
                "h3_index": cell_id,
                "facility_count": stats["facility_count"],
                "total_capacity": stats["total_capacity"],
            }
            for cell_id, stats in sorted(agg.items())
        ]
        return JSONResponse({"org_id": org_id, "cells": cells}, status_code=200)

    routes = [
        Route("/", root_redirect, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/readyz", readyz, methods=["GET"]),
        Route("/metrics", metrics_endpoint, methods=["GET"]),
        Route("/workspace", workspace, methods=["GET"]),
        Route("/v1/h3/summary", _with_observability(_handle_h3_summary), methods=["GET"]),
        Route("/v1/facilities", _with_observability(_handle_list_facilities), methods=["GET"]),
        Route(
            "/v1/facilities/{facility_id}",
            _with_observability(_handle_delete_facility),
            methods=["DELETE"],
        ),
        Route("/v1/facilities/ingest", _v1_post(_handle_ingest), methods=["POST"]),
        Route("/v1/facilities/nearest", _v1_post(_handle_nearest), methods=["POST"]),
        Route("/v1/coverage-gap", _v1_post(_handle_coverage_gap), methods=["POST"]),
        Route("/v1/dispatch/optimize", _v1_post(_handle_optimize_dispatch), methods=["POST"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan)
