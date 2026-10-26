"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import geopandas as gpd
import pandas as pd

from ..presentation import describe_pass
from ..types import DateInput
from .orbits import _to_utc_timestamp


@contextmanager
def process_timer(process_name: str = "Process") -> Iterator[None]:
    """
    Measure and report the execution time of a code block using a context manager.

    Records start and end wall-clock timestamps, prints readable timing
    information and the total elapsed duration in a human-friendly format.

    Args:
        process_name (str, optional): Descriptive name of the process being timed.
            Defaults to "Process".

    Yields:
        None: Control is passed to the wrapped block inside the ``with`` statement.
    """

    start_time = time.time()
    print(f"\n--- Starting {process_name} ---")
    print(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}\n")
    try:
        yield
    finally:
        elapsed = time.time() - start_time
        h, m, s = int(elapsed // 3600), int((elapsed % 3600) // 60), elapsed % 60
        print(f"\n--- {process_name} Complete ---")
        if h:
            msg = f"{h}h {m}m {int(s)}s"
        elif m:
            msg = f"{m}m {int(s)}s"
        else:
            msg = f"{s:.1f}s"
        print("\x1b[92m" + f"Elapsed time: {msg}" + "\x1b[0m")


def summarise_overpasses(passes: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Tabulate predicted overpasses in a compact, human-readable form.

    Args:
        passes (gpd.GeoDataFrame): Output of ``predict_overpasses``.

    Returns:
        pd.DataFrame: Satellite, local day/date, local time window, UTC time,
        direction, ROI coverage, off-nadir angle, sun elevation, expected flag,
        acquisition evidence, TLE freshness and notes. Raw ESA plan status is
        also included when available.
    """

    if passes.empty:
        return pd.DataFrame()
    tz = passes["timezone"].iloc[0]
    df = pd.DataFrame(
        {
            "Satellite": passes["satellite"],
            "Local date": passes["overpass_local"].dt.strftime("%a %d %b %Y"),
            f"Local time ({tz})": passes["roi_entry_local"].dt.strftime("%H:%M:%S")
            + " – "
            + passes["roi_exit_local"].dt.strftime("%H:%M:%S"),
            "UTC offset": passes["utc_offset"],
            "Overpass (UTC)": passes["overpass_utc"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Direction": passes["direction"],
            "ROI cover %": passes["roi_coverage_pct"],
            "View angle °": passes["view_angle_deg"],
            "Sun elev °": passes["sun_elevation_deg"],
            "Expected": passes["imaging_expected"].map({True: "✔", False: "✘"}),
        }
    )
    if "esa_plan_status" in passes:
        df["ESA plan"] = passes["esa_plan_status"]
    statuses = [describe_pass(row) for _, row in passes.iterrows()]
    df["Acquisition"] = [status["acquisition_label"] for status in statuses]
    df["TLE data"] = [
        f"{status['orbital_data']} ({abs(status['tle_age_days']):.1f} d from pass)"
        for status in statuses
    ]
    df["Notes"] = [
        " ".join(warning["message"] for warning in status["warnings"]) for status in statuses
    ]
    return df


def next_overpasses(passes: gpd.GeoDataFrame, now_utc: DateInput | None = None) -> pd.DataFrame:
    """Next upcoming overpass per satellite with a live countdown (n2yo-style)."""
    now = _to_utc_timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
    up = passes[passes["roi_exit_utc"] >= now]
    if up.empty:
        return pd.DataFrame()
    nxt = up.sort_values("overpass_utc").groupby("satellite", sort=False).head(1)

    def _cd(t):
        s = int((t - now).total_seconds())
        if s <= 0:
            return "pass active"
        d, r = divmod(s, 86400)
        h, r = divmod(r, 3600)
        return f"{d}d {h:02d}h {r // 60:02d}m"

    return pd.DataFrame(
        {
            "Satellite": nxt["satellite"].values,
            "Countdown": [_cd(t) for t in nxt["roi_entry_utc"]],
            "Local": nxt["overpass_local"].dt.strftime("%a %d %b %H:%M:%S").values,
            "UTC": nxt["overpass_utc"].dt.strftime("%Y-%m-%d %H:%M:%S").values,
            "ROI cover %": nxt["roi_coverage_pct"].values,
        }
    )
