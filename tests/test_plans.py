import zipfile
from types import SimpleNamespace

import pytest
import requests
from conftest import FIXTURES
from shapely.geometry import Point

from eo_overpass_predictor import (
    crosscheck_s2_acquisition_plan,
    fetch_s2_acquisition_plans,
    load_roi,
    load_s2_acquisition_plans,
)
from eo_overpass_predictor.io import plans as plan_io

PLAN = next(FIXTURES.glob("*.kml"))


def test_kml_kmz_parsing(tmp_path):
    first = load_s2_acquisition_plans(PLAN)
    compressed = tmp_path / "test.kmz"
    with zipfile.ZipFile(compressed, "w") as archive:
        archive.writestr("doc.kml", PLAN.read_bytes())
    second = load_s2_acquisition_plans(compressed)
    assert len(first) == len(second) == 1
    assert first["satellite"].iloc[0] == "SENTINEL-2B"
    assert first["plan_category"].iloc[0] == "NOMINAL"
    assert first["datatake_id"].iloc[0] == "synthetic-datatake"
    assert first.geometry.iloc[0].equals(second.geometry.iloc[0])
    assert str(first["obs_start_utc"].dt.tz) == "UTC"


def test_all_acquisition_statuses(results):
    area = load_roi((144.35, -38.35, 145.15, -37.80))
    plans = load_s2_acquisition_plans(PLAN)
    original = results["polygon"]
    checked = crosscheck_s2_acquisition_plan(original, plans, area)
    statuses = set(checked["esa_plan_status"])
    assert statuses == {"planned (NOMINAL)", "not planned", "no plan loaded", "n/a"}
    assert checked.attrs == original.attrs
    assert "esa_plan_status" not in original
    planned = checked.loc[checked["esa_plan_status"] == "planned (NOMINAL)"].iloc[0]
    assert planned["esa_datatake_id"] == "synthetic-datatake"
    assert planned["esa_relative_orbit"] == 1
    plans["plan_category"] = "CALIBRATION"
    checked = crosscheck_s2_acquisition_plan(original, plans, area)
    assert "planned (CALIBRATION)" in checked["esa_plan_status"].values


def test_empty_plans_and_empty_predictions(results):
    area = load_roi((144.96, -37.82))
    plans = load_s2_acquisition_plans([])
    checked = crosscheck_s2_acquisition_plan(results["polygon"], plans, area)
    assert set(checked["esa_plan_status"]) == {"no plan loaded", "n/a"}
    assert crosscheck_s2_acquisition_plan(results["empty"], plans, area).empty
    with pytest.raises(ValueError):
        crosscheck_s2_acquisition_plan(results["polygon"], plans, area, time_tolerance_s=-1)


def test_antimeridian_plan_matching(tmp_path, results):
    path = tmp_path / PLAN.name
    path.write_text(
        PLAN.read_text().replace(
            "143,-39 146,-39 146,-37 143,-37 143,-39", "179,-1 -179,-1 -179,1 179,1 179,-1"
        )
    )
    plans = load_s2_acquisition_plans(path)
    assert plans.geometry.iloc[0].bounds == (179, -1, 181, 1)
    checked = crosscheck_s2_acquisition_plan(
        results["polygon"].iloc[:1], plans, load_roi(Point(-179.5, 0))
    )
    assert checked["esa_plan_status"].iloc[0] == "planned (NOMINAL)"


def test_no_default_plan_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "eo_overpass_predictor.io.cache.user_cache_path", lambda app: tmp_path / "absent"
    )
    assert load_s2_acquisition_plans().empty


def test_plan_download_cache_and_window_filtering(tmp_path, monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            text=f'<a href="/documents/d/sentinel/{PLAN.stem}">plan</a>',
            content=PLAN.read_bytes(),
        )

    monkeypatch.setattr(requests, "get", get)
    paths = fetch_s2_acquisition_plans(
        tmp_path,
        start_utc="2026-10-26",
        end_utc="2026-11-01",
        satellites=["SENTINEL-2B"],
        verbose=False,
    )
    assert len(paths) == 1 and paths[0].read_bytes() == PLAN.read_bytes()
    assert len(calls) == 2
    fetch_s2_acquisition_plans(
        tmp_path, start_utc="2026-10-26", end_utc="2026-11-01", satellites=["S2B"], verbose=False
    )
    assert len(calls) == 3
    assert (
        fetch_s2_acquisition_plans(
            tmp_path,
            start_utc="2030-01-01",
            end_utc="2030-01-02",
            satellites=["S2B"],
            verbose=False,
        )
        == []
    )


def test_failed_plan_links_are_optional(tmp_path, monkeypatch):
    def get(url, **kwargs):
        if url == plan_io.ESA_S2_PLAN_PAGE:
            return SimpleNamespace(
                raise_for_status=lambda: None,
                text=f'<a href="/documents/d/sentinel/{PLAN.stem}">plan</a>',
            )
        raise requests.HTTPError("mock broken link")

    monkeypatch.setattr(requests, "get", get)
    assert (
        fetch_s2_acquisition_plans(
            tmp_path,
            start_utc="2026-10-26",
            end_utc="2026-11-01",
            satellites=["S2B"],
            verbose=False,
        )
        == []
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_utc": "2026-10-26", "end_utc": "2026-10-25"},
        {"satellites": ["L8"]},
    ],
)
def test_plan_download_validation(tmp_path, kwargs):
    with pytest.raises(ValueError):
        fetch_s2_acquisition_plans(tmp_path, **kwargs)
