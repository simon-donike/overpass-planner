"""One SAR mode must be used by predictions and all their live representations."""

import json
import re

import pandas as pd
import pytest
from conftest import FIXTURES
from skyfield.api import wgs84
from skyfield.framelib import itrs

from eo_overpass_predictor import export_live_tracker_html, fetch_tles, load_roi, predict_overpasses
from eo_overpass_predictor.core.orbits import _build_satellite, _ts
from eo_overpass_predictor.core.sensors import resolve_s1_mode, sentinel1_footprint_edges


@pytest.fixture
def empty_ew(tmp_path):
    tles = fetch_tles(
        "S1C",
        tle_text=(FIXTURES / "sentinel1.tle").read_text(),
        tle_dir=tmp_path / "tle",
        offline=True,
        verbose=False,
    )
    roi = load_roi((0, 0))
    passes = predict_overpasses(
        roi,
        tles,
        start_utc="2026-10-26",
        days=0.001,
        s1_mode="EW",
        timezone_name="UTC",
        verbose=False,
    )
    assert passes.empty
    return tles, roi, passes


def test_empty_predictions_preserve_their_sar_mode(empty_ew, tmp_path):
    tles, roi, passes = empty_ew
    output = export_live_tracker_html(tles, roi, tmp_path / "ew.html", passes=passes)
    payload = json.loads(re.search(r"const P = (.*);", output.read_text()).group(1))
    assert payload["sats"][0]["sensor_config"]["mode"] == "EW"
    with pytest.raises(ValueError, match="conflicts"):
        export_live_tracker_html(tles, roi, tmp_path / "mismatch.html", passes=passes, s1_mode="IW")
    assert not (tmp_path / "mismatch.html").exists()


def test_mode_helper_rejects_conflicting_row_metadata(empty_ew):
    _, _, passes = empty_ew
    assert resolve_s1_mode(passes, " ew ") == "EW"
    conflicting = passes.reindex([0]).copy()
    conflicting.loc[0, "mission"] = "Sentinel-1"
    conflicting.loc[0, "mode"] = "IW"
    with pytest.raises(ValueError, match="conflicting"):
        resolve_s1_mode(conflicting)


def test_notebook_tracker_preserves_mode_and_rejects_mismatch(empty_ew):
    pytest.importorskip("ipyleaflet")
    from eo_overpass_predictor import LiveTracker

    tles, roi, passes = empty_ew
    with pytest.raises(ValueError, match="conflicts"):
        LiveTracker(tles, roi, passes=passes, s1_mode="IW")
    tracker = LiveTracker(tles, roi, passes=passes)
    assert tracker.s1_mode == "EW"
    tracker.stop()
    tracker.map.close()
    tracker.widget.close()


def test_notebook_sar_swath_retains_subsecond_state_time(empty_ew):
    from eo_overpass_predictor import LiveTracker

    tles, _, _ = empty_ew
    name = "SENTINEL-1C"
    tracker = LiveTracker.__new__(LiveTracker)
    tracker.sats = {name: _build_satellite(name, tles[name])}
    tracker.s1_mode = "EW"
    now = pd.Timestamp("2026-10-26T07:26:00.987654Z")
    ring = tracker._current_swath(name, now)
    first = _ts().from_datetime((now - pd.Timedelta(seconds=150)).to_pydatetime())
    state = tracker.sats[name].at(first)
    position, velocity = state.frame_xyz_and_velocity(itrs)
    near, *_ = sentinel1_footprint_edges(
        position.km, velocity.km_per_s, float(wgs84.height_of(state).km), "EW"
    )
    assert ring[0] == pytest.approx([near[1], near[0]], abs=1e-7)
