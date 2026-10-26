"""Offline Sentinel-1 ESA-plan parsing and matching checks."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from eo_overpass_predictor.core.plans import crosscheck_acquisition_plan
from eo_overpass_predictor.io import cache
from eo_overpass_predictor.io.plans import load_s1_acquisition_plans


def test_generic_plan_fetch_preserves_input_error_classification(tmp_path):
    from eo_overpass_predictor.io.plans import fetch_acquisition_plans

    with pytest.raises(ValueError, match="timeout"):
        fetch_acquisition_plans(tmp_path, "2026-10-26", "2026-10-27", satellites="S1", timeout=0)


FIXTURE = (
    Path(__file__).parent / "fixtures" / "s1" / "s1d_mp_user_20261026t181009_20261102t201900.kml"
)


def _pass(when="2026-10-26T18:20:00Z"):
    return gpd.GeoDataFrame(
        {
            "mission": ["Sentinel-1"],
            "satellite": ["SENTINEL-1D"],
            "overpass_utc": [pd.Timestamp(when)],
            "roi_entry_utc": [pd.Timestamp(when) - pd.Timedelta(minutes=1)],
            "roi_exit_utc": [pd.Timestamp(when) + pd.Timedelta(minutes=1)],
        },
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:4326",
    )


def test_real_s1_user_plan_shape_and_metadata():
    plans = load_s1_acquisition_plans(FIXTURE)
    row = plans.iloc[0]
    assert len(plans) > 0
    assert row["satellite"] == "SENTINEL-1D"
    assert row["datatake_id"]
    assert row["mode"] == "IW"
    assert row["polarisation"] == "DV"
    assert row["plan_start_utc"] == pd.Timestamp("2026-10-26T18:10:09Z")
    assert pd.notna(row["obs_start_utc"])
    assert row.geometry is not None and not row.geometry.is_empty


def test_s1_latest_revision_selected_before_mode_matching():
    when = pd.Timestamp("2026-10-26T18:20:00Z")
    old = {
        "satellite": "SENTINEL-1D",
        "plan_start_utc": pd.Timestamp("2026-10-26T00:00Z"),
        "plan_end_utc": pd.Timestamp("2026-10-27T00:00Z"),
        "obs_start_utc": when - pd.Timedelta(minutes=1),
        "obs_stop_utc": when + pd.Timedelta(minutes=1),
        "plan_category": "S1D",
        "datatake_id": "old",
        "orbit_relative": 1,
        "mode": "EW",
        "polarisation": "HH",
        "plan_file": "old.kml",
        "geometry": box(0, 0, 1, 1),
    }
    new = {
        **old,
        "plan_start_utc": pd.Timestamp("2026-10-26T12:00Z"),
        "datatake_id": "new",
        "mode": "EW",
        "polarisation": "DV",
        "plan_file": "new.kml",
    }
    plans = gpd.GeoDataFrame([old, new], geometry="geometry", crs="EPSG:4326")
    passes = _pass()
    passes["mode"] = "IW"
    checked = crosscheck_acquisition_plan(
        passes, plans, gpd.GeoDataFrame(geometry=[Point(0.5, 0.5)], crs="EPSG:4326")
    )
    assert checked.iloc[0]["esa_plan_status"] == "not planned"


def test_s1_matching_mode_is_planned():
    plans = load_s1_acquisition_plans(FIXTURE).iloc[[0]].copy()
    passes = _pass(plans.iloc[0]["obs_start_utc"].isoformat())
    passes["mode"] = plans.iloc[0]["mode"]
    roi = gpd.GeoDataFrame(geometry=[plans.geometry.iloc[0].centroid], crs="EPSG:4326")
    passes["geometry"] = roi.geometry.iloc[0]
    checked = crosscheck_acquisition_plan(passes, plans, roi)
    assert checked.iloc[0]["esa_plan_status"] == "planned"


def test_empty_newer_revision_supersedes_older_populated_revision():
    when = pd.Timestamp("2026-10-26T18:20:00Z")
    row = {
        "satellite": "SENTINEL-1D",
        "plan_start_utc": when - pd.Timedelta(hours=2),
        "plan_end_utc": when + pd.Timedelta(hours=2),
        "obs_start_utc": when - pd.Timedelta(minutes=1),
        "obs_stop_utc": when + pd.Timedelta(minutes=1),
        "plan_category": "S1D",
        "datatake_id": "old",
        "orbit_relative": 1,
        "mode": "IW",
        "polarisation": "DV",
        "plan_file": "old.kml",
        "geometry": box(0, 0, 1, 1),
    }
    plans = gpd.GeoDataFrame([row], geometry="geometry", crs="EPSG:4326")
    plans.attrs["plan_windows"] = [
        {
            "satellite": "SENTINEL-1D",
            "plan_file": "new.kml",
            "plan_start_utc": when - pd.Timedelta(hours=1),
            "plan_end_utc": when + pd.Timedelta(hours=1),
        }
    ]
    checked = crosscheck_acquisition_plan(
        _pass(), plans, gpd.GeoDataFrame(geometry=[Point(0.5, 0.5)], crs="EPSG:4326")
    )
    assert checked.iloc[0]["esa_plan_status"] == "not planned"


def test_same_start_revision_tie_uses_filename_before_matching():
    when = pd.Timestamp("2026-10-26T18:20:00Z")
    base = {
        "satellite": "SENTINEL-1D",
        "plan_start_utc": when - pd.Timedelta(hours=1),
        "plan_end_utc": when + pd.Timedelta(hours=1),
        "obs_start_utc": when - pd.Timedelta(minutes=1),
        "obs_stop_utc": when + pd.Timedelta(minutes=1),
        "plan_category": "S1D",
        "orbit_relative": 1,
        "polarisation": "DV",
        "geometry": box(0, 0, 1, 1),
    }
    old = {**base, "plan_file": "a.kml", "datatake_id": "old", "mode": "IW"}
    new = {**base, "plan_file": "z.kml", "datatake_id": "new", "mode": "EW"}
    plans = gpd.GeoDataFrame([old, new], geometry="geometry", crs="EPSG:4326")
    passes = _pass()
    passes["mode"] = "IW"
    checked = crosscheck_acquisition_plan(
        passes, plans, gpd.GeoDataFrame(geometry=[Point(0.5, 0.5)], crs="EPSG:4326")
    )
    assert checked.iloc[0]["esa_plan_status"] == "not planned"


def test_s1_plan_longitudes_match_canonical_roi_after_wrap():
    when = pd.Timestamp("2026-10-26T18:20:00Z")
    plans = gpd.GeoDataFrame(
        [
            {
                "satellite": "SENTINEL-1D",
                "plan_start_utc": when - pd.Timedelta(hours=1),
                "plan_end_utc": when + pd.Timedelta(hours=1),
                "obs_start_utc": when - pd.Timedelta(minutes=1),
                "obs_stop_utc": when + pd.Timedelta(minutes=1),
                "plan_category": "S1D",
                "datatake_id": "wrapped",
                "orbit_relative": 1,
                "mode": "IW",
                "polarisation": "DV",
                "plan_file": "wrapped.kml",
                "geometry": box(-186, 0, -184, 1),
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    roi = gpd.GeoDataFrame(geometry=[box(174, 0, 176, 1)], crs="EPSG:4326")
    passes = _pass()
    passes["mode"] = "IW"
    passes["geometry"] = roi.geometry.iloc[0]
    checked = crosscheck_acquisition_plan(passes, plans, roi)
    assert checked.iloc[0]["esa_plan_status"] == "planned"


def test_s1_no_matching_mode_is_not_planned():
    when = pd.Timestamp("2026-10-26T18:20:00Z")
    plans = gpd.GeoDataFrame(
        [
            {
                "satellite": "SENTINEL-1D",
                "plan_start_utc": when - pd.Timedelta(hours=1),
                "plan_end_utc": when + pd.Timedelta(hours=1),
                "obs_start_utc": when + pd.Timedelta(hours=3),
                "obs_stop_utc": when + pd.Timedelta(hours=4),
                "plan_category": "S1D",
                "datatake_id": "later",
                "orbit_relative": 1,
                "mode": "IW",
                "polarisation": "DV",
                "plan_file": "plan.kml",
                "geometry": box(0, 0, 1, 1),
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    checked = crosscheck_acquisition_plan(
        _pass(), plans, gpd.GeoDataFrame(geometry=[Point(0.5, 0.5)], crs="EPSG:4326")
    )
    assert checked.iloc[0]["esa_plan_status"] == "not planned"


def test_s1_plan_files_are_managed_by_cache(tmp_path):
    path = tmp_path / "plans" / "s1d_mp_user_20261026t181009_20261102t201900.kml"
    path.parent.mkdir()
    path.write_bytes(b"kml")
    assert path in cache.cache_files(tmp_path)
    assert cache.clear_cache(tmp_path) == (1, 3)
