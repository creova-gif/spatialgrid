"""OpenAPI 3.1.0 specification builder for the SpatialGrid HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from spatialgrid.models import (
    CoverageGapRequest,
    CoverageGapResponse,
    IngestGeoJSONResponse,
    NearestFacilitiesRequest,
    NearestFacilitiesResponse,
    OptimizeDispatchRequest,
    OptimizeDispatchResponse,
)

_SCHEMA_MODELS: tuple[type[BaseModel], ...] = (
    NearestFacilitiesRequest,
    NearestFacilitiesResponse,
    CoverageGapRequest,
    CoverageGapResponse,
    OptimizeDispatchRequest,
    OptimizeDispatchResponse,
    IngestGeoJSONResponse,
)


def _build_component_schemas() -> dict[str, Any]:
    """Generate OpenAPI component schemas from SpatialGrid Pydantic models."""
    schemas: dict[str, Any] = {}
    for model in _SCHEMA_MODELS:
        raw = model.model_json_schema(ref_template="#/components/schemas/{model}")
        defs = raw.pop("$defs", None)
        if isinstance(defs, dict):
            for def_name, def_schema in defs.items():
                schemas.setdefault(def_name, def_schema)
        schemas[model.__name__] = raw
    return schemas


def _json_content(schema_ref: str) -> dict[str, Any]:
    return {"application/json": {"schema": {"$ref": schema_ref}}}


def _error_responses(*statuses: tuple[str, str]) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for code, desc in statuses:
        responses[code] = {
            "description": desc,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {"type": "string"},
                            "error_type": {"type": "string"},
                        },
                        "required": ["error"],
                    }
                }
            },
        }
    return responses


def build_openapi_spec(version: str = "0.2.0") -> dict[str, Any]:
    """Build a valid OpenAPI 3.1.0 specification for the SpatialGrid HTTP API."""
    auth_errors = (
        ("401", "Missing or invalid Bearer token."),
        ("403", "Authenticated org_id does not match requested org_id."),
        ("429", "Rate limit exceeded."),
    )
    bearer_security: list[dict[str, list[str]]] = [{"BearerAuth": []}]

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "SpatialGrid API",
            "version": version,
            "description": (
                "Multi-tenant geospatial indexing, coverage gap analysis, and "
                "deterministic OR-Tools CVRP dispatch optimization API."
            ),
        },
        "components": {
            "schemas": _build_component_schemas(),
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                }
            },
        },
        "paths": {
            "/healthz": {
                "get": {
                    "summary": "Liveness probe",
                    "responses": {
                        "200": {
                            "description": "Service is alive.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "service": {"type": "string"},
                                        },
                                        "required": ["status", "service"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/readyz": {
                "get": {
                    "summary": "Readiness probe",
                    "responses": {
                        "200": {
                            "description": "Service is ready.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "orgs_indexed": {"type": "integer"},
                                        },
                                        "required": ["status", "orgs_indexed"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/metrics": {
                "get": {
                    "summary": "Prometheus metrics",
                    "responses": {
                        "200": {
                            "description": "Prometheus text exposition format.",
                            "content": {
                                "text/plain; version=0.0.4; charset=utf-8": {
                                    "schema": {"type": "string"}
                                }
                            },
                        }
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "summary": "OpenAPI 3.1.0 specification",
                    "responses": {
                        "200": {
                            "description": "OpenAPI JSON document.",
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        }
                    },
                }
            },
            "/workspace": {
                "get": {
                    "summary": "Interactive SpatialGrid web workspace",
                    "responses": {
                        "200": {
                            "description": "HTML workspace page.",
                            "content": {"text/html": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/v1/facilities": {
                "get": {
                    "summary": "List facilities for an organization",
                    "security": bearer_security,
                    "parameters": [
                        {
                            "name": "org_id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "only_active",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean", "default": False},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "List of facilities.",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        },
                        **_error_responses(("400", "Missing org_id."), *auth_errors),
                    },
                }
            },
            "/v1/facilities/{facility_id}": {
                "delete": {
                    "summary": "Remove a facility by ID",
                    "security": bearer_security,
                    "parameters": [
                        {
                            "name": "facility_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "org_id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Facility removed.",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        },
                        **_error_responses(
                            ("400", "Missing org_id."),
                            ("404", "Facility not found."),
                            *auth_errors,
                        ),
                    },
                }
            },
            "/v1/facilities/ingest": {
                "post": {
                    "summary": "Ingest GeoJSON facilities into the spatial index",
                    "security": bearer_security,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "org_id": {"type": "string", "minLength": 1},
                                        "geojson": {"type": "object"},
                                    },
                                    "required": ["org_id", "geojson"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "GeoJSON ingestion result.",
                            "content": _json_content("#/components/schemas/IngestGeoJSONResponse"),
                        },
                        **_error_responses(
                            ("400", "Invalid JSON or geometry."),
                            ("413", "Payload too large."),
                            ("422", "Validation error."),
                            *auth_errors,
                        ),
                    },
                }
            },
            "/v1/facilities/nearest": {
                "post": {
                    "summary": "Find nearest qualifying facilities",
                    "security": bearer_security,
                    "requestBody": {
                        "required": True,
                        "content": _json_content("#/components/schemas/NearestFacilitiesRequest"),
                    },
                    "responses": {
                        "200": {
                            "description": "Ranked nearest facilities.",
                            "content": _json_content(
                                "#/components/schemas/NearestFacilitiesResponse"
                            ),
                        },
                        **_error_responses(
                            ("400", "Invalid request."),
                            ("413", "Payload too large."),
                            ("422", "Validation error."),
                            *auth_errors,
                        ),
                    },
                }
            },
            "/v1/coverage-gap": {
                "post": {
                    "summary": "Compute demand coverage gap",
                    "security": bearer_security,
                    "requestBody": {
                        "required": True,
                        "content": _json_content("#/components/schemas/CoverageGapRequest"),
                    },
                    "responses": {
                        "200": {
                            "description": "Coverage gap analysis result.",
                            "content": _json_content("#/components/schemas/CoverageGapResponse"),
                        },
                        **_error_responses(
                            ("400", "Invalid request."),
                            ("413", "Payload too large."),
                            ("422", "Validation error."),
                            *auth_errors,
                        ),
                    },
                }
            },
            "/v1/dispatch/optimize": {
                "post": {
                    "summary": "Optimize CVRP vehicle dispatch routes",
                    "security": bearer_security,
                    "parameters": [
                        {
                            "name": "format",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "enum": ["json", "geojson"]},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": _json_content("#/components/schemas/OptimizeDispatchRequest"),
                    },
                    "responses": {
                        "200": {
                            "description": "Optimized vehicle routes.",
                            "content": _json_content(
                                "#/components/schemas/OptimizeDispatchResponse"
                            ),
                        },
                        **_error_responses(
                            ("400", "Invalid request."),
                            ("404", "Depot facility not found."),
                            ("413", "Payload too large."),
                            ("422", "Validation error or infeasible solver."),
                            *auth_errors,
                        ),
                    },
                }
            },
            "/v1/h3/summary": {
                "get": {
                    "summary": "Summarize facilities grouped by H3 cell",
                    "security": bearer_security,
                    "parameters": [
                        {
                            "name": "org_id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Per-H3-cell facility counts and capacities.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "org_id": {"type": "string"},
                                            "cells": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "h3_index": {"type": "string"},
                                                        "facility_count": {"type": "integer"},
                                                        "total_capacity": {"type": "integer"},
                                                    },
                                                    "required": [
                                                        "h3_index",
                                                        "facility_count",
                                                        "total_capacity",
                                                    ],
                                                },
                                            },
                                        },
                                        "required": ["org_id", "cells"],
                                    }
                                }
                            },
                        },
                        **_error_responses(("400", "Missing org_id."), *auth_errors),
                    },
                }
            },
        },
    }
