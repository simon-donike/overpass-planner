"""Run the actual example notebooks with deterministic data and no HTTP access."""

import json
from pathlib import Path

import nbformat
import pytest
from conftest import FIXTURES
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.notebooks
@pytest.mark.parametrize(
    ("name", "working_directory"),
    [
        ("p00_overpass_env_setup", "notebooks"),
        ("p01_overpass_prediction", "."),
        ("p02_overpass_live_tracker", "notebooks"),
    ],
)
def test_example_notebook(name, working_directory, tmp_path, monkeypatch):
    pytest.importorskip("ipyleaflet")
    pytest.importorskip("folium")
    pytest.importorskip("matplotlib")
    workspace = tmp_path / "workspace"
    for key in ["IPYTHONDIR", "JUPYTER_RUNTIME_DIR", "MPLCONFIGDIR"]:
        directory = tmp_path / key.lower()
        directory.mkdir()
        monkeypatch.setenv(key, str(directory))
    monkeypatch.setenv("EO_OVERPASS_WORKSPACE", str(workspace))
    notebook = nbformat.read(ROOT / "notebooks" / f"{name}.ipynb", as_version=4)
    setup = f"""
import requests
import eo_overpass_predictor as eo
from pathlib import Path
_fixture_dir = Path({json.dumps(str(FIXTURES.resolve()))})
_fetch = eo.fetch_tles
_predict = eo.predict_overpasses
_load_plans = eo.load_acquisition_plans

def _no_http(*args, **kwargs):
    raise AssertionError("Notebook attempted a network request")
requests.sessions.Session.request = _no_http

def _fixed_tles(*args, **kwargs):
    text = (_fixture_dir / "satellites.tle").read_text() + "\\n" + (_fixture_dir / "sentinel1.tle").read_text()
    kwargs.update(tle_text=text, offline=True)
    return _fetch(*args, **kwargs)

def _fixed_predictions(*args, **kwargs):
    kwargs["start_utc"] = "2026-10-26"
    return _predict(*args, **kwargs)

eo.fetch_tles = _fixed_tles
eo.predict_overpasses = _fixed_predictions
_plan_files = list(_fixture_dir.rglob("*.kml"))
eo.fetch_acquisition_plans = lambda *args, **kwargs: _plan_files
eo.fetch_s1_acquisition_plans = lambda *args, **kwargs: _plan_files
eo.fetch_s2_acquisition_plans = lambda *args, **kwargs: _plan_files
eo.load_acquisition_plans = lambda *args, **kwargs: _load_plans(_plan_files)
eo.load_s2_acquisition_plans = lambda *args, **kwargs: _load_plans(_plan_files)
"""
    notebook.cells.insert(0, nbformat.v4.new_code_cell(setup))
    client = NotebookClient(
        notebook,
        timeout=180,
        kernel_name="python3",
        resources={"metadata": {"path": str(ROOT / working_directory)}},
    )
    executed = client.execute()
    assert all(
        output.output_type != "error"
        for cell in executed.cells
        if cell.cell_type == "code"
        for output in cell.get("outputs", [])
    )
    if name.startswith(("p01", "p02")):
        assert (workspace / "OUTPUT" / "live_tracker.html").is_file()
