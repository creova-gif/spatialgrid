"""Unit tests for SQLiteFacilityStore and SpatialIndex persistence lifecycle."""

from __future__ import annotations

from pathlib import Path

from spatialgrid.index import SpatialIndex, point_to_h3
from spatialgrid.models import Facility, GeoPoint, NearestFacilitiesRequest
from spatialgrid.storage import SQLiteFacilityStore


def _make_facility(
    facility_id: str,
    org_id: str,
    lat: float,
    lon: float,
    *,
    capacity: int = 100,
    facility_type: str = "warehouse",
    active: bool = True,
) -> Facility:
    return Facility(
        facility_id=facility_id,
        org_id=org_id,
        name=f"Name {facility_id}",
        location=GeoPoint(lat=lat, lon=lon),
        capacity=capacity,
        facility_type=facility_type,
        active=active,
    )


def test_sqlite_store_upsert_delete_count_and_reload(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "facilities.db"
    store = SQLiteFacilityStore(db_path)

    assert db_path.exists()
    assert store.upsert_facilities([]) == 0
    assert store.load_all_facilities() == []
    assert store.count_by_org() == {}

    fac_a1 = _make_facility("f-1", "org-a", 41.8781, -87.6298, capacity=120)
    fac_a2 = _make_facility("f-2", "org-a", 41.8900, -87.6350, capacity=200, active=False)
    fac_b1 = _make_facility("f-1", "org-b", 40.7128, -74.0060, capacity=300)

    assert store.upsert_facilities([fac_a1, fac_a2, fac_b1]) == 3
    assert store.count_by_org() == {"org-a": 2, "org-b": 1}

    # Upsert update on existing (org_id, facility_id)
    fac_a1_updated = fac_a1.model_copy(update={"capacity": 999})
    assert store.upsert_facilities([fac_a1_updated]) == 1
    assert store.count_by_org() == {"org-a": 2, "org-b": 1}

    # Delete missing vs existing
    assert store.delete_facility("org-a", "missing") is False
    assert store.delete_facility("org-a", "f-2") is True
    assert store.count_by_org() == {"org-a": 1, "org-b": 1}
    store.close()

    # Reload from a new store instance backed by the same DB file
    reloaded_store = SQLiteFacilityStore(db_path)
    loaded = reloaded_store.load_all_facilities()
    assert len(loaded) == 2
    by_key = {(fac.org_id, fac.facility_id): fac for fac in loaded}
    assert by_key[("org-a", "f-1")].capacity == 999
    assert by_key[("org-b", "f-1")].capacity == 300
    reloaded_store.close()


def test_spatial_index_persists_across_restarts_with_tenant_isolation(tmp_path: Path) -> None:
    db_path = tmp_path / "spatial.db"
    store1 = SQLiteFacilityStore(db_path)
    index1 = SpatialIndex(h3_resolution=7, store=store1)

    index1.add_facilities(
        [
            _make_facility("f-1", "org-alpha", 41.8781, -87.6298, capacity=150),
            _make_facility("f-2", "org-alpha", 41.8900, -87.6350, capacity=250),
            _make_facility("f-1", "org-beta", 40.7128, -74.0060, capacity=350),
        ]
    )
    assert index1.remove_facility("org-alpha", "f-2") is True
    assert index1.remove_facility("org-alpha", "nonexistent") is False
    store1.close()

    # Brand new SpatialIndex backed by the same SQLite file
    store2 = SQLiteFacilityStore(db_path)
    index2 = SpatialIndex(h3_resolution=7, store=store2)

    assert index2.org_facility_counts() == {"org-alpha": 1, "org-beta": 1}
    assert index2.get_facility("org-alpha", "f-2") is None
    alpha_f1 = index2.get_facility("org-alpha", "f-1")
    beta_f1 = index2.get_facility("org-beta", "f-1")
    assert alpha_f1 is not None and beta_f1 is not None
    assert alpha_f1.h3_index == point_to_h3(alpha_f1.location, resolution=7)
    assert beta_f1.capacity == 350

    # Tenant isolation on nearest_facilities after reload
    nearest_alpha = index2.nearest_facilities(
        NearestFacilitiesRequest(
            org_id="org-alpha",
            origin=GeoPoint(lat=40.7128, lon=-74.0060),
            limit=5,
        )
    )
    assert [m.facility.facility_id for m in nearest_alpha.matches] == ["f-1"]
    assert nearest_alpha.matches[0].facility.org_id == "org-alpha"

    nearest_beta = index2.nearest_facilities(
        NearestFacilitiesRequest(
            org_id="org-beta",
            origin=GeoPoint(lat=40.7128, lon=-74.0060),
            limit=5,
        )
    )
    assert [m.facility.facility_id for m in nearest_beta.matches] == ["f-1"]
    assert nearest_beta.matches[0].facility.org_id == "org-beta"
    store2.close()
