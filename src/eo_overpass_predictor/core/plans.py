"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import transform


def crosscheck_s2_acquisition_plan(
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
    tol = pd.Timedelta(seconds=time_tolerance_s)
    status, dt_id, rel = [], [], []

    for _, r in out.iterrows():
        if r["mission"] != "Sentinel-2":
            status.append("n/a")
            dt_id.append(None)
            rel.append(None)
            continue
        p = plans[plans["satellite"] == r["satellite"]]
        spans = p[["plan_start_utc", "plan_end_utc"]].drop_duplicates()
        covered = (
            (spans["plan_start_utc"] <= r["overpass_utc"])
            & (spans["plan_end_utc"] >= r["overpass_utc"])
        ).any()
        if not covered:
            status.append("no plan loaded")
            dt_id.append(None)
            rel.append(None)
            continue
        cand = p[
            (p["obs_start_utc"] <= r["roi_exit_utc"] + tol)
            & (p["obs_stop_utc"] >= r["roi_entry_utc"] - tol)
        ]
        cand = cand[cand.geometry.notna()]
        cand = cand[cand.geometry.intersects(roi) | cand.geometry.intersects(roi_alt)]
        if cand.empty:
            status.append("not planned")
            dt_id.append(None)
            rel.append(None)
            continue
        nominal = cand[cand["plan_category"].fillna("").str.upper() == "NOMINAL"]
        best = (nominal if len(nominal) else cand).iloc[0]
        status.append(f"planned ({best['plan_category']})")
        dt_id.append(best["datatake_id"])
        rel.append(int(best["orbit_relative"]) if pd.notna(best["orbit_relative"]) else None)

    out["esa_plan_status"] = status
    out["esa_datatake_id"] = dt_id
    out["esa_relative_orbit"] = pd.array(rel, dtype="Int64")
    if out.empty:
        for column in ["esa_plan_status", "esa_datatake_id"]:
            out[column] = pd.Series(index=out.index, dtype="str")
    counts = out.loc[out["mission"] == "Sentinel-2", "esa_plan_status"].value_counts()
    print(
        "🗓️  ESA plan cross-check: "
        + (", ".join(f"{k}: {v}" for k, v in counts.items()) or "no Sentinel-2 passes")
    )
    return out
