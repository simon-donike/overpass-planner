"""Integration checks for browser orchestration, without external services."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import requests

from eo_overpass_predictor import fetch_tles, load_roi, predict_overpasses, satellite_positions
from eo_overpass_predictor.presentation import prediction_document

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Website unit tests must not request external services")

    monkeypatch.setattr(requests.sessions.Session, "request", reject)


def test_satellite_progress_preserves_full_engine_result(tmp_path):
    adapter = module("website_adapter", SITE / "python/browser_adapter.py")
    text = (ROOT / "tests/fixtures/satellites.tle").read_text()
    names = ["SENTINEL-2B", "SENTINEL-2C", "LANDSAT 9"]
    roi = [144.35, -38.35, 145.15, -37.8]
    request = {
        "roi": roi,
        "roi_name": "Historical test",
        "tle_text": text,
        "orbital_source": "Historical frozen test fixture",
        "orbital_sources": {"SENTINEL-2B": "Historical fixture; stale browser cache"},
        "orbital_warnings": [
            {"code": "orbital_cache_fallback", "message": "Historical test warning"}
        ],
        "satellites": names,
        "start_utc": "2026-10-26T00:00:00Z",
        "days": 3,
        "timezone": "Australia/Melbourne",
        "imaging_only": False,
        "min_coverage": 0,
        "min_sun_elevation": 0,
        "s1_mode": "IW",
    }
    events = []
    actual = json.loads(
        adapter.run_request_json(json.dumps(request), lambda *event: events.append(event))
    )
    area = load_roi(roi, name="Historical test")
    tles = fetch_tles(names, tle_text=text, tle_dir=tmp_path, offline=True, verbose=False)
    for record in tles.values():
        record["source"] = request["orbital_source"]
    frame = predict_overpasses(
        area,
        tles,
        start_utc=request["start_utc"],
        days=3,
        satellites=names,
        timezone_name="Australia/Melbourne",
        imaging_only=False,
        verbose=False,
    )
    expected = prediction_document("next", area, tles, frame, {}, [])
    assert actual["document"]["passes"] == expected["passes"]
    assert actual["document"]["count"] == len(frame)
    assert (
        actual["document"]["provenance"]["orbital_elements"][0]["source"]
        == "Historical fixture; stale browser cache"
    )
    assert actual["document"]["warnings"] == request["orbital_warnings"]
    assert [event[2] for event in events if event[0] == "prediction"] == [0, 1, 1, 2, 2, 3]


def test_bundled_wheel_preserves_package_files():
    import zipfile

    wheels = list((SITE / "public/wheels").glob("eo_overpass_predictor-*.whl"))
    assert len(wheels) == 1, "Prepare browser assets before running website tests"
    with zipfile.ZipFile(wheels[0]) as wheel:
        for source in (ROOT / "src/eo_overpass_predictor").rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts:
                name = source.relative_to(ROOT / "src").as_posix()
                assert wheel.read(name) == source.read_bytes(), name
        assert any(name.endswith("/licenses/LICENSE") for name in wheel.namelist())


def test_live_positions_use_public_propagation_and_preserve_element_epochs(tmp_path):
    import pandas as pd

    adapter = module("website_adapter", SITE / "python/browser_adapter.py")
    text = "\n".join(
        (ROOT / "tests/fixtures" / name).read_text() for name in ["satellites.tle", "sentinel1.tle"]
    )
    names = list(json.loads(adapter.catalogue_json()))
    tles = fetch_tles(names, tle_text=text, tle_dir=tmp_path, offline=True, verbose=False)
    request = {
        "satellites": names,
        "tle_text": text,
        "orbital_source": "Frozen historical test elements",
    }
    for when in ["2026-10-29T12:34:56Z", "2026-11-30T12:34:56Z", "2026-09-01T12:34:56Z"]:
        actual = json.loads(adapter.positions_json(json.dumps({**request, "when_utc": when})))
        expected = satellite_positions(tles, when_utc=when).to_dict(orient="records")
        for row, reference in zip(actual["positions"], expected, strict=True):
            assert {key: row[key] for key in reference} == reference
            epoch = tles[row["satellite"]]["epoch_utc"]
            assert pd.Timestamp(row["epoch_utc"]) == pd.Timestamp(epoch)
            age = (pd.Timestamp(when) - pd.Timestamp(epoch)).total_seconds() / 86400
            assert row["tle_age_days"] == age
            assert row["freshness"] == (
                "fresh" if abs(age) <= 7 else "aged" if abs(age) <= 14 else "stale"
            )
    with pytest.raises(ValueError, match="UTC offset"):
        adapter.positions_json(json.dumps({**request, "when_utc": "2026-10-29T12:34:56"}))
    with pytest.raises(ValueError, match="without duplicates"):
        adapter.positions_json(
            json.dumps({**request, "satellites": [], "when_utc": "2026-10-29T12:34:56Z"})
        )


def test_simplified_timezone_data_is_small_attributed_and_usable(tmp_path):
    import zipfile

    from timezonefinder import TimezoneFinder

    build = json.loads((SITE / "public/build.json").read_text())
    assert all("parts" not in wheel for wheel in build["wheels"])
    entry = next(wheel for wheel in build["wheels"] if "timezonefinder_data-" in wheel["url"])
    path = SITE / "public" / entry["url"]
    assert path.stat().st_size < 10 * 1024 * 1024
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    with zipfile.ZipFile(path) as wheel:
        report = json.loads(wheel.read("timezonefinder_data/OVERPASS_DATA_PROVENANCE.json"))
        assert report["vertices_after"] < report["vertices_before"] * 0.1
        assert report["tolerance_degrees"] == 0.01
        assert any(name.endswith("/licenses/DATA_LICENSE") for name in wheel.namelist())
        assert "+overpass.1" in path.name
        wheel.extractall(tmp_path / "compact")
    original = next((SITE / ".cache").glob("timezonefinder_data-*.whl"))
    assert hashlib.sha256(original.read_bytes()).hexdigest() == report["source_sha256"]
    with zipfile.ZipFile(original) as wheel:
        wheel.extractall(tmp_path / "original")
    with (
        TimezoneFinder(bin_file_location=tmp_path / "compact/timezonefinder_data/data") as compact,
        TimezoneFinder(bin_file_location=tmp_path / "original/timezonefinder_data/data") as full,
    ):
        assert compact.timezone_names == full.timezone_names
        # Cities, islands, an enclave and both sides of international borders.
        locations = [
            (-3.7038, 40.4168),
            (13.405, 52.52),
            (144.96, -37.82),
            (151.2093, -33.8688),
            (-74.006, 40.7128),
            (-123.12, 49.28),
            (139.6917, 35.6895),
            (85.324, 27.7172),
            (77.209, 28.6139),
            (-155.5828, 19.8968),
            (178.065, -17.7134),
            (-157.4, 1.87),
            (8.69, 47.697),
            (4.93, 51.44),
            (-106.485, 31.7619),
            (-106.4245, 31.6904),
            (-5.348, 36.1408),
            (-5.4537, 36.1312),
            (0, 0),
            (179, 0),
            (-179, 0),
            (0, -85),
        ]
        for lng, lat in locations:
            assert compact.timezone_at(lng=lng, lat=lat) == full.timezone_at(lng=lng, lat=lat)
