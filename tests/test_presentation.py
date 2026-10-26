"""Pass labels keep unknown acquisitions separate from negative plan evidence."""

import json

import pandas as pd
import pytest

from eo_overpass_predictor import next_overpasses, summarise_overpasses
from eo_overpass_predictor.presentation import describe_pass, json_value


@pytest.mark.parametrize(
    ("mission", "plan", "expected"),
    [
        ("Sentinel-2", None, "unknown"),
        ("Sentinel-2", pd.NA, "unknown"),
        ("Sentinel-2", "no plan loaded", "unknown"),
        ("Sentinel-2", "not planned", "not_planned"),
        ("Sentinel-2", "planned (NOMINAL)", "planned_nominal"),
        ("Sentinel-2", "planned (VICARIOUS)", "planned_other"),
        ("Landsat", None, "not_checked"),
        ("Landsat", "n/a", "not_checked"),
    ],
)
def test_acquisition_evidence(mission, plan, expected):
    row = {
        "mission": mission,
        "esa_plan_status": plan,
        "imaging_expected": True,
        "tle_age_days": 1,
        "edge_margin_km": 40,
    }
    original = row.copy()
    status = describe_pass(row)
    assert status["acquisition"] == expected
    assert status["geometry"] == "intersects" and status["imaging"] == "expected"
    assert status["imagery"] == "not_checked"
    assert row == original
    codes = {warning["code"] for warning in status["warnings"]}
    assert ("acquisition_unknown" in codes) == (expected == "unknown")
    assert ("non_nominal_plan" in codes) == (expected == "planned_other")


@pytest.mark.parametrize(
    ("age", "status"),
    [(0, "fresh"), (7, "fresh"), (7.1, "aged"), (14, "aged"), (14.1, "stale"), (-15, "stale")],
)
def test_age_relative_to_pass_and_edge_warnings(age, status):
    result = describe_pass(
        {"mission": "Landsat", "imaging_expected": False, "tle_age_days": age, "edge_margin_km": -4}
    )
    assert result["orbital_data"] == status
    assert result["imaging"] == "not_expected"
    codes = {warning["code"] for warning in result["warnings"]}
    assert "swath_edge" in codes
    assert ("tle_age" in codes) == (abs(age) > 7)


def test_json_nulls_and_timestamps():
    document = json_value(
        {
            "values": [float("nan"), float("inf"), pd.NA, pd.NaT],
            "utc": pd.Timestamp("2026-10-26T01:02:03.1Z"),
            "local": pd.Timestamp("2026-10-26T01:02:03+02:00"),
        }
    )
    assert document["values"] == [None] * 4
    assert document["utc"] == "2026-10-26T01:02:03.100000Z"
    assert document["local"].endswith("+02:00")
    json.dumps(document, allow_nan=False)


def test_notebook_summary_shows_unknown_and_unchecked_acquisitions(results):
    summary = summarise_overpasses(results["polygon"])
    sentinel = summary["Satellite"].str.startswith("SENTINEL")
    assert summary.loc[sentinel, "Acquisition"].str.contains("unknown").all()
    assert summary.loc[~sentinel, "Acquisition"].str.contains("not checked").all()
    assert summary["TLE data"].str.contains("d from pass").all()


def test_active_countdown_does_not_claim_acquisition(results):
    frame = results["polygon"].iloc[:1].copy()
    frame["imaging_expected"] = False
    countdown = next_overpasses(frame, now_utc=frame["roi_entry_utc"].iloc[0])
    assert countdown["Countdown"].tolist() == ["pass active"]
