"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import re
import warnings
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
ESA_S1_PLAN_PAGE = "https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans"
ESA_BASE_URL = "https://sentinels.copernicus.eu"
_PLAN_RE = re.compile(r"(s2[abc])_mp_acq__kml_(\d{8}t\d{6})_(\d{8}t\d{6})", re.I)
_S1_PLAN_RE = re.compile(r"(s1[cd])_mp_user_(\d{8}t\d{6})_(\d{8}t\d{6})", re.I)


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


def fetch_s1_acquisition_plans(
    plan_dir: str | Path | None = None,
    start_utc: DateInput | None = None,
    end_utc: DateInput | None = None,
    satellites: SatelliteSelection = ("S1C", "S1D"),
    timeout: int = 90,
    verbose: bool = True,
) -> list[Path]:
    """Download Sentinel-1 user acquisition plans overlapping a time window."""
    managed_cache = plan_dir is None
    directory = Path(plan_dir) if plan_dir is not None else cache_root() / "plans"
    directory.mkdir(parents=True, exist_ok=True)
    start = _to_utc_timestamp(start_utc) if start_utc is not None else pd.Timestamp.now(tz="UTC")
    end = _to_utc_timestamp(end_utc) if end_utc is not None else start + pd.Timedelta(days=16)
    if end <= start:
        raise ValueError("end_utc must be later than start_utc.")
    if timeout <= 0:
        raise ValueError("timeout must be positive.")
    names = _resolve_satellites(satellites)
    if any(not s.startswith("SENTINEL-1") for s in names):
        raise ValueError("ESA acquisition plans support Sentinel-1 satellites only.")
    wanted = {s.replace("SENTINEL-", "S").lower() for s in names}
    response = requests.get(ESA_S1_PLAN_PAGE, timeout=timeout)
    response.raise_for_status()
    links = {}
    for m in re.finditer(
        r'href="([^"]*?(s1[cd])_mp_user_(\d{8}t\d{6})_(\d{8}t\d{6})[^" ]*)"',
        response.text,
        re.I,
    ):
        href, sat, a, b = m.groups()
        if sat.lower() not in wanted:
            continue
        p0 = pd.Timestamp(datetime.strptime(a.lower(), "%Y%m%dt%H%M%S"), tz="UTC")
        p1 = pd.Timestamp(datetime.strptime(b.lower(), "%Y%m%dt%H%M%S"), tz="UTC")
        if p1 < start or p0 > end:
            continue
        links[f"{sat.lower()}_mp_user_{a.lower()}_{b.lower()}"] = (
            href if href.startswith("http") else ESA_BASE_URL + href
        )
    paths, failed = [], []
    for stem, url in sorted(links.items()):
        path = directory / f"{stem}.kml"
        if path.exists():
            paths.append(path)
            continue
        base = url.split("?")[0]
        plain = f"{ESA_BASE_URL}/documents/d/sentinel/{stem}"
        candidates = list(
            dict.fromkeys([url, base + "?download=true", plain, plain + "?download=true"])
        )
        last_err = None
        for candidate in candidates:
            try:
                r = requests.get(candidate, timeout=timeout)
                r.raise_for_status()
                if b"<kml" not in r.content[:2000] and r.content[:2] != b"PK":
                    raise ValueError("response is not KML/KMZ")
                write_cache_file(path, r.content)
                paths.append(path)
                break
            except Exception as exc:
                last_err = exc
        else:
            failed.append(stem)
            if verbose:
                print(f"\x1b[93m⚠ {stem}: download failed ({type(last_err).__name__})\x1b[0m")
    if failed and verbose:
        print(f"\x1b[93m⚠ {len(failed)} Sentinel-1 plan file(s) unavailable.\x1b[0m")
    if managed_cache:
        prune_plan_cache(directory.parent, protected=paths)
    return paths


def fetch_acquisition_plans(
    plan_dir: str | Path | None = None,
    start_utc: DateInput | None = None,
    end_utc: DateInput | None = None,
    satellites: SatelliteSelection | None = None,
    timeout: int = 90,
    verbose: bool = True,
) -> list[Path]:
    """Fetch all requested Sentinel-1/2 plans through mission-specific adapters.

    Landsat is intentionally rejected because this adapter only handles ESA plans.
    If both missions are selected, each mission's page is requested independently
    and the resulting paths are combined.
    """
    selection = satellites if satellites is not None else ("S1C", "S1D", "S2A", "S2B", "S2C")
    names = _resolve_satellites(selection)
    if any(not name.startswith("SENTINEL-") for name in names):
        raise ValueError("ESA acquisition plans support Sentinel-1 and Sentinel-2 only.")
    paths: list[Path] = []
    failures: list[str] = []
    managed_cache = plan_dir is None
    common_dir = Path(plan_dir) if plan_dir is not None else cache_root() / "plans"
    common_dir.mkdir(parents=True, exist_ok=True)
    s1 = [name for name in names if name.startswith("SENTINEL-1")]
    s2 = [name for name in names if name.startswith("SENTINEL-2")]
    if s1:
        try:
            paths.extend(
                fetch_s1_acquisition_plans(common_dir, start_utc, end_utc, s1, timeout, verbose)
            )
        except (requests.RequestException, RuntimeError, OSError) as exc:
            failures.append(f"Sentinel-1: {exc}")
            fallback = common_dir
            paths.extend(
                sorted(
                    list(fallback.glob("s1[cd]_mp_user_*.kml"))
                    + list(fallback.glob("s1[cd]_mp_user_*.kmz"))
                )
            )
    if s2:
        try:
            paths.extend(
                fetch_s2_acquisition_plans(common_dir, start_utc, end_utc, s2, timeout, verbose)
            )
        except (requests.RequestException, RuntimeError, OSError) as exc:
            failures.append(f"Sentinel-2: {exc}")
            fallback = common_dir
            paths.extend(
                sorted(
                    list(fallback.glob("s2[abc]_mp_acq__kml_*.kml"))
                    + list(fallback.glob("s2[abc]_mp_acq__kml_*.kmz"))
                )
            )
    if failures:
        message = "ESA plan retrieval failed: " + "; ".join(failures)
        if paths:
            warnings.warn(message + "; using available plans.", UserWarning, stacklevel=2)
        else:
            raise RuntimeError(message)
    if managed_cache:
        prune_plan_cache(common_dir.parent, protected=paths)
    return paths


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _plan_window(name: str, document_name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    match = re.search(
        r"from\s+(\d{4}-?\d{2}-?\d{2}T\d{2}:?\d{2}:?\d{2})\s+to\s+(\d{4}-?\d{2}-?\d{2}T\d{2}:?\d{2}:?\d{2})",
        document_name,
        re.I,
    )
    match = match or _PLAN_RE.search(name) or _S1_PLAN_RE.search(name)
    if not match:
        return pd.NaT, pd.NaT
    a, b = (value.replace("-", "").replace(":", "") for value in match.groups()[-2:])
    return (
        pd.Timestamp(datetime.strptime(a.upper(), "%Y%m%dT%H%M%S"), tz="UTC"),
        pd.Timestamp(datetime.strptime(b.upper(), "%Y%m%dT%H%M%S"), tz="UTC"),
    )


def _kml_points(element: ET.Element) -> list[tuple[float, float]]:
    text = next(
        (child.text for child in element.iter() if _strip_ns(child.tag) == "coordinates"), ""
    )
    points = []
    for coordinate in (text or "").split():
        try:
            lon, lat = map(float, coordinate.split(",")[:2])
        except (TypeError, ValueError):
            continue
        points.append((lon, lat))
    return points


def load_acquisition_plans(source: str | Path | Iterable | None = None) -> gpd.GeoDataFrame:
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
    plan_windows: list[dict[str, object]] = []
    for f in files:
        raw = f.read_bytes()
        if raw[:2] == b"PK":
            with zipfile.ZipFile(f) as z:
                raw = z.read(next(n for n in z.namelist() if n.lower().endswith(".kml")))
        root = ET.fromstring(raw)

        doc_name = next((el.text or "" for el in root.iter() if _strip_ns(el.tag) == "name"), "")
        plan_start, plan_end = _plan_window(f.name, doc_name)
        file_match = _PLAN_RE.search(f.name) or _S1_PLAN_RE.search(f.name)
        plan_satellite = ""
        if file_match:
            code = file_match.group(1).upper()
            plan_satellite = {
                "S1C": "SENTINEL-1C",
                "S1D": "SENTINEL-1D",
                "S2A": "SENTINEL-2A",
                "S2B": "SENTINEL-2B",
                "S2C": "SENTINEL-2C",
            }.get(code, code)
        plan_windows.append(
            {
                "plan_file": f.name,
                "satellite": plan_satellite,
                "plan_start_utc": plan_start,
                "plan_end_utc": plan_end,
            }
        )

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
            data.update(
                {
                    d.get("name"): d.text
                    for d in pm.iter()
                    if _strip_ns(d.tag) == "SimpleData" and d.get("name")
                }
            )
            data = {str(k): v for k, v in data.items() if k}
            data.setdefault("SatelliteId", data.get("Satellite"))
            data.setdefault("DatatakeId", data.get("ID"))
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
            if not polys:
                for ring in (el for el in pm.iter() if _strip_ns(el.tag) == "LinearRing"):
                    pts = _kml_points(ring)
                    if len(pts) >= 3:
                        lons = np.array([p[0] for p in pts])
                        if lons.max() - lons.min() > 180:
                            pts = [((x + 360) if x < 0 else x, y) for x, y in pts]
                        polys.append(shapely.make_valid(Polygon(pts)))
            sat_folder = next(
                (fo for fo in folders if re.fullmatch(r"S[12][A-D]", fo or "", re.I)), None
            )
            sat_code = (
                str(data.get("SatelliteId") or "").strip()
                or sat_folder
                or (
                    (_PLAN_RE.search(f.name) or _S1_PLAN_RE.search(f.name)).group(1)
                    if (_PLAN_RE.search(f.name) or _S1_PLAN_RE.search(f.name))
                    else ""
                )
            ).upper()
            if sat_code.startswith("S1"):
                satellite = {"S1C": "SENTINEL-1C", "S1D": "SENTINEL-1D"}.get(
                    sat_code, f"SENTINEL-{sat_code[1:]}"
                )
            else:
                satellite = {
                    "S2A": "SENTINEL-2A",
                    "S2B": "SENTINEL-2B",
                    "S2C": "SENTINEL-2C",
                }.get(sat_code, sat_code)
            times = next((el for el in pm.iter() if _strip_ns(el.tag) == "TimeSpan"), None)
            span_children = times if times is not None else []
            begin = next((el.text for el in span_children if _strip_ns(el.tag) == "begin"), None)
            end = next((el.text for el in span_children if _strip_ns(el.tag) == "end"), None)
            obs_start = pd.to_datetime(
                data.get("ObservationTimeStart") or begin, utc=True, errors="coerce"
            )
            obs_stop = pd.to_datetime(
                data.get("ObservationTimeStop") or end, utc=True, errors="coerce"
            )
            rows.append(
                {
                    "satellite": satellite,
                    "plan_category": folders[-1] if folders else None,
                    "datatake_id": data.get("DatatakeId"),
                    "mode": data.get("Mode"),
                    "swath": data.get("Swath"),
                    "polarisation": data.get("Polarisation"),
                    "obs_start_utc": obs_start,
                    "obs_stop_utc": obs_stop,
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
        "swath",
        "polarisation",
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
    gdf.attrs["plan_windows"] = plan_windows
    if gdf.empty:
        for column in ["obs_start_utc", "obs_stop_utc", "plan_start_utc", "plan_end_utc"]:
            gdf[column] = pd.Series(dtype="datetime64[ns, UTC]")
        for column in ["swath", "polarisation"]:
            gdf[column] = pd.Series(dtype="str")
    if not files:
        print("📄 No acquisition-plan KML files found.")
    elif len(gdf):
        print(
            f"📄 {len(gdf)} planned data-take(s) from {len(files)} KML file(s): "
            + ", ".join(f"{k} {v}" for k, v in gdf["satellite"].value_counts().sort_index().items())
        )
    return gdf


def load_s2_acquisition_plans(source: str | Path | Iterable | None = None) -> gpd.GeoDataFrame:
    """Backward-compatible Sentinel-2 plan loader."""
    out = load_acquisition_plans(source)
    return out[out["satellite"].str.startswith("SENTINEL-2")].copy()


def load_s1_acquisition_plans(source: str | Path | Iterable | None = None) -> gpd.GeoDataFrame:
    """Load Sentinel-1 user acquisition plans from KML/KMZ files."""
    out = load_acquisition_plans(source)
    return out[out["satellite"].str.startswith("SENTINEL-1")].copy()
