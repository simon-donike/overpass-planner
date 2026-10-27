"""Conservative one-to-one matching, with interval-censored timestamp errors."""

from __future__ import annotations

import pandas as pd

from .design import Study, horizon_bin, utc


def tle_age_band(age_days: float) -> str:
    for low, high in ((0, 1), (1, 3), (3, 7), (7, 14), (14, 30), (30, 60)):
        if low <= age_days < high:
            return f"{low}-{high}d"
    return "60d+" if age_days >= 60 else "invalid"


def discrepancy(prediction: dict, event: dict) -> dict:
    t = utc(prediction["overpass_utc"])
    low = (utc(event["start_utc"]) - t).total_seconds()
    high = (utc(event["end_utc"]) - t).total_seconds()
    return {
        "residual_lower_s": low,
        "residual_upper_s": high,
        "midpoint_residual_s": (low + high) / 2,
        "interval_distance_s": max(low, 0, -high),
        "absolute_error_upper_s": max(abs(low), abs(high)),
        "time_basis": event["time_basis"],
    }


def match_case(case: dict, predictions: list[dict], evidence: dict, study: Study) -> dict:
    events = evidence.get("events", []) if evidence["status"] == "complete" else []
    neighbors: list[list[int]] = [[] for _ in predictions]
    reverse: list[list[int]] = [[] for _ in events]
    for i, prediction in enumerate(predictions):
        for j, event in enumerate(events):
            direction = event.get("direction")
            if direction and direction != prediction["direction"].lower():
                continue
            if discrepancy(prediction, event)["interval_distance_s"] <= study.match_gate_s:
                neighbors[i].append(j)
                reverse[j].append(i)
    rows, used = [], set()
    for i, prediction in enumerate(predictions):
        lead = (utc(prediction["overpass_utc"]) - utc(case["origin"])).total_seconds() / 86400
        row = {
            **prediction,
            "case_id": case["case_id"],
            "origin": case["origin"],
            "site_id": case["site_id"],
            "spatial_block": case["spatial_block"],
            "surface": case["surface"],
            "latitude_band": case["latitude_band"],
            "sampling_weight": case["sampling_weight"],
            "satellite": case["satellite"],
            "lead_days": lead,
            "horizon_bin": horizon_bin(study, lead),
            "catalog_status": evidence["status"],
            "status": "unknown",
            "asset_status": "unknown",
            "event_id": None,
        }
        row["edge_band"] = "edge" if abs(row["edge_margin_km"]) <= 5 else "interior"
        age = row["initial_tle_age_days"]
        row["initial_tle_age_band"] = tle_age_band(age)
        row["element_cutoff_lag_days"] = case.get("element_cutoff_lag_days", 0.0)
        if "tle_epoch_utc" in row:
            row["tle_age_at_pass_days"] = (
                utc(row["overpass_utc"]) - utc(row["tle_epoch_utc"])
            ).total_seconds() / 86400
            row["tle_age_at_pass_band"] = tle_age_band(row["tle_age_at_pass_days"])
        if evidence["status"] == "complete":
            candidates = neighbors[i]
            if not candidates:
                row["status"] = "not_catalogued_within_gate"
            elif len(candidates) == 1 and len(reverse[candidates[0]]) == 1:
                j = candidates[0]
                event = events[j]
                used.add(j)
                row.update(discrepancy(prediction, event))
                row.update(
                    status="matched",
                    event_id=event["event_id"],
                    item_ids=[item["id"] for item in event["items"]],
                    asset_status=event.get("asset_check", {}).get("status", "unknown"),
                )
            else:
                row["status"] = "ambiguous"
        rows.append(row)
    end = utc(case["origin"]) + pd.Timedelta(days=study.horizon_days)
    observed = []
    for j, event in enumerate(events):
        midpoint = utc(event["start_utc"]) + (utc(event["end_utc"]) - utc(event["start_utc"])) / 2
        if not utc(case["origin"]) <= midpoint < end:
            continue
        observed.append(
            {
                "event_id": event["event_id"],
                "origin": case["origin"],
                "site_id": case["site_id"],
                "spatial_block": case["spatial_block"],
                "satellite": case["satellite"],
                "surface": case["surface"],
                "element_cutoff_lag_days": case.get("element_cutoff_lag_days", 0.0),
                "horizon_bin": horizon_bin(
                    study, (midpoint - utc(case["origin"])).total_seconds() / 86400
                ),
                "status": "matched"
                if j in used
                else ("ambiguous" if reverse[j] else "unpredicted"),
                "matched_imaging_expected": any(
                    rows[i]["status"] == "matched" and rows[i]["imaging_expected"]
                    for i in reverse[j]
                ),
            }
        )
    return {"predictions": rows, "observations": observed}
