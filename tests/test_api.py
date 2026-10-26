import pandas as pd
import pytest

from eo_overpass_predictor import (
    list_satellites,
    load_roi,
    next_overpasses,
    predict_overpasses,
    satellite_positions,
    summarise_overpasses,
)
from eo_overpass_predictor.core.orbits import _resolve_satellites


@pytest.mark.parametrize("selection", [["L8"], ["landsat_8"], [39084], "L8", 39084])
def test_satellite_aliases(selection):
    assert _resolve_satellites(selection) == ["LANDSAT 8"]


@pytest.mark.parametrize("selection", [["S1A"], [99999], []])
def test_invalid_satellites(selection):
    with pytest.raises(ValueError):
        _resolve_satellites(selection)


def test_catalogue_mission_selection():
    assert len(list_satellites()) == 7
    assert list_satellites("landsat")["alias"].tolist() == ["L8", "L9"]
    assert len(list_satellites("sentinel-2")) == 3
    assert list_satellites("Sentinel-1")["alias"].tolist() == ["S1C", "S1D"]
    with pytest.raises(ValueError):
        list_satellites("unknown")


@pytest.mark.parametrize(
    "config",
    [
        {"days": 0},
        {"days": -1},
        {"days": float("nan")},
        {"days": 1e-8},
        {"coarse_step_s": 0},
        {"fine_step_s": -1},
        {"fine_step_s": 31},
        {"min_roi_coverage_pct": 101},
        {"min_roi_coverage_pct": float("nan")},
        {"min_sun_elevation_deg": -91},
        {"start_utc": "NaT"},
        {"start_utc": "bad-date"},
        {"satellites": []},
        {"satellites": ["unknown"]},
    ],
)
def test_prediction_rejects_invalid_parameters(tles, config):
    with pytest.raises(ValueError):
        predict_overpasses(load_roi((144.96, -37.82)), tles, verbose=False, **config)


def test_missing_elements(tles):
    with pytest.raises(KeyError, match="No TLE loaded"):
        predict_overpasses(
            load_roi((144.96, -37.82)),
            {"LANDSAT 8": tles["LANDSAT 8"]},
            satellites=["S2B"],
            verbose=False,
        )


def test_prediction_is_independent_of_file_io(tles, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = predict_overpasses(
        load_roi((144.96, -37.82)),
        tles,
        start_utc="2026-10-26",
        days=0.001,
        timezone_name="UTC",
        verbose=False,
    )
    assert result.empty
    assert list(tmp_path.iterdir()) == []


def test_result_structure_and_reports(results):
    frame = results["polygon"]
    assert frame["overpass_utc"].is_monotonic_increasing
    optical = list_satellites().query("mission != 'Sentinel-1'")
    assert set(frame["satellite"]) == set(optical["satellite"])
    assert frame["roi_coverage_pct"].between(0, 100).all()
    assert frame["imaging_expected"].all()
    assert len(summarise_overpasses(frame)) == len(frame)
    upcoming = next_overpasses(frame, now_utc="2026-10-26")
    assert upcoming["Satellite"].is_unique
    assert len(upcoming) == 5
    assert next_overpasses(frame, now_utc="2030-01-01").empty
    assert summarise_overpasses(results["empty"]).empty
    assert next_overpasses(results["empty"]).empty
    for column in ["overpass_utc", "roi_entry_utc", "roi_exit_local"]:
        assert isinstance(results["empty"][column].dtype, pd.DatetimeTZDtype)


def test_filtering_coverage_and_satellites(tles, results):
    result = predict_overpasses(
        load_roi((144.35, -38.35, 145.15, -37.80)),
        tles,
        start_utc="2026-10-26",
        days=16,
        satellites=["S2B"],
        min_roi_coverage_pct=99,
        timezone_name="Australia/Melbourne",
        verbose=False,
    )
    expected = results["polygon"].query("satellite == 'SENTINEL-2B' and roi_coverage_pct >= 99")
    assert result["overpass_utc"].tolist() == expected["overpass_utc"].tolist()


def test_positions_are_deterministic_and_timezone_normalized(tles):
    first = satellite_positions(tles, "2026-10-26T00:00Z", satellites=["S2B"])
    equivalent = satellite_positions(tles, "2026-10-26T11:00+11:00", satellites=["S2B"])
    pd.testing.assert_frame_equal(first, equivalent)
    assert set(first) == {"satellite", "lat", "lon", "alt_km", "ground_speed_kms", "direction"}
    assert -90 <= first.iloc[0]["lat"] <= 90
    assert 600 < first.iloc[0]["alt_km"] < 900


def test_local_times_across_dst(tles, results):
    frame = predict_overpasses(
        load_roi((144.35, -38.35, 145.15, -37.80)),
        tles,
        start_utc="2026-10-26",
        days=16,
        timezone_name="America/New_York",
        verbose=False,
    )
    assert set(frame["utc_offset"]) == {"-04:00", "-05:00"}
    assert frame["overpass_utc"].tolist() == results["polygon"]["overpass_utc"].tolist()
    for _, row in frame.iterrows():
        assert row["overpass_local"] == row["overpass_utc"].tz_convert("America/New_York")
