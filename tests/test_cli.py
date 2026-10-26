import json

import pandas as pd
import pytest
import requests
from conftest import FIXTURES
from typer.testing import CliRunner

from eo_overpass_predictor import api
from eo_overpass_predictor.cli import app

runner = CliRunner()


@pytest.fixture
def cli_args(tmp_path):
    roi = tmp_path / "area.geojson"
    roi.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [144.35, -38.35],
                        [145.15, -38.35],
                        [145.15, -37.8],
                        [144.35, -37.8],
                        [144.35, -38.35],
                    ]
                ],
            }
        )
    )
    return [
        "--roi",
        str(roi),
        "--start",
        "2026-10-26",
        "--end",
        "2026-10-27",
        "--satellite",
        "S2B",
        "--tle-file",
        str(FIXTURES / "satellites.tle"),
        "--offline",
        "--cache-dir",
        str(tmp_path / "cache"),
    ]


@pytest.mark.parametrize(
    "args", [["--help"], ["predict", "--help"], ["tracker", "--help"], ["satellites", "--help"]]
)
def test_cli_help(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_list_satellites():
    result = runner.invoke(app, ["satellites", "--mission", "Landsat"])
    assert result.exit_code == 0
    assert "LANDSAT 8" in result.output and "SENTINEL" not in result.output
    assert runner.invoke(app, ["satellites", "--mission", "unknown"]).exit_code == 2


@pytest.mark.parametrize("suffix", [".csv", ".geojson", ".gpkg"])
def test_prediction_exports(cli_args, tmp_path, suffix):
    output = tmp_path / f"report{suffix}"
    result = runner.invoke(app, ["predict", *cli_args, "--output", str(output)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert output.exists()
    if suffix == ".csv":
        frame = pd.read_csv(output)
        assert len(frame) == 1
        assert frame["satellite"].tolist() == ["SENTINEL-2B"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--end", "2026-10-25"],
        ["--start", "NaT"],
        ["--satellite", "S1A"],
        ["--timezone", "Wrong/Zone"],
        ["--min-coverage", "101"],
        ["--format", "gpkg"],
        ["--fetch-plans"],
    ],
)
def test_argument_validation(cli_args, tmp_path, extra):
    result = runner.invoke(
        app, ["predict", *cli_args, "--output", str(tmp_path / "out.csv"), *extra]
    )
    assert result.exit_code == 2, (result.output, result.exception)
    assert not (tmp_path / "out.csv").exists()


def test_invalid_s1_mode_is_structured(cli_args, tmp_path):
    result = runner.invoke(
        app,
        ["predict", *cli_args, "--output", str(tmp_path / "out.csv"), "--s1-mode", "SM", "--json"],
    )
    assert result.exit_code == 2
    document = json.loads(result.stdout)
    assert document["schema_version"] == "2.0"
    assert document["error"]["code"] == "invalid_input"


def test_empty_result_is_success(cli_args, tmp_path):
    output = tmp_path / "empty.csv"
    result = runner.invoke(
        app, ["predict", *cli_args, "--end", "2026-10-26T00:01Z", "--output", str(output)]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert pd.read_csv(output).empty


def test_operational_failure_is_exit_one(cli_args, tmp_path, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("No TLE available: mock outage")

    monkeypatch.setattr(api, "fetch_tles", fail)
    result = runner.invoke(app, ["predict", *cli_args, "--output", str(tmp_path / "out.csv")])
    assert result.exit_code == 1
    assert "No TLE available" in result.output


def test_local_plans_and_unavailable_download(cli_args, tmp_path, monkeypatch):
    output = tmp_path / "checked.csv"
    args = ["predict", *cli_args, "--output", str(output)]
    result = runner.invoke(app, [*args, "--plans", str(next(FIXTURES.glob("*.kml")))])
    assert result.exit_code == 0, (result.output, result.exception)
    assert pd.read_csv(output)["esa_plan_status"].iloc[0] == "planned (NOMINAL)"

    def fail(**kwargs):
        raise requests.ConnectionError("mock ESA outage")

    monkeypatch.setattr(api, "fetch_s2_acquisition_plans", fail)
    args.remove("--offline")
    result = runner.invoke(app, [*args, "--fetch-plans"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "Warning:" in result.output
    assert pd.read_csv(output)["esa_plan_status"].iloc[0] == "no plan loaded"


def test_standalone_tracker(cli_args, tmp_path):
    output = tmp_path / "tracker.html"
    result = runner.invoke(app, ["tracker", *cli_args, "--output", str(output)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "const P =" in output.read_text()
