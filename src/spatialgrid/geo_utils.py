"""Shared geodesic math utilities for SpatialGrid."""

from __future__ import annotations

import math

from spatialgrid.models import GeoPoint

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Compute exact great-circle distance in kilometers between two WGS84 points."""
    lat1 = math.radians(a.lat)
    lon1 = math.radians(a.lon)
    lat2 = math.radians(b.lat)
    lon2 = math.radians(b.lon)

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    sin_dlat = math.sin(dlat / 2.0)
    sin_dlon = math.sin(dlon / 2.0)
    h = sin_dlat * sin_dlat + math.cos(lat1) * math.cos(lat2) * sin_dlon * sin_dlon
    c = 2.0 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1.0 - h)))
    return EARTH_RADIUS_KM * c
