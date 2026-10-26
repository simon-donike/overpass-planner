"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import transform


def crosscheck_acquisition_plan(
    passes: gpd.GeoDataFrame,
    plans: gpd.GeoDataFrame,
    roi_gdf: gpd.GeoDataFrame,
    time_tolerance_s: float = 120.0,
) -> gpd.GeoDataFrame:
    """
    Flag each predicted Sentinel-2 overpass as planned / not planned by ESA.

    A pass is ``planned (NOMINAL)`` when a data-take of the same unit overlaps the
    pass's ROI entry–exit window (± ``time_tolerance_s``) and its footprint
    intersects the ROI. Data-takes in other plan categories (e.g. vicarious
    calibration) are reported as such because they are not distributed. Passes
    outside the time span of every loaded plan for that unit are reported as
    ``no plan loaded``; Landsat rows get ``n/a``.

    Args:
        passes (gpd.GeoDataFrame): Output of ``predict_overpasses``.
        plans (gpd.GeoDataFrame): Output of ``load_s2_acquisition_plans``.
        roi_gdf (gpd.GeoDataFrame): ROI from ``load_roi``.
        time_tolerance_s (float, optional): Timing tolerance. Defaults to 120 s.

    Returns:
        gpd.GeoDataFrame: ``passes`` with ``esa_plan_status``, ``esa_datatake_id``
        and ``esa_relative_orbit`` columns.
    """

    if not np.isfinite(time_tolerance_s) or time_tolerance_s < 0:
        raise ValueError("time_tolerance_s must be finite and nonnegative.")
    out = passes.copy()
    roi = roi_gdf.geometry.iloc[0]
    roi_alt = transform(
        lambda x, y, z=None: (np.where(np.asarray(x) < 0, np.asarray(x) + 360, x), y), roi
    )
    roi_shift = transform(lambda x, y, z=None: (np.asarray(x) - 360, y), roi)
    tol = pd.Timedelta(seconds=time_tolerance_s)
    status, dt_id, rel, modes, pols, files = [], [], [], [], [], []

    for _, r in out.iterrows():
        if r["mission"] not in {"Sentinel-1", "Sentinel-2"}:
            status.append("n/a")
            dt_id.append(None)
            rel.append(None)
            modes.append(None)
            pols.append(None)
            files.append(None)
            continue
        p_all = plans[plans["satellite"] == r["satellite"]]
        p = p_all
        if r["mission"] == "Sentinel-1":
            revisions = list(plans.attrs.get("plan_windows", []))
            revisions.extend(
                {
                    "plan_file": row["plan_file"],
                    "satellite": row["satellite"],
                    "plan_start_utc": row["plan_start_utc"],
                    "plan_end_utc": row["plan_end_utc"],
                }
                for _, row in p_all.iterrows()
            )
            covered = [
                revision
                for revision in revisions
                if revision.get("satellite") == r["satellite"]
                and pd.notna(revision.get("plan_start_utc"))
                and revision["plan_start_utc"] <= r["overpass_utc"]
                and revision["plan_end_utc"] >= r["overpass_utc"]
            ]
            if covered:
                winner = max(
                    covered,
                    key=lambda revision: (revision["plan_start_utc"], revision["plan_file"]),
                )
                p = p_all[p_all["plan_file"] == winner["plan_file"]]
        spans = p[["plan_start_utc", "plan_end_utc"]].drop_duplicates()
        covered = (
            (spans["plan_start_utc"] <= r["overpass_utc"])
            & (spans["plan_end_utc"] >= r["overpass_utc"])
        ).any()
        if not covered:
            windows = plans.attrs.get("plan_windows", [])
            if r["mission"] == "Sentinel-1":
                matching_windows = [
                    window
                    for window in windows
                    if window.get("satellite") == r["satellite"]
                    and pd.notna(window.get("plan_start_utc"))
                    and window["plan_start_utc"] <= r["overpass_utc"]
                    and window["plan_end_utc"] >= r["overpass_utc"]
                ]
                if matching_windows:
                    status.append("not planned")
                    dt_id.append(None)
                    rel.append(None)
                    modes.append(None)
                    pols.append(None)
                    files.append(
                        max(matching_windows, key=lambda w: (w["plan_start_utc"], w["plan_file"]))[
                            "plan_file"
                        ]
                    )
                    continue
            status.append("no plan loaded")
            dt_id.append(None)
            rel.append(None)
            modes.append(None)
            pols.append(None)
            files.append(None)
            continue
        cand = p[
            (p["obs_start_utc"] <= r["roi_exit_utc"] + tol)
            & (p["obs_stop_utc"] >= r["roi_entry_utc"] - tol)
        ]
        if r["mission"] == "Sentinel-1" and r.get("mode"):
            cand = cand[cand["mode"].fillna("").str.upper() == str(r["mode"]).upper()]
        cand = cand[cand.geometry.notna()]
        cand = cand[
            cand.geometry.intersects(roi)
            | cand.geometry.intersects(roi_alt)
            | cand.geometry.intersects(roi_shift)
        ]
        if cand.empty:
            status.append("not planned")
            dt_id.append(None)
            rel.append(None)
            modes.append(None)
            pols.append(None)
            files.append(None)
            continue
        nominal = cand[cand["plan_category"].fillna("").str.upper() == "NOMINAL"]
        best = (nominal if len(nominal) else cand).iloc[0]
        if r["mission"] == "Sentinel-1":
            status.append("planned")
        else:
            status.append(f"planned ({best['plan_category']})")
        dt_id.append(best["datatake_id"])
        rel.append(int(best["orbit_relative"]) if pd.notna(best["orbit_relative"]) else None)
        modes.append(best.get("mode"))
        pols.append(best.get("polarisation"))
        files.append(best.get("plan_file"))

    out["esa_plan_status"] = status
    out["esa_datatake_id"] = dt_id
    out["esa_relative_orbit"] = pd.array(rel, dtype="Int64")
    out["esa_mode"] = modes
    out["esa_polarisation"] = pols
    out["esa_plan_file"] = files
    if out.empty:
        for column in ["esa_plan_status", "esa_datatake_id"]:
            out[column] = pd.Series(index=out.index, dtype="str")
    counts = out.loc[
        out["mission"].isin(["Sentinel-1", "Sentinel-2"]), "esa_plan_status"
    ].value_counts()
    print(
        "🗓️  ESA plan cross-check: "
        + (", ".join(f"{k}: {v}" for k, v in counts.items()) or "no Sentinel-1/2 passes")
    )
    return out


def crosscheck_s2_acquisition_plan(
    passes: gpd.GeoDataFrame,
    plans: gpd.GeoDataFrame,
    roi_gdf: gpd.GeoDataFrame,
    time_tolerance_s: float = 120.0,
) -> gpd.GeoDataFrame:
    """Backward-compatible Sentinel-2-only name for the generic matcher."""
    restricted = passes.copy()
    if not restricted.empty:
        restricted.loc[restricted["mission"] == "Sentinel-1", "mission"] = "Other"
    out = crosscheck_acquisition_plan(restricted, plans, roi_gdf, time_tolerance_s)
    out["mission"] = passes["mission"].values
    return out
