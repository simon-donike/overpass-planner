"""Presentation metadata shared by terminal reports, JSON and the HTML planner.

These labels describe existing model outputs; they do not change the orbital
calculation or turn element age into a statistical accuracy estimate.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from importlib.metadata import version
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

from .types import TLERecord

SCHEMA_VERSION = "2.0"
ASSUMPTIONS = [
    "Optical sensors use nominal nadir-centred swaths; Sentinel-1 uses nominal side-looking radar geometry. Geometric coverage is not an acquisition guarantee.",
    "Imaging eligibility follows the sensor model: optical sensors use direction, solar elevation and mission limits; Sentinel-1 operates day and night.",
    "ESA plans are checked only when supplied or explicitly requested; a missing plan means unknown acquisition. Landsat acquisition is not checked.",
    "TLE age labels use absolute distance from the pass epoch: fresh <=7, aged <=14, stale >14 days. "
    "These are refresh reminders, not calibrated confidence estimates.",
    "Optical swath-edge margins within approximately +/-5 km are uncertain; Sentinel-1 geometry is nominal and mode/look configuration dependent.",
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
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def describe_pass(row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    """Describe geometry, imaging eligibility, plan evidence and element freshness."""
    warnings: list[dict[str, str]] = []
    raw_plan = row.get("esa_plan_status")
    plan = str(raw_plan) if raw_plan is not None and pd.notna(raw_plan) else None
    mission = str(row.get("mission", ""))
    if mission == "Sentinel-1":
        if plan == "planned" or (plan and plan.startswith("planned (")):
            acquisition, acquisition_label = "planned", "Planned"
        elif plan == "not planned":
            acquisition, acquisition_label = "not_planned", "No match in loaded ESA plan"
            warnings.append(
                {
                    "code": "plan_no_match",
                    "message": "No matching Sentinel-1 acquisition in the loaded plan.",
                }
            )
        else:
            acquisition, acquisition_label = "unknown", "Acquisition unknown (no plan loaded)"
            warnings.append(
                {
                    "code": "acquisition_unknown",
                    "message": "No loaded Sentinel-1 plan covers this pass; acquisition is unknown.",
                }
            )
    elif mission == "Sentinel-2":
        acquisition, acquisition_label = "unknown", "Acquisition unknown (no plan loaded)"
        if plan == "planned (NOMINAL)":
            acquisition, acquisition_label = "planned_nominal", "Planned (nominal)"
        elif plan and plan.startswith("planned ("):
            acquisition, acquisition_label = "planned_other", plan.capitalize()
            warnings.append(
                {
                    "code": "non_nominal_plan",
                    "message": "Non-nominal plans may not produce distributed imagery.",
                }
            )
        elif plan == "not planned":
            acquisition, acquisition_label = "not_planned", "No match in loaded ESA plan"
            warnings.append(
                {
                    "code": "plan_no_match",
                    "message": "No matching datatake in the loaded plan; simplified plan footprints can miss edge overlaps.",
                }
            )
        else:
            warnings.append(
                {
                    "code": "acquisition_unknown",
                    "message": "No loaded ESA plan covers this pass; acquisition is unknown.",
                }
            )
    else:
        acquisition, acquisition_label = "not_checked", "Acquisition not checked (Landsat)"

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
    if mission != "Sentinel-1" and abs(float(row["edge_margin_km"])) <= 5:
        warnings.append(
            {
                "code": "swath_edge",
                "message": "The ROI anchor is near the nominal swath edge (within +/-5 km).",
            }
        )
    expected = bool(row["imaging_expected"])
    if mission == "Sentinel-1":
        warnings.append(
            {
                "code": "sar_nominal_geometry",
                "message": "Sentinel-1 footprint and incidence are nominal mode geometry; acquisition planning and imagery remain separate facts.",
            }
        )
    imaging_label = (
        "Radar imaging eligible"
        if mission == "Sentinel-1" and expected
        else (
            "Radar imaging not expected"
            if mission == "Sentinel-1"
            else ("Imaging expected" if expected else "Imaging not expected")
        )
    )
    return {
        "geometry": "intersects",
        "imaging": "expected" if expected else "not_expected",
        "imaging_label": imaging_label,
        "acquisition": acquisition,
        "acquisition_label": acquisition_label,
        "orbital_data": freshness,
        "tle_age_days": age,
        "imagery": "not_checked",
        "warnings": warnings,
    }


def prediction_document(
    command: str,
    area: gpd.GeoDataFrame,
    tles: dict[str, TLERecord],
    passes: gpd.GeoDataFrame,
    parameters: dict[str, Any],
    diagnostics: list[dict[str, str]],
    output: str | None = None,
) -> dict[str, Any]:
    """Build the versioned CLI response, including resolved inputs and provenance."""
    records = []
    for _, row in passes.iterrows():
        record = row.drop(labels="geometry").to_dict()
        record["geometry"] = mapping(row.geometry)
        record["status"] = describe_pass(row)
        records.append(record)
    return json_value(
        {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "command": command,
            "status": "ok" if records else "empty",
            "generated_at": pd.Timestamp.now(tz="UTC"),
            "request": {
                "roi_name": passes.attrs["roi_name"],
                "roi": mapping(area.geometry.iloc[0]),
                "crs": "EPSG:4326",
                "start_utc": passes.attrs["window_start_utc"],
                "end_utc": passes.attrs["window_end_utc"],
                "timezone": passes.attrs["timezone"],
                "satellites": list(tles),
                **parameters,
            },
            "provenance": {
                "package_version": version("eo-overpass-predictor"),
                "orbital_elements": [
                    {
                        "satellite": name,
                        "norad_id": record["norad_id"],
                        "epoch_utc": record["epoch_utc"],
                        "source": record["source"],
                        "sha256": hashlib.sha256(
                            (record["line1"] + "\n" + record["line2"]).encode()
                        ).hexdigest(),
                    }
                    for name, record in tles.items()
                ],
            },
            "assumptions": ASSUMPTIONS,
            "warnings": diagnostics,
            "count": len(records),
            "passes": records,
            "output": output,
        }
    )
