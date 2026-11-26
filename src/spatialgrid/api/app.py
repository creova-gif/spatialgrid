"""Starlette HTTP API gateway with optional Bearer token auth and tenant enforcement."""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from spatialgrid.exceptions import (
    InvalidGeometryError,
    SolverInfeasibleError,
    SpatialGridError,
    TenantIsolationError,
)
from spatialgrid.index import SpatialIndex
from spatialgrid.mcp_tools import tool_ingest_geojson
from spatialgrid.models import (
    CoverageGapRequest,
    NearestFacilitiesRequest,
    OptimizeDispatchRequest,
)
from spatialgrid.solver import optimize_dispatch


class _IngestFacilitiesPayload(BaseModel):
    """Input contract for POST /v1/facilities/ingest."""

    org_id: str = Field(..., min_length=1)
    geojson: dict[str, Any]


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


async def _parse_json_body(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Parse request body as a JSON object, returning a 400 response on failure."""
    try:
        raw = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
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


def create_http_app(
    index: SpatialIndex | None = None,
    api_keys: dict[str, str] | None = None,
) -> Starlette:
    """Create the SpatialGrid Starlette HTTP API application."""
    active_index = index if index is not None else SpatialIndex()

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "spatialgrid"}, status_code=200)

    def _v1_endpoint(
        handler: Callable[[dict[str, Any]], JSONResponse],
    ) -> Callable[[Request], Awaitable[JSONResponse]]:
        async def endpoint(request: Request) -> JSONResponse:
            authed_org: str | None = None
            if api_keys is not None:
                authed_org, auth_err = _authenticate_request(request, api_keys)
                if auth_err is not None:
                    return auth_err

            payload, parse_err = await _parse_json_body(request)
            if parse_err is not None or payload is None:
                assert parse_err is not None
                return parse_err

            payload_org = payload.get("org_id")
            if (
                authed_org is not None
                and isinstance(payload_org, str)
                and payload_org != authed_org
            ):
                return JSONResponse(
                    {
                        "error": (
                            f"Authenticated org '{authed_org}' does not match "
                            f"payload org_id '{payload_org}'."
                        ),
                        "error_type": "TenantIsolationError",
                    },
                    status_code=403,
                )

            try:
                return handler(payload)
            except (ValidationError, SpatialGridError) as exc:
                return _error_response(exc)

        return endpoint

    def _handle_ingest(payload: dict[str, Any]) -> JSONResponse:
        req = _IngestFacilitiesPayload.model_validate(payload)
        result = tool_ingest_geojson(active_index, req.org_id, req.geojson)
        return JSONResponse(result, status_code=200)

    def _handle_nearest(payload: dict[str, Any]) -> JSONResponse:
        req = NearestFacilitiesRequest.model_validate(payload)
        result = active_index.nearest_facilities(req)
        return JSONResponse(result.model_dump(), status_code=200)

    def _handle_coverage_gap(payload: dict[str, Any]) -> JSONResponse:
        req = CoverageGapRequest.model_validate(payload)
        result = active_index.coverage_gap(req)
        return JSONResponse(result.model_dump(), status_code=200)

    def _handle_optimize_dispatch(payload: dict[str, Any]) -> JSONResponse:
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
        return JSONResponse(result.model_dump(), status_code=200)

    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/v1/facilities/ingest", _v1_endpoint(_handle_ingest), methods=["POST"]),
        Route("/v1/facilities/nearest", _v1_endpoint(_handle_nearest), methods=["POST"]),
        Route("/v1/coverage-gap", _v1_endpoint(_handle_coverage_gap), methods=["POST"]),
        Route("/v1/dispatch/optimize", _v1_endpoint(_handle_optimize_dispatch), methods=["POST"]),
    ]
    return Starlette(routes=routes)
