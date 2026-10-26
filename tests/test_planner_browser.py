"""Optional real-browser interaction checks; see docs/cli.md for setup."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eo_overpass_predictor import export_live_tracker_html, load_roi


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
