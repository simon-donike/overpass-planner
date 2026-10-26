"""CLI coverage for Sentinel-1 mode and mission-aware plan fetching."""

import json

import geopandas as gpd
import pandas as pd
from conftest import FIXTURES
from shapely.geometry import Point
from typer.testing import CliRunner

from eo_overpass_predictor import api
from eo_overpass_predictor.cli import _check_plans, app

runner = CliRunner()


def test_sentinel1_cli_iw_and_ew_json_contract():
    args = [
        "next",
        "--point",
        "144.96",
        "-37.82",
        "--start",
        "2026-10-26",
        "--days",
        "16",
        "--satellite",
        "S1",
        "--tle-file",
        str(FIXTURES / "sentinel1.tle"),
        "--offline",
        "--no-cache",
        "--all-passes",
        "--json",
    ]
    iw = runner.invoke(app, [*args, "--s1-mode", "IW"])
    ew = runner.invoke(app, [*args, "--s1-mode", "EW"])
    assert iw.exit_code == ew.exit_code == 0, (iw.output, ew.output)
    iw_doc, ew_doc = json.loads(iw.stdout), json.loads(ew.stdout)
    assert iw_doc["schema_version"] == ew_doc["schema_version"] == "2.0"
    assert iw_doc["request"]["s1_mode"] == "IW"
    assert ew_doc["request"]["s1_mode"] == "EW"
    assert iw_doc["count"] > 0 and ew_doc["count"] > 0
    assert {row["s1_mode"] for row in iw_doc["passes"]} == {"IW"}
    assert {row["s1_mode"] for row in ew_doc["passes"]} == {"EW"}
    row = iw_doc["passes"][0]
    assert row["look_side"] == "right"
    assert isinstance(row["incidence_angle_deg"], (int, float))


def test_fetch_plans_mission_failure_keeps_other_mission(monkeypatch):
    passes = gpd.GeoDataFrame({"mission": [], "satellite": []}, geometry=[], crs="EPSG:4326")
    passes.attrs.update(
        window_start_utc=pd.Timestamp("2026-10-26", tz="UTC"),
        window_end_utc=pd.Timestamp("2026-11-11", tz="UTC"),
    )
    area = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:4326")
    tles = {"SENTINEL-1C": {}, "SENTINEL-2B": {}}
    calls = []

    def s1_fail(**kwargs):
        raise RuntimeError("S1 outage")

    def s2_ok(**kwargs):
        calls.append("s2")
        return ["s2.kml"]

    monkeypatch.setattr(api, "fetch_s1_acquisition_plans", s1_fail)
    monkeypatch.setattr(api, "fetch_s2_acquisition_plans", s2_ok)
    monkeypatch.setattr(api, "load_acquisition_plans", lambda source: source)
    monkeypatch.setattr(api, "crosscheck_acquisition_plan", lambda p, loaded, a: loaded)
    diagnostics = []
    result = _check_plans(passes, area, tles, None, True, None, diagnostics)
    assert result == ["s2.kml"]
    assert calls == ["s2"]
    assert diagnostics[0]["code"] == "plans_unavailable"
    assert "S1 outage" in diagnostics[0]["message"]


def test_failed_mission_uses_default_cache(monkeypatch, tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    cached = plans / "s1c_mp_user_20261026t000000_20261110t000000.kml"
    cached.write_text("cached")
    monkeypatch.setattr("eo_overpass_predictor.cli.cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        api,
        "fetch_s1_acquisition_plans",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("S1 outage")),
    )
    monkeypatch.setattr(api, "fetch_s2_acquisition_plans", lambda **kwargs: ["s2.kml"])
    loaded = []
    monkeypatch.setattr(
        api, "load_acquisition_plans", lambda source: loaded.extend(source) or source
    )
    monkeypatch.setattr(api, "crosscheck_acquisition_plan", lambda p, loaded, a: loaded)
    passes = gpd.GeoDataFrame({"mission": [], "satellite": []}, geometry=[], crs="EPSG:4326")
    passes.attrs.update(
        window_start_utc=pd.Timestamp("2026-10-26", tz="UTC"),
        window_end_utc=pd.Timestamp("2026-11-11", tz="UTC"),
    )
    tles = {"SENTINEL-1C": {}, "SENTINEL-2B": {}}
    result = _check_plans(
        passes,
        gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:4326"),
        tles,
        None,
        True,
        None,
        [],
    )
    assert "s2.kml" in result and cached in result
