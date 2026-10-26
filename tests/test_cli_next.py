"""User and machine workflows for the lightweight upcoming-pass command."""

import json

import pandas as pd
import pytest
from conftest import FIXTURES
from typer.testing import CliRunner

from eo_overpass_predictor import api
from eo_overpass_predictor.cli import app

runner = CliRunner()
SCHEMA_PATH = FIXTURES.parent.parent / "docs" / "cli-output.schema.json"


@pytest.fixture
def next_args():
    return [
        "--start",
        "2026-10-26",
        "--days",
        "1",
        "--satellite",
        "S2B",
        "--tle-file",
        str(FIXTURES / "satellites.tle"),
        "--offline",
        "--no-cache",
    ]


def response(result):
    document = json.loads(result.stdout)
    assert document["schema_version"] == "1.0"
    # A single strict JSON value, never Python's NaN/Infinity extensions.
    json.dumps(document, allow_nan=False)
    # jsonschema is supplied by the development notebook tooling (nbformat).
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(
        document
    )
    return document


def test_point_without_output(next_args, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["next", "--point", "144.96", "-37.82", *next_args])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "1 overpass(es)" in result.stdout
    assert "Australia/Melbourne" in result.stdout
    assert "Acquisition unknown" in result.stdout
    assert "Geometry: intersects" in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["point", "bbox", "file"])
def test_roi_inputs_json_with_provenance(next_args, tmp_path, kind):
    options = {
        "point": ["--point", "144.96", "-37.82"],
        "bbox": ["--bbox", "144.35", "-38.35", "145.15", "-37.8"],
    }
    if kind == "file":
        roi = tmp_path / "area.geojson"
        roi.write_text('{"type":"Point","coordinates":[144.96,-37.82]}')
        options[kind] = ["--roi", str(roi)]
    result = runner.invoke(app, ["next", *options[kind], *next_args, "--json"])
    assert result.exit_code == 0, (result.output, result.exception)
    doc = response(result)
    assert doc["ok"] and doc["status"] == "ok" and doc["count"] == 1
    assert doc["output"] is None
    assert doc["request"]["start_utc"] == "2026-10-26T00:00:00Z"
    assert doc["request"]["end_utc"] == "2026-10-27T00:00:00Z"
    provenance = doc["provenance"]["orbital_elements"][0]
    assert provenance["source"] == "manual" and len(provenance["sha256"]) == 64
    row = doc["passes"][0]
    assert row["overpass_utc"].endswith("Z")
    assert row["overpass_local"].endswith("+11:00")
    assert row["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert row["status"]["acquisition"] == "unknown"
    assert row["status"]["imagery"] == "not_checked"
    assert row["status"]["orbital_data"] == "fresh"
    assert doc["assumptions"]


@pytest.mark.parametrize("suffix", [".csv", ".geojson", ".gpkg", ".html"])
def test_optional_export_has_clean_json(next_args, tmp_path, suffix):
    path = tmp_path / f"output{suffix}"
    result = runner.invoke(
        app,
        [
            "next",
            "--point",
            "144.96",
            "-37.82",
            *next_args,
            "--output",
            str(path),
            "--json",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert response(result)["output"] == str(path.resolve())
    assert path.exists()
    assert "written" in result.stderr


def test_defaults_resolve_now_and_fourteen_days(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eo_overpass_predictor.cli._run_prediction", lambda *args: calls.append(args)
    )
    before = pd.Timestamp.now(tz="UTC").floor("min")
    result = runner.invoke(app, ["next", "--point", "-10", "-20"])
    after = pd.Timestamp.now(tz="UTC")
    assert result.exit_code == 0, (result.output, result.exception)
    command, point, start, end, output, *_ = calls[0]
    assert command == "next" and point == (-10, -20) and output is None
    assert before <= pd.Timestamp(start) <= after
    assert pd.Timestamp(end) - pd.Timestamp(start) == pd.Timedelta(days=14)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--point", "1"],
        ["--point", "181", "0"],
        ["--point", "nan", "0"],
        ["--point", "1", "2", "--bbox", "0", "0", "1", "1"],
        ["--bbox", "145", "-38", "144", "-37"],
        ["--point", "1", "2", "--days", "0"],
        ["--point", "1", "2", "--days", "inf"],
        ["--point", "1", "2", "--days", "nan"],
        ["--point", "1", "2", "--start", "NaT"],
        ["--point", "1", "2", "--min-coverage", "101"],
        ["--point", "1", "2", "--roi", "missing.geojson"],
        ["--point", "1", "2", "--fetch-plans", "--offline"],
        ["--unknown"],
    ],
)
def test_validation_errors_are_structured(args):
    result = runner.invoke(app, ["next", *args, "--json"])
    assert result.exit_code == 2, (result.output, result.exception)
    doc = response(result)
    assert not doc["ok"] and doc["command"] == "next"
    assert doc["error"]["code"] == "invalid_input"
    assert doc["error"]["message"]
    assert "Invalid input:" in result.stderr


def test_empty_json_is_success(next_args):
    result = runner.invoke(
        app,
        [
            "next",
            "--point",
            "144.96",
            "-37.82",
            *next_args,
            "--days",
            "0.0001",
            "--json",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    doc = response(result)
    assert doc["ok"] and doc["status"] == "empty" and doc["passes"] == []
    assert doc["count"] == 0 and doc["request"]["timezone"] == "Australia/Melbourne"


def test_operational_error_is_structured(next_args, monkeypatch):
    def fail(**kwargs):
        print("Diagnostic from a dependency")
        raise RuntimeError("Elements unavailable")

    monkeypatch.setattr(api, "fetch_tles", fail)
    result = runner.invoke(app, ["next", "--point", "144.96", "-37.82", *next_args, "--json"])
    assert result.exit_code == 1
    assert response(result)["error"]["code"] == "operational_error"
    assert "Diagnostic from a dependency" in result.stderr


def test_local_plans_are_reflected_in_json(next_args):
    result = runner.invoke(
        app,
        [
            "next",
            "--point",
            "144.96",
            "-37.82",
            *next_args,
            "--plans",
            str(next(FIXTURES.glob("*.kml"))),
            "--json",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    row = response(result)["passes"][0]
    assert row["esa_plan_status"] == "planned (NOMINAL)"
    assert row["status"]["acquisition"] == "planned_nominal"
    assert isinstance(row["esa_relative_orbit"], int)
    assert "cross-check" in result.stderr


@pytest.mark.parametrize("args", [["satellites"], ["cache", "info"], ["cache", "clear"]])
def test_other_commands_support_json(args, tmp_path):
    options = [] if args == ["satellites"] else ["--cache-dir", str(tmp_path / "cache")]
    result = runner.invoke(app, [*args, *options, "--json"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert response(result)["ok"]


@pytest.mark.parametrize("command", ["predict", "tracker"])
def test_existing_prediction_commands_json(command, tmp_path):
    roi = tmp_path / "area.geojson"
    roi.write_text('{"type":"Point","coordinates":[144.96,-37.82]}')
    output = tmp_path / ("report.csv" if command == "predict" else "planner.html")
    result = runner.invoke(
        app,
        [
            command,
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
            "--no-cache",
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert response(result)["command"] == command
    assert output.exists()


@pytest.mark.parametrize("args", [["predict"], ["tracker"], ["cache", "info", "--bad"]])
def test_other_commands_parse_errors_json(args):
    result = runner.invoke(app, [*args, "--json"])
    assert result.exit_code == 2, (result.output, result.exception)
    document = response(result)
    assert document["error"]["code"] == "invalid_input"
    assert document["command"] == " ".join(args[:2] if args[0] == "cache" else args[:1])
