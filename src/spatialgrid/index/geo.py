"""Geospatial utilities for H3 indexing and GeoJSON facility loading."""

from __future__ import annotations

import random
from typing import Any

import h3
from pydantic import ValidationError

from spatialgrid.exceptions import InvalidGeometryError
from spatialgrid.models import Facility, GeoPoint

DEFAULT_H3_RESOLUTION = 7
_DEFAULT_CENTER = GeoPoint(lat=41.8781, lon=-87.6298)
_SYNTHETIC_TYPES = ("warehouse", "depot", "hub", "substation")


def point_to_h3(point: GeoPoint, resolution: int = DEFAULT_H3_RESOLUTION) -> str:
    """Convert a WGS84 GeoPoint to an H3 cell ID at the given resolution."""
    return str(h3.latlng_to_cell(point.lat, point.lon, resolution))


def h3_grid_distance(cell_a: str, cell_b: str) -> int | None:
    """Return H3 grid ring distance between two cells, or None if invalid or too far apart."""
    try:
        return int(h3.grid_distance(cell_a, cell_b))
    except (h3.H3BaseException, ValueError):
        return None


def _parse_point_coordinates(geometry: Any, index: int) -> GeoPoint:
    """Validate and convert a GeoJSON Point geometry into a GeoPoint."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise InvalidGeometryError(f"Feature at index {index} must have a Point geometry.")

    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        raise InvalidGeometryError(
            f"Feature at index {index} Point coordinates must be [lon, lat]."
        )

    lon_raw, lat_raw = coords[0], coords[1]
    if isinstance(lon_raw, bool) or isinstance(lat_raw, bool):
        raise InvalidGeometryError(f"Feature at index {index} has boolean coordinates.")
    if not isinstance(lon_raw, (int, float)) or not isinstance(lat_raw, (int, float)):
        raise InvalidGeometryError(f"Feature at index {index} has non-numeric coordinates.")

    try:
        return GeoPoint(lat=float(lat_raw), lon=float(lon_raw))
    except ValidationError as exc:
        raise InvalidGeometryError(
            f"Feature at index {index} has out-of-range coordinates."
        ) from exc


def load_facilities_geojson(payload: dict[str, Any], default_org_id: str) -> list[Facility]:
    """Parse a GeoJSON FeatureCollection of Point features into Facility models."""
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise InvalidGeometryError("GeoJSON payload must be a FeatureCollection.")

    features = payload.get("features")
    if not isinstance(features, list):
        raise InvalidGeometryError("GeoJSON FeatureCollection must contain a 'features' list.")

    facilities: list[Facility] = []
    for idx, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise InvalidGeometryError(f"Item at index {idx} must be a GeoJSON Feature.")

        location = _parse_point_coordinates(feature.get("geometry"), idx)

        raw_props = feature.get("properties")
        if raw_props is None:
            props: dict[str, Any] = {}
        elif isinstance(raw_props, dict):
            props = raw_props
        else:
            raise InvalidGeometryError(f"Feature at index {idx} properties must be a mapping.")

        raw_id = feature.get("id") if feature.get("id") is not None else props.get("facility_id")
        if raw_id is None or not str(raw_id).strip():
            raise InvalidGeometryError(
                f"Feature at index {idx} must define 'id' or 'properties.facility_id'."
            )
        facility_id = str(raw_id)

        raw_name = props.get("name")
        name = str(raw_name) if raw_name is not None and str(raw_name).strip() else facility_id

        raw_org = props.get("org_id")
        org_id = str(raw_org) if raw_org is not None and str(raw_org).strip() else default_org_id

        raw_metadata = props.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

        try:
            facility = Facility(
                facility_id=facility_id,
                org_id=org_id,
                name=name,
                location=location,
                capacity=props.get("capacity", 0),
                facility_type=props.get("facility_type", "warehouse"),
                active=props.get("active", True),
                h3_index=point_to_h3(location),
                metadata=metadata,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise InvalidGeometryError(
                f"Feature at index {idx} has invalid facility attributes: {exc}"
            ) from exc

        facilities.append(facility)

    return facilities


def generate_synthetic_facilities(
    org_id: str,
    count: int = 25,
    seed: int = 42,
    center: GeoPoint | None = None,
) -> list[Facility]:
    """Generate a deterministic synthetic urban facility dataset around a center point."""
    if count < 0:
        raise ValueError("count must be non-negative.")

    origin = center or _DEFAULT_CENTER
    rng = random.Random(seed)
    facilities: list[Facility] = []

    for i in range(count):
        dlat = rng.uniform(-0.10, 0.10)
        dlon = rng.uniform(-0.12, 0.12)
        lat = max(-90.0, min(90.0, origin.lat + dlat))
        lon = max(-180.0, min(180.0, origin.lon + dlon))
        location = GeoPoint(lat=round(lat, 6), lon=round(lon, 6))
        facility_type = _SYNTHETIC_TYPES[i % len(_SYNTHETIC_TYPES)]
        capacity = rng.randint(50, 500)
        active = (i % 10) != 9

        facilities.append(
            Facility(
                facility_id=f"{org_id}-fac-{i:03d}",
                org_id=org_id,
                name=f"Synthetic {facility_type.title()} {i:03d}",
                location=location,
                capacity=capacity,
                facility_type=facility_type,
                active=active,
                h3_index=point_to_h3(location),
                metadata={"synthetic": True, "seed": seed},
            )
        )

    return facilities
