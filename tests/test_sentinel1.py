import pandas as pd
from conftest import FIXTURES
from skyfield.api import wgs84
from skyfield.framelib import itrs as itrs_frame

from eo_overpass_predictor import fetch_tles, load_roi, predict_overpasses
from eo_overpass_predictor.core.orbits import _build_satellite, _ts
from eo_overpass_predictor.core.sensors import sentinel1_footprint_edges


def _right_edge(tles, name, when, mode):
    when = pd.Timestamp(when).tz_convert("UTC")
    rec = tles[name]
    sat = _build_satellite(name, rec)
    ts = _ts().utc(when.year, when.month, when.day, when.hour, when.minute, when.second)
    state = sat.at(ts)
    p = state.frame_xyz(itrs_frame).km
    later = sat.at(
        _ts().utc(when.year, when.month, when.day, when.hour, when.minute, when.second + 1)
    )
    v = later.frame_xyz(itrs_frame).km - p
    altitude = float(wgs84.height_of(state).km)
    near, far, *_ = sentinel1_footprint_edges(p, v, altitude, mode)
    return ((near[0] + far[0]) / 2.0, (near[1] + far[1]) / 2.0)


def test_sentinel1_prediction_uses_right_looking_geometry(tmp_path):
    tles = fetch_tles(
        satellites="S1C",
        tle_text=(FIXTURES / "sentinel1.tle").read_text(),
        tle_dir=tmp_path,
        offline=True,
        verbose=False,
    )
    roi = load_roi((154.0, -5.0, 168.0, 6.0), name="S1 fixture area")
    result = predict_overpasses(
        roi,
        tles,
        start_utc="2026-10-26T07:26:00Z",
        days=0.03,
        satellites="S1C",
        s1_mode="IW",
        coarse_step_s=30,
        fine_step_s=5,
        verbose=False,
    )
    assert not result.empty
    row = result.iloc[0]
    assert row["mission"] == "Sentinel-1"
    assert row["mode"] == "IW"
    assert row["look_side"] == "right"
    assert row["near_look_angle_deg"] < row["far_look_angle_deg"]
    assert row["incidence_angle_deg"] > 0


def test_sentinel1_ew_is_available_and_imaging_filter_is_optional(tmp_path):
    tles = fetch_tles(
        satellites="S1D",
        tle_text=(FIXTURES / "sentinel1.tle").read_text(),
        tle_dir=tmp_path,
        offline=True,
        verbose=False,
    )
    roi = load_roi((4.0, -10.0, 15.0, 4.0), name="S1 fixture area")
    result = predict_overpasses(
        roi,
        tles,
        start_utc="2026-10-25T17:26:00Z",
        days=0.02,
        satellites="S1D",
        s1_mode="EW",
        imaging_only=False,
        coarse_step_s=30,
        fine_step_s=5,
        verbose=False,
    )
    assert not result.empty
    assert set(result["mode"]) == {"EW"}


def test_sentinel1_point_timestamps_stay_inside_fractional_window(tmp_path):
    tles = fetch_tles(
        satellites="S1D",
        tle_text=(FIXTURES / "sentinel1.tle").read_text(),
        tle_dir=tmp_path,
        offline=True,
        verbose=False,
    )
    start = pd.Timestamp("2026-10-25T17:26:00Z")
    end = start + pd.Timedelta(seconds=43.2)
    result = predict_overpasses(
        load_roi((4.0, -10.0, 15.0, 4.0), name="S1 fractional window"),
        tles,
        start_utc=start,
        days=43.2 / 86400.0,
        satellites="S1D",
        s1_mode="EW",
        imaging_only=False,
        coarse_step_s=30,
        fine_step_s=5,
        verbose=False,
    )
    assert not result.empty
    assert (result["roi_entry_utc"] <= result["overpass_utc"]).all()
    assert (result["overpass_utc"] <= result["roi_exit_utc"]).all()
    assert (result["overpass_utc"] >= start).all()
    assert (result["overpass_utc"] <= end).all()
