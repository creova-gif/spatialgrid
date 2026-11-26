"""Spatial indexing package combining H3 hierarchical grids and Shapely STRtree."""

from spatialgrid.index.geo import (
    DEFAULT_H3_RESOLUTION,
    generate_synthetic_facilities,
    h3_grid_distance,
    load_facilities_geojson,
    point_to_h3,
)
from spatialgrid.index.spatial_index import SpatialIndex

__all__ = [
    "DEFAULT_H3_RESOLUTION",
    "SpatialIndex",
    "generate_synthetic_facilities",
    "h3_grid_distance",
    "load_facilities_geojson",
    "point_to_h3",
]
