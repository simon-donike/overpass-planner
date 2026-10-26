"""Optional real-browser interaction checks; see docs/cli.md for setup."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skyfield.api import wgs84
from skyfield.framelib import itrs

from eo_overpass_predictor import export_live_tracker_html, fetch_tles, load_roi, predict_overpasses
from eo_overpass_predictor.core.orbits import _build_satellite, _ts
from eo_overpass_predictor.core.sensors import sensor_config, sentinel1_footprint_edges


@pytest.mark.skipif(os.environ.get("EO_BROWSER_TESTS") != "1", reason="Opt-in Playwright checks")
def test_html_planner_in_browser(results, tles, tmp_path):
    if not shutil.which("node"):
        pytest.fail("EO_BROWSER_TESTS requires Node.js")
    satellite_js = os.environ.get("EO_SATELLITE_JS")
    if not satellite_js or not Path(satellite_js).is_file():
        pytest.fail("Set EO_SATELLITE_JS to a local copy of the exported tracker's satellite.js")
    # Synthetic labels on real frozen prediction geometry exercise every UI status.
    frame = results["polygon"].copy()
    frame["esa_plan_status"] = "no plan loaded"
    sentinels = frame.index[frame["mission"] == "Sentinel-2"]
    for index, status in zip(
        sentinels, ["planned (NOMINAL)", "not planned", "planned (VICARIOUS)"]
    ):
        frame.loc[index, "esa_plan_status"] = status
    frame.loc[frame.index[0], "imaging_expected"] = False
    frame.loc[frame.index[0], "tle_age_days"] = 10
    frame.loc[frame.index[1], "tle_age_days"] = -20
    area = load_roi((144.35, -38.35, 145.15, -37.80))
    export_live_tracker_html(tles, area, tmp_path / "planner.html", passes=frame)
    export_live_tracker_html(tles, area, tmp_path / "empty.html", passes=results["empty"])
    export_live_tracker_html(tles, area, tmp_path / "positions.html")
    subprocess.run(
        ["node", str(Path(__file__).with_name("planner_browser.cjs")), str(tmp_path), satellite_js],
        check=True,
        timeout=90,
    )


@pytest.mark.skipif(os.environ.get("EO_BROWSER_TESTS") != "1", reason="Opt-in Playwright checks")
def test_sentinel1_tracker_geometry_in_browser(tmp_path):
    satellite_js = os.environ.get("EO_SATELLITE_JS")
    chromium = os.environ.get("EO_CHROMIUM_PATH")
    if not shutil.which("node") or not satellite_js or not chromium:
        pytest.fail("Sentinel-1 browser test requires Node, EO_SATELLITE_JS and EO_CHROMIUM_PATH")
    tle_text = (Path(__file__).parent / "fixtures" / "sentinel1.tle").read_text()
    tles = fetch_tles(
        satellites=["S1"], tle_text=tle_text, tle_dir=tmp_path / "tle", offline=True, verbose=False
    )
    area = load_roi((144.35, -38.35, 145.15, -37.80))
    frame = predict_overpasses(
        area,
        tles,
        satellites=["S1"],
        s1_mode="IW",
        start_utc="2026-10-29",
        days=3,
        coarse_step_s=60,
        fine_step_s=5,
        daytime_descending_only=False,
        verbose=False,
    )
    frame["esa_plan_status"] = "planned"
    frame["esa_mode"] = "IW"
    frame["esa_polarisation"] = "VV/VH"
    frame["esa_relative_orbit"] = 37
    frame["esa_plan_file"] = "s1c_mp_user_fixture.kml"
    export_live_tracker_html(tles, area, tmp_path / "sar.html", passes=frame, s1_mode="IW")
    sat = _build_satellite("SENTINEL-1C", tles["SENTINEL-1C"])
    when = _ts().utc(2026, 10, 26, 0, 0, 0)
    state = sat.at(when)
    p = state.frame_xyz(itrs).km
    v = state.frame_xyz_and_velocity(itrs)[1].km_per_s
    altitude = float(wgs84.height_of(state).km)
    references = []
    for mode in ("IW", "EW"):
        references.append(
            {
                "state": {
                    "position": dict(zip("xyz", p.tolist(), strict=True)),
                    "velocity": dict(zip("xyz", v.tolist(), strict=True)),
                    "altitude": altitude,
                },
                "config": sensor_config("S1", mode),
                "edges": sentinel1_footprint_edges(p, v, altitude, mode)[:2],
            }
        )
    (tmp_path / "sar_expected.json").write_text(json.dumps(references))
    subprocess.run(
        ["node", str(Path(__file__).with_name("sar_browser.cjs")), str(tmp_path), satellite_js],
        check=True,
        timeout=90,
    )
