"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

from ..core.orbits import _resolve_satellites, _to_utc_timestamp
from ..types import DateInput, SatelliteSelection
from .cache import cache_root, prune_plan_cache, write_cache_file

ESA_S2_PLAN_PAGE = "https://sentinels.copernicus.eu/copernicus/sentinel-2/acquisition-plans"
ESA_BASE_URL = "https://sentinels.copernicus.eu"
_PLAN_RE = re.compile(r"(s2[abc])_mp_acq__kml_(\d{8}t\d{6})_(\d{8}t\d{6})", re.I)


def fetch_s2_acquisition_plans(
    plan_dir: str | Path | None = None,
    start_utc: DateInput | None = None,
    end_utc: DateInput | None = None,
    satellites: SatelliteSelection = ("S2A", "S2B", "S2C"),
    timeout: int = 90,
    verbose: bool = True,
) -> list[Path]:
    """
    Download the ESA Sentinel-2 acquisition-plan KMLs that overlap a time window.

    ESA publishes the mission planning for each Sentinel-2 unit as KML files of
    planned data-takes (usually 10–18 days each, refreshed weekly). This function
    scrapes the acquisition-plans page for KML links, keeps those whose planning
    window (encoded in the file name) overlaps ``[start_utc, end_utc]``, and caches
    them in ``plan_dir``. If the page layout changes, download the files manually
    from the page and drop them into ``plan_dir`` — ``load_s2_acquisition_plans``
    reads any KML there.

    Args:
        plan_dir (str | Path, optional): Cache folder. Defaults to the platform user cache.
            The default cache retains unused plans for at most 30 days and targets
            50 MiB, protecting this request's files. Explicit directories are not pruned.
        start_utc, end_utc (optional): Window of interest. Defaults to now → now + 16 d.
        satellites (Iterable, optional): Units to fetch. Defaults to S2A, S2B, S2C.
        timeout (int, optional): HTTP timeout (s). Defaults to 90.
        verbose (bool, optional): Print progress. Defaults to True.

    Returns:
        list[Path]: Local KML paths covering the window.
    """

    managed_cache = plan_dir is None
    plan_dir = Path(plan_dir) if plan_dir is not None else cache_root() / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    start = _to_utc_timestamp(start_utc) if start_utc is not None else pd.Timestamp.now(tz="UTC")
    end = _to_utc_timestamp(end_utc) if end_utc is not None else start + pd.Timedelta(days=16)
    if end <= start:
        raise ValueError("end_utc must be later than start_utc.")
    if timeout <= 0:
        raise ValueError("timeout must be positive.")
    names = _resolve_satellites(satellites)
    if any(not s.startswith("SENTINEL-2") for s in names):
        raise ValueError("ESA acquisition plans support Sentinel-2 satellites only.")
    wanted = {s.replace("SENTINEL-", "S").lower() for s in names}

    response = requests.get(ESA_S2_PLAN_PAGE, timeout=timeout)
    response.raise_for_status()
    html = response.text
    links = {}
    for m in re.finditer(
        r'href="([^"]*?(s2[abc])_mp_acq__kml_(\d{8}t\d{6})_(\d{8}t\d{6})[^"]*)"', html, re.I
    ):
        href, sat, a, b = m.groups()
        if sat.lower() not in wanted:
            continue
        p0 = pd.Timestamp(datetime.strptime(a.lower(), "%Y%m%dt%H%M%S"), tz="UTC")
        p1 = pd.Timestamp(datetime.strptime(b.lower(), "%Y%m%dt%H%M%S"), tz="UTC")
        if p1 < start or p0 > end:
            continue
        url = href if href.startswith("http") else ESA_BASE_URL + href
        links[f"{sat.lower()}_mp_acq__kml_{a.lower()}_{b.lower()}"] = url

    paths, failed = [], []
    for stem, url in sorted(links.items()):
        path = plan_dir / f"{stem}.kml"
        if path.exists():
            if verbose:
                print(f"✔ {path.name} (cached)")
            paths.append(path)
            continue
        # ESA links occasionally carry a broken suffix or need ?download=true — try variants,
        # and never let one bad link stop the remaining units from downloading.
        base = url.split("?")[0]
        plain = f"{ESA_BASE_URL}/documents/d/sentinel/{stem}"
        candidates = list(
            dict.fromkeys([url, base + "?download=true", plain, plain + "?download=true"])
        )
        last_err = None
        for cand in candidates:
            try:
                r = requests.get(cand, timeout=timeout)
                r.raise_for_status()
                if b"<kml" not in r.content[:2000] and r.content[:2] != b"PK":
                    raise ValueError("response is not KML/KMZ")
                write_cache_file(path, r.content)
                if verbose:
                    print(f"⬇ {path.name} ({len(r.content) / 1e6:.1f} MB)")
                paths.append(path)
                break
            except Exception as exc:
                last_err = exc
        else:
            failed.append(stem)
            if verbose:
                print(
                    f"\x1b[93m⚠ {stem}: download failed on ESA's link ({type(last_err).__name__}). "
                    f"Skipping; save it manually into {plan_dir} if the page link is fixed.\x1b[0m"
                )
    if failed and verbose:
        print(
            f"\x1b[93m⚠ {len(failed)} plan file(s) unavailable — passes they cover will show "
            f"'no plan loaded' (or rely on an older overlapping plan).\x1b[0m"
        )
    if not paths and verbose:
        print(
            "\x1b[93m⚠ No acquisition-plan KMLs found for this window "
            "(plans are typically published ~2 weeks ahead).\x1b[0m"
        )
    if managed_cache:
        prune_plan_cache(plan_dir.parent, protected=paths)
    return paths


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def load_s2_acquisition_plans(source: str | Path | Iterable | None = None) -> gpd.GeoDataFrame:
    """
    Parse ESA Sentinel-2 acquisition-plan KML/KMZ files into a GeoDataFrame of data-takes.

    Each Placemark becomes one row with its unit, plan category (the KML folder,
    e.g. ``NOMINAL`` — the only category distributed to users), data-take id,
    observation start/stop (UTC), absolute/relative orbit and footprint polygon.
    The planning window of each file is taken from the KML document name
    ("... from YYYYMMDDTHHMMSS to YYYYMMDDTHHMMSS") or, failing that, the file name.

    Args:
        source (str | Path | Iterable): A folder of KML/KMZ files, a single file, or a list.

    Returns:
        gpd.GeoDataFrame: EPSG:4326 data-take footprints.
    """

    if source is None:
        source = cache_root() / "plans"
        if not source.exists():
            source = []
    if isinstance(source, (str, Path)) and Path(source).is_dir():
        files = sorted(list(Path(source).glob("*.kml")) + list(Path(source).glob("*.kmz")))
    elif isinstance(source, (str, Path)):
        files = [Path(source)]
    else:
        files = [Path(f) for f in source]

    rows = []
    for f in files:
        raw = f.read_bytes()
        if raw[:2] == b"PK":
            with zipfile.ZipFile(f) as z:
                raw = z.read(next(n for n in z.namelist() if n.lower().endswith(".kml")))
        root = ET.fromstring(raw)

        doc_name = next((el.text or "" for el in root.iter() if _strip_ns(el.tag) == "name"), "")
        m = re.search(r"from\s+(\d{8}T\d{6})\s+to\s+(\d{8}T\d{6})", doc_name, re.I)
        m = m or _PLAN_RE.search(f.name)
        if m:
            a, b = m.groups()[-2:]
            plan_start = pd.Timestamp(datetime.strptime(a.upper(), "%Y%m%dT%H%M%S"), tz="UTC")
            plan_end = pd.Timestamp(datetime.strptime(b.upper(), "%Y%m%dT%H%M%S"), tz="UTC")
        else:
            plan_start = plan_end = pd.NaT

        def walk(node, folders):
            for child in node:
                tag = _strip_ns(child.tag)
                if tag in ("Document", "Folder"):
                    nm = next((c.text for c in child if _strip_ns(c.tag) == "name"), None)
                    walk(child, folders + ([nm] if (tag == "Folder" and nm) else []))
                elif tag == "Placemark":
                    yield_placemark(child, folders)

        def yield_placemark(pm, folders):
            data = {
                d.get("name"): next((v.text for v in d if _strip_ns(v.tag) == "value"), None)
                for d in pm.iter()
                if _strip_ns(d.tag) == "Data"
            }
            polys = []
            for pg in (el for el in pm.iter() if _strip_ns(el.tag) == "Polygon"):
                outer = next((el for el in pg.iter() if _strip_ns(el.tag) == "outerBoundaryIs"), pg)
                coords = next(
                    (el.text for el in outer.iter() if _strip_ns(el.tag) == "coordinates"), ""
                )
                pts = [tuple(map(float, c.split(",")[:2])) for c in coords.split()]
                if len(pts) >= 3:
                    lons = np.array([p[0] for p in pts])
                    if lons.max() - lons.min() > 180:  # antimeridian: shift to 0–360
                        pts = [((x + 360) if x < 0 else x, y) for x, y in pts]
                    polys.append(shapely.make_valid(Polygon(pts)))
            sat_folder = next(
                (fo for fo in folders if re.fullmatch(r"S2[ABC]", fo or "", re.I)), None
            )
            sat_code = (
                sat_folder or (_PLAN_RE.search(f.name).group(1) if _PLAN_RE.search(f.name) else "")
            ).upper()
            rows.append(
                {
                    "satellite": {
                        "S2A": "SENTINEL-2A",
                        "S2B": "SENTINEL-2B",
                        "S2C": "SENTINEL-2C",
                    }.get(sat_code, sat_code),
                    "plan_category": folders[-1] if folders else None,
                    "datatake_id": data.get("ID"),
                    "mode": data.get("Mode"),
                    "obs_start_utc": pd.to_datetime(
                        data.get("ObservationTimeStart"), utc=True, errors="coerce"
                    ),
                    "obs_stop_utc": pd.to_datetime(
                        data.get("ObservationTimeStop"), utc=True, errors="coerce"
                    ),
                    "orbit_absolute": pd.to_numeric(data.get("OrbitAbsolute"), errors="coerce"),
                    "orbit_relative": pd.to_numeric(data.get("OrbitRelative"), errors="coerce"),
                    "plan_file": f.name,
                    "plan_start_utc": plan_start,
                    "plan_end_utc": plan_end,
                    "geometry": unary_union(polys) if polys else None,
                }
            )

        walk(root, [])

    cols = [
        "satellite",
        "plan_category",
        "datatake_id",
        "mode",
        "obs_start_utc",
        "obs_stop_utc",
        "orbit_absolute",
        "orbit_relative",
        "plan_file",
        "plan_start_utc",
        "plan_end_utc",
        "geometry",
    ]
    gdf = gpd.GeoDataFrame(rows, columns=cols, geometry="geometry", crs="EPSG:4326")
    if gdf.empty:
        for column in ["obs_start_utc", "obs_stop_utc", "plan_start_utc", "plan_end_utc"]:
            gdf[column] = pd.Series(dtype="datetime64[ns, UTC]")
    if not files:
        print("📄 No acquisition-plan KML files found.")
    elif len(gdf):
        print(
            f"📄 {len(gdf)} planned data-take(s) from {len(files)} KML file(s): "
            + ", ".join(f"{k} {v}" for k, v in gdf["satellite"].value_counts().sort_index().items())
        )
    return gdf
