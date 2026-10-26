"""Fixed orbital inputs; tests must not depend on external service availability."""

import json
from pathlib import Path

import pytest
import requests

from eo_overpass_predictor import fetch_tles, load_roi, predict_overpasses

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Unexpected network request; use a fixture or explicit mock.")

    monkeypatch.setattr(requests.sessions.Session, "request", reject)


@pytest.fixture(scope="session")
def baseline():
    return json.loads((FIXTURES / "baseline.json").read_text())


@pytest.fixture(scope="session")
def tles(tmp_path_factory):
    return fetch_tles(
        satellites=["LS", "S2"],
        tle_text=(FIXTURES / "satellites.tle").read_text(),
        tle_dir=tmp_path_factory.mktemp("tle"),
        offline=True,
        verbose=False,
    )


@pytest.fixture(scope="session")
def results(baseline, tles):
    frames = {}
    for key, case in baseline["cases"].items():
        config = dict(case["config"])
        roi = load_roi(config.pop("roi"), name="Regression ROI")
        frames[key] = predict_overpasses(
            roi,
            tles,
            start_utc="2026-10-26",
            timezone_name="Australia/Melbourne",
            verbose=False,
            **config,
        )
    return frames
