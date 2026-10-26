"""Satellite groups and multi-value options across the public API and CLI."""

import json
import re

import pandas as pd
import pytest
from conftest import FIXTURES
from typer.testing import CliRunner

from eo_overpass_predictor import api
from eo_overpass_predictor.cli import app
from eo_overpass_predictor.core.orbits import _resolve_satellites

SENTINELS = ["SENTINEL-2A", "SENTINEL-2B", "SENTINEL-2C"]
LANDSATS = ["LANDSAT 8", "LANDSAT 9"]
S1 = ["SENTINEL-1C", "SENTINEL-1D"]


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("S2", SENTINELS),
        ("s2", SENTINELS),
        ("Sentinel-2", SENTINELS),
        ("LS", LANDSATS),
        ("ls", LANDSATS),
        ("Landsat", LANDSATS),
        ("S1", S1),
        ("Sentinel-1", S1),
        (["S2", "LS"], SENTINELS + LANDSATS),
        ("S2B,L9", ["SENTINEL-2B", "LANDSAT 9"]),
        ("42063, 49260", ["SENTINEL-2B", "LANDSAT 9"]),
        (
            ["S2B", "S2", "LS", "L8", 42063],
            ["SENTINEL-2B", "SENTINEL-2A", "SENTINEL-2C", *LANDSATS],
        ),
        (["LANDSAT 8", "L8", 39084], ["LANDSAT 8"]),
    ],
)
def test_group_expansion_and_ordered_deduplication(selection, expected):
    assert _resolve_satellites(selection) == expected


@pytest.mark.parametrize("selection", ["", "S2,", ",LS", "S2,,LS", ["S2", "not-a-satellite"]])
def test_invalid_group_or_empty_member(selection):
    with pytest.raises(ValueError):
        _resolve_satellites(selection)


def test_api_lists_sentinel1_aliases():
    table = api.list_satellites("Sentinel-1")
    assert table["satellite"].tolist() == S1
    assert table["alias"].tolist() == ["S1C", "S1D"]


@pytest.mark.parametrize(("group", "expected"), [("S2", SENTINELS), ("LS", LANDSATS)])
def test_api_offline_fetch_groups(group, expected, tmp_path):
    loaded = api.fetch_tles(
        group,
        tle_text=(FIXTURES / "satellites.tle").read_text(),
        tle_dir=tmp_path,
        offline=True,
        verbose=False,
    )
    assert list(loaded) == expected
    positions = api.satellite_positions(loaded, when_utc="2026-10-26", satellites=group)
    assert positions["satellite"].tolist() == expected


@pytest.mark.parametrize("command", ["predict", "tracker"])
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (["--satellite", "S2"], SENTINELS),
        (["--satellite", "LS"], LANDSATS),
        (["--satellite", "S2A", "S2B", "S2C"], SENTINELS),
        (["--satellite", "S2", "LS", "S2B"], SENTINELS + LANDSATS),
        (["--satellite", "S2B,L9"], ["SENTINEL-2B", "LANDSAT 9"]),
        (["--satellite", "S2B", "--satellite", "L9"], ["SENTINEL-2B", "LANDSAT 9"]),
        (["-s", "S2A", "S2B"], SENTINELS[:2]),
        (["--satellite=S2A", "S2B"], SENTINELS[:2]),
        (["-sS2A", "S2B"], SENTINELS[:2]),
        (["--satellite", "LANDSAT 8", "42063"], ["LANDSAT 8", "SENTINEL-2B"]),
    ],
)
def test_cli_group_and_list_selection(command, options, expected, tmp_path, monkeypatch):
    area = tmp_path / "area.geojson"
    area.write_text('{"type":"Point","coordinates":[144.96,-37.82]}')
    output = tmp_path / ("passes.csv" if command == "predict" else "tracker.html")
    selections = []
    original = api.predict_overpasses

    def record_selection(roi, tles, **kwargs):
        selections.append(list(tles))
        return original(roi, tles, **kwargs)

    monkeypatch.setattr(api, "predict_overpasses", record_selection)
    result = CliRunner().invoke(
        app,
        [
            command,
            "--roi",
            str(area),
            "--start",
            "2026-10-26",
            *options,
            "--end",
            "2026-10-27",
            "--output",
            str(output),
            "--tle-file",
            str(FIXTURES / "satellites.tle"),
            "--offline",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert selections == [expected]
    if command == "tracker":
        payload = json.loads(re.search(r"const P = (.*);", output.read_text()).group(1))
        assert [sat["name"] for sat in payload["sats"]] == expected
    else:
        frame = pd.read_csv(output)
        assert set(frame["satellite"]) <= set(expected)


@pytest.mark.parametrize(
    "options",
    [
        ["--satellite", "S2", "typo"],
        ["--satellite", ""],
        ["--satellite"],
        ["--satellite", "S2", "--unknown"],
        ["--satellite", "S2", "--", "LS"],
    ],
)
def test_multi_value_validation_preserves_option_boundaries(options, tmp_path, monkeypatch):
    area = tmp_path / "area.geojson"
    area.write_text('{"type":"Point","coordinates":[144.96,-37.82]}')

    def no_prediction(*args, **kwargs):
        pytest.fail("Invalid selection must fail before prediction.")

    monkeypatch.setattr(api, "predict_overpasses", no_prediction)
    result = CliRunner().invoke(
        app,
        [
            "predict",
            "--roi",
            str(area),
            "--start",
            "2026-10-26",
            "--end",
            "2026-10-27",
            "--output",
            str(tmp_path / "out.csv"),
            "--offline",
            "--tle-file",
            str(FIXTURES / "satellites.tle"),
            "--cache-dir",
            str(tmp_path / "cache"),
            *options,
        ],
    )
    assert result.exit_code == 2, (result.output, result.exception)
    assert not (tmp_path / "out.csv").exists()
