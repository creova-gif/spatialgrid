"""Tenant-isolated H3 + Shapely STRtree spatial index for SpatialGrid."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError
from shapely.geometry import Point, box
from shapely.strtree import STRtree

from spatialgrid.exceptions import InvalidGeometryError, TenantIsolationError
from spatialgrid.geo_utils import haversine_km
from spatialgrid.index.geo import (
    DEFAULT_H3_RESOLUTION,
    h3_grid_disk,
    h3_grid_distance,
    point_to_h3,
)
from spatialgrid.models import (
    CoverageGapRequest,
    CoverageGapResponse,
    Facility,
    FacilityMatch,
    GeoPoint,
    NearestFacilitiesRequest,
    NearestFacilitiesResponse,
    UncoveredDemand,
)
from spatialgrid.storage import SQLiteFacilityStore

_KM_PER_DEGREE_LAT = 110.574


class SpatialIndex:
    """In-memory, tenant-isolated spatial index backed by Shapely STRtree and H3."""

    def __init__(
        self,
        h3_resolution: int = DEFAULT_H3_RESOLUTION,
        store: SQLiteFacilityStore | None = None,
    ) -> None:
        self._h3_resolution = h3_resolution
        self._store = store
        self._lock = threading.RLock()
        self._facilities_by_org: dict[str, dict[str, Facility]] = {}
        self._facility_lists_by_org: dict[str, list[Facility]] = {}
        self._trees_by_org: dict[str, STRtree] = {}

        if self._store is not None:
            existing = self._store.load_all_facilities()
            if existing:
                with self._lock:
                    self._index_in_memory(existing)

    def _index_in_memory(self, facilities: list[Facility]) -> list[Facility]:
        """Assign H3 cells, update in-memory maps, and rebuild affected per-org STRtrees."""
        indexed_facilities: list[Facility] = []
        affected_orgs: set[str] = set()
        for facility in facilities:
            h3_cell = point_to_h3(facility.location, self._h3_resolution)
            indexed = facility.model_copy(update={"h3_index": h3_cell})
            org_store = self._facilities_by_org.setdefault(facility.org_id, {})
            org_store[facility.facility_id] = indexed
            indexed_facilities.append(indexed)
            affected_orgs.add(facility.org_id)

        for org_id in affected_orgs:
            self._rebuild_org_tree(org_id)

        return indexed_facilities

    def add_facilities(self, facilities: list[Facility]) -> int:
        """Index facilities grouped by org_id, rebuilding affected per-org STRtrees."""
        if not facilities:
            return 0

        with self._lock:
            indexed_facilities = self._index_in_memory(facilities)
            if self._store is not None:
                self._store.upsert_facilities(indexed_facilities)

        return len(facilities)

    def remove_facility(self, org_id: str, facility_id: str) -> bool:
        """Remove a facility for org_id and rebuild or clean up that org's STRtree."""
        with self._lock:
            org_store = self._facilities_by_org.get(org_id)
            if org_store is None or facility_id not in org_store:
                return False

            org_store.pop(facility_id)
            self._rebuild_org_tree(org_id)
            if self._store is not None:
                self._store.delete_facility(org_id, facility_id)
            return True

    def get_facility(self, org_id: str, facility_id: str) -> Facility | None:
        """Return a facility for the given org_id, or None if absent."""
        with self._lock:
            return self._facilities_by_org.get(org_id, {}).get(facility_id)

    def list_facilities(self, org_id: str, only_active: bool = False) -> list[Facility]:
        """Return a deterministic list of facilities for org_id sorted by facility_id."""
        with self._lock:
            facs = self._facility_lists_by_org.get(org_id, [])
            if only_active:
                facs = [fac for fac in facs if fac.active]
            return sorted(facs, key=lambda fac: fac.facility_id)

    def facilities_in_h3_ring(
        self,
        org_id: str,
        origin: GeoPoint,
        k: int = 1,
        only_active: bool = True,
    ) -> list[Facility]:
        """Return facilities for org_id within k H3 grid steps of origin, sorted by facility_id."""
        with self._lock:
            disk = h3_grid_disk(point_to_h3(origin, self._h3_resolution), k)
            facs = self._facility_lists_by_org.get(org_id, [])
            matches = [
                fac
                for fac in facs
                if (not only_active or fac.active) and fac.h3_index in disk
            ]
            return sorted(matches, key=lambda fac: fac.facility_id)

    def _rebuild_org_tree(self, org_id: str) -> None:
        """Rebuild the Shapely STRtree for a single organization."""
        facilities = list(self._facilities_by_org.get(org_id, {}).values())
        if facilities:
            self._facility_lists_by_org[org_id] = facilities
            geoms = [Point(fac.location.lon, fac.location.lat) for fac in facilities]
            self._trees_by_org[org_id] = STRtree(geoms)
        else:
            self._facilities_by_org.pop(org_id, None)
            self._facility_lists_by_org.pop(org_id, None)
            self._trees_by_org.pop(org_id, None)

    @staticmethod
    def _candidate_indices_within_km(
        tree: STRtree,
        total_count: int,
        origin: GeoPoint,
        radius_km: float,
    ) -> list[int]:
        """Return candidate indices from STRtree within a conservative bounding box."""
        deg_lat = (radius_km / _KM_PER_DEGREE_LAT) * 1.05 + 0.01
        cos_lat = math.cos(math.radians(origin.lat))
        if cos_lat < 0.05 or deg_lat >= 90.0:
            return list(range(total_count))

        deg_lon = (radius_km / (_KM_PER_DEGREE_LAT * cos_lat)) * 1.05 + 0.01
        min_lon = origin.lon - deg_lon
        max_lon = origin.lon + deg_lon
        if min_lon < -180.0 or max_lon > 180.0:
            return list(range(total_count))

        envelope = box(
            min_lon,
            max(-90.0, origin.lat - deg_lat),
            max_lon,
            min(90.0, origin.lat + deg_lat),
        )
        return [int(idx) for idx in tree.query(envelope)]

    def nearest_facilities(self, request: NearestFacilitiesRequest) -> NearestFacilitiesResponse:
        """Find nearest facilities for request.org_id using STRtree + exact haversine_km."""
        start = time.perf_counter()
        with self._lock:
            facilities = list(self._facility_lists_by_org.get(request.org_id, []))
            tree = self._trees_by_org.get(request.org_id)

            if not facilities or tree is None:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return NearestFacilitiesResponse(
                    org_id=request.org_id,
                    origin=request.origin,
                    matches=[],
                    query_time_ms=elapsed_ms,
                )

            if request.max_distance_km is not None:
                candidate_indices = self._candidate_indices_within_km(
                    tree, len(facilities), request.origin, request.max_distance_km
                )
            else:
                candidate_indices = list(range(len(facilities)))

        origin_h3 = point_to_h3(request.origin, self._h3_resolution)
        scored: list[tuple[float, str, FacilityMatch]] = []

        for idx in candidate_indices:
            fac = facilities[idx]
            if request.only_active and not fac.active:
                continue
            if fac.capacity < request.min_capacity:
                continue
            if request.facility_type is not None and fac.facility_type != request.facility_type:
                continue

            dist_km = haversine_km(request.origin, fac.location)
            if request.max_distance_km is not None and dist_km > request.max_distance_km:
                continue

            ring_dist = (
                h3_grid_distance(origin_h3, fac.h3_index) if fac.h3_index is not None else None
            )
            scored.append(
                (
                    dist_km,
                    fac.facility_id,
                    FacilityMatch(
                        facility=fac,
                        distance_km=dist_km,
                        h3_ring_distance=ring_dist,
                    ),
                )
            )

        scored.sort(key=lambda item: (item[0], item[1]))
        matches = [item[2] for item in scored[: request.limit]]
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return NearestFacilitiesResponse(
            org_id=request.org_id,
            origin=request.origin,
            matches=matches,
            query_time_ms=elapsed_ms,
        )

    def coverage_gap(self, request: CoverageGapRequest) -> CoverageGapResponse:
        """Evaluate demand node coverage against qualifying active facilities for request.org_id."""
        start = time.perf_counter()

        for node in request.demand_nodes:
            if node.org_id != request.org_id:
                raise TenantIsolationError(
                    f"Demand node '{node.demand_id}' belongs to org '{node.org_id}', "
                    f"expected '{request.org_id}'."
                )

        with self._lock:
            org_facilities = self._facility_lists_by_org.get(request.org_id, [])
            qualifying = [
                fac
                for fac in org_facilities
                if fac.active
                and fac.capacity >= request.min_facility_capacity
                and (request.facility_type is None or fac.facility_type == request.facility_type)
            ]

            uncovered_nodes: list[UncoveredDemand] = []
            uncovered_demand_units = 0

            if not qualifying:
                for node in request.demand_nodes:
                    uncovered_nodes.append(
                        UncoveredDemand(
                            demand_node=node,
                            nearest_facility_id=None,
                            nearest_distance_km=None,
                        )
                    )
                    uncovered_demand_units += node.demand_units
            else:
                existing_tree = self._trees_by_org.get(request.org_id)
                if len(qualifying) == len(org_facilities) and existing_tree is not None:
                    qualifying_tree = existing_tree
                else:
                    qualifying_tree = STRtree(
                        [Point(fac.location.lon, fac.location.lat) for fac in qualifying]
                    )
                for node in request.demand_nodes:
                    query_pt = Point(node.location.lon, node.location.lat)
                    approx_idx = int(qualifying_tree.nearest(query_pt))
                    approx_dist_km = haversine_km(node.location, qualifying[approx_idx].location)

                    candidate_indices = self._candidate_indices_within_km(
                        qualifying_tree, len(qualifying), node.location, approx_dist_km
                    )
                    if approx_idx not in candidate_indices:
                        candidate_indices.append(approx_idx)

                    nearest_fac: Facility | None = None
                    nearest_dist_km = math.inf
                    for idx in candidate_indices:
                        fac = qualifying[idx]
                        dist_km = haversine_km(node.location, fac.location)
                        if dist_km < nearest_dist_km or (
                            dist_km == nearest_dist_km
                            and nearest_fac is not None
                            and fac.facility_id < nearest_fac.facility_id
                        ):
                            nearest_dist_km = dist_km
                            nearest_fac = fac

                    if nearest_dist_km > request.service_radius_km:
                        uncovered_nodes.append(
                            UncoveredDemand(
                                demand_node=node,
                                nearest_facility_id=(
                                    nearest_fac.facility_id if nearest_fac is not None else None
                                ),
                                nearest_distance_km=nearest_dist_km,
                            )
                        )
                        uncovered_demand_units += node.demand_units

        total_nodes = len(request.demand_nodes)
        uncovered_count = len(uncovered_nodes)
        covered_count = total_nodes - uncovered_count
        coverage_ratio = covered_count / total_nodes if total_nodes > 0 else 0.0
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return CoverageGapResponse(
            org_id=request.org_id,
            total_demand_nodes=total_nodes,
            covered_count=covered_count,
            uncovered_count=uncovered_count,
            coverage_ratio=coverage_ratio,
            uncovered_demand_units=uncovered_demand_units,
            uncovered_nodes=uncovered_nodes,
            query_time_ms=elapsed_ms,
        )

    def org_facility_counts(self) -> dict[str, int]:
        """Return indexed facility counts keyed by org_id."""
        with self._lock:
            counts: dict[str, int] = defaultdict(int)
            for org_id, facs in self._facility_lists_by_org.items():
                counts[org_id] = len(facs)
            return dict(counts)

    def save_snapshot(self, path: Path) -> int:
        """Atomically persist the spatial index state to a JSON snapshot file."""
        with self._lock:
            all_facilities = sorted(
                (
                    fac
                    for facs in self._facility_lists_by_org.values()
                    for fac in facs
                ),
                key=lambda fac: (fac.org_id, fac.facility_id),
            )
            payload = {
                "version": 1,
                "h3_resolution": self._h3_resolution,
                "facilities": [fac.model_dump(mode="json") for fac in all_facilities],
            }

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                json.dump(payload, tmp_file, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

        return len(all_facilities)

    @classmethod
    def load_snapshot(cls, path: Path) -> SpatialIndex:
        """Load and validate a SpatialIndex from a JSON snapshot file."""
        try:
            raw_text = path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidGeometryError(f"Failed to read snapshot '{path}': {exc}") from exc

        if not isinstance(payload, dict):
            raise InvalidGeometryError("Snapshot root must be a JSON object.")

        version = payload.get("version")
        if version != 1:
            raise InvalidGeometryError(f"Unsupported snapshot version: {version!r}.")

        h3_resolution = payload.get("h3_resolution")
        if (
            not isinstance(h3_resolution, int)
            or isinstance(h3_resolution, bool)
            or not (0 <= h3_resolution <= 15)
        ):
            raise InvalidGeometryError(
                f"Invalid snapshot h3_resolution: {h3_resolution!r}."
            )

        raw_facilities = payload.get("facilities")
        if not isinstance(raw_facilities, list):
            raise InvalidGeometryError("Snapshot 'facilities' must be a list.")

        facilities: list[Facility] = []
        for idx, item in enumerate(raw_facilities):
            try:
                facilities.append(Facility.model_validate(item))
            except (ValidationError, TypeError, ValueError) as exc:
                raise InvalidGeometryError(
                    f"Invalid facility at index {idx} in snapshot '{path}': {exc}"
                ) from exc

        index = cls(h3_resolution=h3_resolution)
        index.add_facilities(facilities)
        return index
