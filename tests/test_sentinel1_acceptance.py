"""Scientific behavior checks using historical elements, never live acquisitions."""

import hashlib
from pathlib import Path

import pandas as pd
import pytest
from conftest import FIXTURES

from eo_overpass_predictor import fetch_tles, load_roi, predict_overpasses, satellite_positions


@pytest.fixture(scope="module")
def radar_tles(tmp_path_factory):
    return fetch_tles(
        "S1",
        tle_text=(FIXTURES / "sentinel1.tle").read_text(),
        tle_dir=tmp_path_factory.mktemp("s1-acceptance-tle"),
        offline=True,
        verbose=False,
    )


def test_default_radar_eligibility_includes_night_and_both_directions(radar_tles):
    passes = predict_overpasses(
        load_roi((144.96, -37.82)),
        radar_tles,
        start_utc="2026-10-26",
        days=12,
        timezone_name="UTC",
        min_sun_elevation_deg=90,
        verbose=False,
    )
    assert not passes.empty
    assert set(passes["direction"]) == {"Ascending", "Descending"}
    assert (passes["sun_elevation_deg"] < 0).any()
    assert passes["imaging_expected"].all()
    assert passes["mode"].eq("IW").all()
    assert passes.attrs["s1_mode"] == "IW"


def test_nadir_is_outside_the_radar_envelope(radar_tles):
    instant = pd.Timestamp("2026-10-26T07:27:00Z")
    position = satellite_positions(radar_tles, instant, "S1C").iloc[0]
    passes = predict_overpasses(
        load_roi((position["lon"], position["lat"])),
        radar_tles,
        satellites="S1C",
        start_utc=instant - pd.Timedelta(seconds=60),
        days=120 / 86400,
        timezone_name="UTC",
        verbose=False,
    )
    assert passes.empty


def test_radar_prediction_is_pure_and_can_partially_cover_a_polygon(
    radar_tles, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    passes = predict_overpasses(
        load_roi((142.0, -39.0, 149.0, -36.0)),
        radar_tles,
        satellites="S1D",
        start_utc="2026-10-30T09:00:00Z",
        days=0.02,
        timezone_name="UTC",
        verbose=False,
    )
    assert len(passes) == 1
    assert 0 < passes.iloc[0]["roi_coverage_pct"] < 100
    assert passes.geometry.is_valid.all()
    assert list(Path.cwd().iterdir()) == []


def test_sentinel1_tle_provenance_matches_the_frozen_input():
    digest = hashlib.sha256((FIXTURES / "sentinel1.tle").read_bytes()).hexdigest()
    provenance = (FIXTURES / "sentinel1.tle.provenance").read_text()
    assert digest in provenance
    for identifier in ("62261", "66315"):
        assert f"https://celestrak.org/NORAD/elements/gp.php?CATNR={identifier}" in provenance
