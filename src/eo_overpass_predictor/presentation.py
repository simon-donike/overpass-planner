"""Presentation metadata shared by terminal reports, JSON and the HTML planner.

These labels describe existing model outputs; they do not change the orbital
calculation or turn element age into a statistical accuracy estimate.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

SCHEMA_VERSION = "1.0"
ASSUMPTIONS = [
    "Nominal nadir-centred optical swaths; geometric coverage is not an acquisition guarantee.",
    "Expected imaging uses direction, solar elevation and mission latitude limits.",
    "ESA plans are checked only when supplied or explicitly requested; Landsat is not checked.",
    "TLE age labels use absolute distance from the pass epoch: fresh <=7, aged <=14, stale >14 days. These are refresh reminders, not calibrated confidence estimates.",
    "Swath-edge margins within approximately +/-5 km are uncertain.",
    "Imagery availability and cloud cover have not been checked.",
]


def json_value(value: Any) -> Any:
    """Convert scientific values to strict JSON with ISO dates and null missing values."""
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat().replace("+00:00", "Z")
    if hasattr(value, "item"):
        return json_value(value.item())
    if isinstance(value, float) and (not math.isfinite(value)):
        return None
    return value


def describe_pass(row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    """Describe geometry, imaging eligibility, plan evidence and element freshness."""
    warnings: list[dict[str, str]] = []
    raw_plan = row.get("esa_plan_status")
    plan = str(raw_plan) if raw_plan is not None and pd.notna(raw_plan) else None
    if row["mission"] != "Sentinel-2":
        acquisition, acquisition_label = ("not_checked", "Acquisition not checked (Landsat)")
    elif plan == "planned (NOMINAL)":
        acquisition, acquisition_label = ("planned_nominal", "Planned (nominal)")
    elif plan and plan.startswith("planned ("):
        acquisition, acquisition_label = ("planned_other", plan.capitalize())
        warnings.append(
            {
                "code": "non_nominal_plan",
                "message": "Non-nominal plans may not produce distributed imagery.",
            }
        )
    elif plan == "not planned":
        acquisition, acquisition_label = ("not_planned", "No match in loaded ESA plan")
        warnings.append(
            {
                "code": "plan_no_match",
                "message": "No matching datatake in the loaded plan; simplified plan footprints can miss edge overlaps.",
            }
        )
    else:
        acquisition, acquisition_label = ("unknown", "Acquisition unknown (no plan loaded)")
        warnings.append(
            {
                "code": "acquisition_unknown",
                "message": "No loaded ESA plan covers this pass; acquisition is unknown.",
            }
        )
    age = float(row["tle_age_days"])
    distance = abs(age)
    freshness = "fresh" if distance <= 7 else "aged" if distance <= 14 else "stale"
    if distance > 7:
        warnings.append(
            {
                "code": "tle_age",
                "message": f"Orbital elements are {distance:.1f} days from this pass; refresh before relying on its timing.",
            }
        )
    if abs(float(row["edge_margin_km"])) <= 5:
        warnings.append(
            {
                "code": "swath_edge",
                "message": "The ROI anchor is near the nominal swath edge (within +/-5 km).",
            }
        )
    expected = bool(row["imaging_expected"])
    return {
        "geometry": "intersects",
        "imaging": "expected" if expected else "not_expected",
        "imaging_label": "Imaging expected" if expected else "Imaging not expected",
        "acquisition": acquisition,
        "acquisition_label": acquisition_label,
        "orbital_data": freshness,
        "tle_age_days": age,
        "imagery": "not_checked",
        "warnings": warnings,
    }
