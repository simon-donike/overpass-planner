import os
import time
from types import SimpleNamespace

import pytest
import requests
from conftest import FIXTURES

from eo_overpass_predictor import fetch_tles, normalise_tle_line, parse_tle_text
from eo_overpass_predictor.io import tles as tle_io

TEXT = (FIXTURES / "satellites.tle").read_text()


def test_parse_and_normalize_genuine_elements():
    parsed = parse_tle_text(TEXT)
    assert set(parsed) == {39084, 49260, 40697, 42063, 60989}
    for record in parsed.values():
        for key in ["line1", "line2"]:
            line = record[key]
            assert normalise_tle_line(" ".join(line.split())) == line
            damaged = line[:-1] + str((int(line[-1]) + 1) % 10)
            with pytest.raises(ValueError):
                normalise_tle_line(damaged)


@pytest.mark.parametrize("text", ["", "not a TLE", "1 42063U broken"])
def test_invalid_text(text):
    with pytest.raises(ValueError):
        parse_tle_text(text)


def test_mismatched_line_identifiers():
    parsed = parse_tle_text(TEXT)
    with pytest.raises(ValueError, match="identifiers"):
        parse_tle_text(parsed[39084]["line1"] + "\n" + parsed[49260]["line2"])


def test_manual_priority_and_fresh_cache(tmp_path):
    first = fetch_tles(["S2B"], tle_dir=tmp_path, tle_text=TEXT, offline=True, verbose=False)
    assert first["SENTINEL-2B"]["source"] == "manual"
    second = fetch_tles(["S2B"], tle_dir=tmp_path, verbose=False)
    assert second["SENTINEL-2B"]["source"].startswith("cache")
    assert first["SENTINEL-2B"]["line1"] == second["SENTINEL-2B"]["line1"]


def test_stale_cache_offline_and_network_fallback(tmp_path, monkeypatch):
    fetch_tles(["S2B"], tle_dir=tmp_path, tle_text=TEXT, offline=True, verbose=False)
    stale = time.time() - 13 * 3600
    os.utime(tmp_path / "42063.tle", (stale, stale))
    with pytest.warns(UserWarning, match="stale TLE"):
        cached = fetch_tles(["S2B"], tle_dir=tmp_path, offline=True, verbose=False)

    def fail(*args, **kwargs):
        raise requests.ConnectionError("mock outage")

    monkeypatch.setattr(requests, "get", fail)
    with pytest.warns(UserWarning, match="stale TLE"):
        fallback = fetch_tles(["S2B"], tle_dir=tmp_path, verbose=False)
    assert fallback["SENTINEL-2B"]["line1"] == cached["SENTINEL-2B"]["line1"]
    assert "stale cache" in fallback["SENTINEL-2B"]["source"]


def test_offline_missing_elements_and_partial_supplied_input(tmp_path):
    with pytest.raises(RuntimeError, match="offline"):
        fetch_tles(["S2B"], tle_dir=tmp_path, offline=True, verbose=False)
    with pytest.raises(RuntimeError, match="SENTINEL-2B"):
        fetch_tles(
            ["S2B"],
            tle_dir=tmp_path,
            tle_text="\n".join(TEXT.splitlines()[:3]),
            offline=True,
            verbose=False,
        )


def test_provider_fallback(monkeypatch):
    record = parse_tle_text(TEXT)[42063]
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if "celestrak" in url:
            raise requests.HTTPError("mock provider unavailable")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: record)

    monkeypatch.setattr(requests, "get", get)
    parsed, source = tle_io._download_tle(42063, 1)
    assert parsed["line1"] == record["line1"]
    assert source == "TLE API mirror"
    assert len(calls) == 2


def test_corrupt_cache_can_be_refreshed(tmp_path, monkeypatch):
    (tmp_path / "42063.tle").write_text("broken cache")
    record = parse_tle_text(TEXT)[42063]
    monkeypatch.setattr(tle_io, "_download_tle", lambda *args: (record, "mock provider"))
    with pytest.warns(UserWarning, match="invalid TLE cache"):
        assert (
            fetch_tles(["S2B"], tle_dir=tmp_path, verbose=False)["SENTINEL-2B"]["source"]
            == "mock provider"
        )


def test_default_cache_is_platform_specific(tmp_path, monkeypatch):
    monkeypatch.setattr("eo_overpass_predictor.io.cache.user_cache_path", lambda app: tmp_path)
    fetch_tles(["S2B"], tle_text=TEXT, offline=True, verbose=False)
    assert (tmp_path / "tle" / "42063.tle").is_file()
