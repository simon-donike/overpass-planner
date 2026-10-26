import pandas as pd
import pytest
from conftest import FIXTURES
from typer.testing import CliRunner

from eo_overpass_predictor.cli import app

runner = CliRunner()


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


def test_point_without_output(next_args, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["next", "--point", "144.96", "-37.82", *next_args])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "1 overpass(es)" in result.stdout
    assert "Australia/Melbourne" in result.stdout
    assert not list(tmp_path.iterdir())


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
    assert command == "next" and point == (-10, -20) and (output is None)
    assert before <= pd.Timestamp(start) <= after
    assert pd.Timestamp(end) - pd.Timestamp(start) == pd.Timedelta(days=14)
