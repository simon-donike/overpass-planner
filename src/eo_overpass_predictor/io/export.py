"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import json
import re
from html import escape
from importlib.resources import files
from pathlib import Path

import geopandas as gpd
import pandas as pd

from ..core.geometry import resolve_roi_timezone
from ..core.orbits import SATELLITE_CATALOGUE, _resolve_satellites
from ..types import SatelliteSelection, TLERecord


def export_overpass_csv(
    passes: gpd.GeoDataFrame,
    out_dir: str | Path = "OUTPUT",
    file_name: str | None = None,
    expected_only: bool = False,
) -> Path:
    """
    Write the overpass report to CSV with both UTC and ROI-local times.

    Times are written as ISO-8601 strings: UTC columns end in ``Z``; local columns
    carry their explicit offset (``+11:00``) so the file is unambiguous across DST
    changes, plus split ``local_date`` / ``local_time`` columns for spreadsheets.
    The swath geometry is not written (see ``export_overpass_footprints``).

    Args:
        passes (gpd.GeoDataFrame): Output of ``predict_overpasses`` (optionally cross-checked).
        out_dir (str | Path, optional): Output folder. Defaults to "OUTPUT".
        file_name (str, optional): Override the file name. Defaults to
            ``overpasses_<roi>_<start>_<end>.csv``.
        expected_only (bool, optional): Write only rows with ``imaging_expected``.

    Returns:
        Path: Path of the written CSV.
    """

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(passes.drop(columns="geometry")).copy()
    if expected_only:
        df = df[df["imaging_expected"]]

    for c in ["roi_entry_utc", "overpass_utc", "roi_exit_utc", "tle_epoch_utc"]:
        if c in df:
            df[c] = df[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in ["roi_entry_local", "overpass_local", "roi_exit_local"]:
        if c in df:
            df[c] = df[c].apply(lambda t: t.isoformat(timespec="seconds"))
    if "overpass_local" in passes:
        loc = passes.loc[df.index, "overpass_local"]
        df.insert(df.columns.get_loc("timezone"), "local_date", loc.dt.strftime("%Y-%m-%d").values)
        df.insert(df.columns.get_loc("timezone"), "local_time", loc.dt.strftime("%H:%M:%S").values)
        df.insert(df.columns.get_loc("timezone"), "local_weekday", loc.dt.strftime("%A").values)

    if file_name is None:
        roi = re.sub(r"[^A-Za-z0-9_-]+", "_", str(passes.attrs.get("roi_name", "ROI")))
        s = passes.attrs.get("window_start_utc")
        e = passes.attrs.get("window_end_utc")
        span = f"_{s:%Y%m%d}_{e:%Y%m%d}" if s is not None and e is not None else ""
        file_name = f"overpasses_{roi}{span}.csv"
    path = out_dir / file_name
    df.to_csv(path, index=False)
    print(f"💾 {len(df)} overpass(es) written to {path}")
    return path


def export_overpass_footprints(passes: gpd.GeoDataFrame, path: str | Path) -> Path:
    """Write the per-pass swath strips to a GeoPackage / GeoJSON / shapefile for QGIS or ArcGIS."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    g = passes.copy()
    for c in g.columns:
        if pd.api.types.is_datetime64_any_dtype(g[c]):
            g[c] = g[c].astype(str)
    g.to_file(path)
    print(f"💾 {len(g)} footprint(s) written to {path}")
    return path


def export_live_tracker_html(
    tles: dict[str, TLERecord],
    roi_gdf: gpd.GeoDataFrame,
    out_path: str | Path = Path("OUTPUT") / "live_tracker.html",
    passes: gpd.GeoDataFrame | None = None,
    timezone_name: str | None = None,
    satellites: SatelliteSelection = None,
    title: str = "Overpass Tracker",
    standalone: bool = True,
) -> Path:
    """
    Write a standalone, self-updating live-tracker web page (no Python needed to view it).

    The page embeds the TLEs, ROI and predicted overpasses, and propagates the
    orbits in the browser with satellite.js (SGP4), so it keeps tracking in real
    time on any device — useful outside Jupyter, on GitHub Pages, or on a wall
    screen. It shows positions, ground tracks, live swaths, the terminator,
    UTC/ROI-local clocks and an overpass countdown list.

    The embedded TLEs age: re-export after refreshing TLEs (the page shows TLE age).

    Args:
        tles (dict): Output of ``fetch_tles``.
        roi_gdf (gpd.GeoDataFrame): ROI from ``load_roi``.
        out_path (str | Path, optional): Output HTML path. Defaults to ``OUTPUT/live_tracker.html``.
        passes (gpd.GeoDataFrame, optional): Output of ``predict_overpasses`` for the countdown list.
        timezone_name (str, optional): IANA zone. Defaults to the ROI zone.
        satellites (Iterable, optional): Subset to include.
        title (str, optional): Page title. Defaults to "Overpass Tracker".
        standalone (bool, optional): Wrap in a full ``<html>`` document. Defaults to True.

    Returns:
        Path: Path of the written HTML file.
    """

    names = _resolve_satellites(satellites) if satellites is not None else list(tles)
    tz_name = timezone_name or resolve_roi_timezone(roi_gdf, verbose=False)
    sats = [
        {
            "name": n,
            "line1": tles[n]["line1"],
            "line2": tles[n]["line2"],
            "colour": SATELLITE_CATALOGUE[n]["colour"],
            "swath_km": SATELLITE_CATALOGUE[n]["swath_km"],
            "mission": SATELLITE_CATALOGUE[n]["mission"],
            "epoch": tles[n]["epoch_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for n in names
    ]
    plist = []
    if passes is not None and len(passes):
        for _, r in passes[passes["satellite"].isin(names)].iterrows():
            plist.append(
                {
                    "sat": r["satellite"],
                    "in": r["roi_entry_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ca": r["overpass_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "out": r["roi_exit_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "cov": float(r["roi_coverage_pct"]),
                    "dir": r["direction"],
                    "exp": bool(r["imaging_expected"]),
                    "plan": r.get("esa_plan_status") if "esa_plan_status" in r else None,
                }
            )
    payload = {
        "title": title,
        "tz": tz_name,
        "roi_name": str(roi_gdf["name"].iloc[0]) if "name" in roi_gdf else "ROI",
        "roi": json.loads(gpd.GeoSeries([roi_gdf.geometry.iloc[0]]).to_json()),
        "sats": sats,
        "passes": plist,
        "generated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    resources = files("eo_overpass_predictor.resources")
    template = resources.joinpath("live_tracker_template.html").read_text(encoding="utf-8")
    land = resources.joinpath("world_land_50m.json").read_text(encoding="utf-8")

    def safe(s: str) -> str:
        return s.replace("</", "<\\/")  # keep embedded JSON from closing the script

    html = (
        template.replace("__TITLE__", escape(title))
        .replace("/*__PAYLOAD__*/null", safe(json.dumps(payload)))
        .replace("/*__LAND__*/null", land)
    )
    if standalone:
        html = (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + html
            + "\n</html>\n"
        )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"🌐 Live tracker written to {out_path} — open it in any browser.")
    return out_path
