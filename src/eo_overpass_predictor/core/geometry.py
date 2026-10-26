"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import geopandas as gpd
from pyproj import CRS, Transformer
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from timezonefinder import TimezoneFinder

_TZ_FINDER = None


def _roi_anchor(geom: BaseGeometry) -> Point:
    """Centroid if it lies inside the ROI, otherwise a guaranteed interior point."""
    c = geom.centroid
    return c if geom.covers(c) else geom.representative_point()


def _local_equal_area(geom: BaseGeometry):
    """Forward/inverse transformers for a Lambert azimuthal equal-area CRS centred on the ROI (metres)."""
    a = _roi_anchor(geom)
    crs = CRS.from_proj4(f"+proj=laea +lat_0={a.y} +lon_0={a.x} +datum=WGS84 +units=m +no_defs")
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    return fwd, inv


def resolve_roi_timezone(
    roi_gdf: gpd.GeoDataFrame, override: str | None = None, verbose: bool = True
) -> str:
    """
    Resolve the IANA time zone of the ROI (e.g. "Australia/Melbourne").

    The zone is looked up at the ROI anchor point with ``timezonefinder`` (offline
    polygon database). Using an IANA zone rather than a fixed offset means
    daylight-saving transitions inside the prediction window are handled
    per-pass. The ROI corners are also checked: if they fall in different zones
    a warning is printed and the anchor-point zone is used. Offshore ROIs
    resolve to the nautical ``Etc/GMT±N`` zone.

    Args:
        roi_gdf (gpd.GeoDataFrame): ROI from ``load_roi``.
        override (str, optional): Force a specific IANA zone name.
        verbose (bool, optional): Print the resolved zone. Defaults to True.

    Returns:
        str: IANA time-zone name.
    """

    global _TZ_FINDER
    if override:
        ZoneInfo(override)  # validates the name
        tz_name = override
    else:
        if _TZ_FINDER is None:
            _TZ_FINDER = TimezoneFinder()
        geom = roi_gdf.geometry.iloc[0]
        a = _roi_anchor(geom)
        tz_name = _TZ_FINDER.timezone_at(lng=a.x, lat=a.y)
        if tz_name is None:
            offset = int(round(a.x / 15.0))
            tz_name = "Etc/GMT" + (f"{-offset:+d}" if offset else "")
        minx, miny, maxx, maxy = geom.bounds
        corners = {
            _TZ_FINDER.timezone_at(lng=x, lat=y)
            for x, y in [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
        } - {None}
        if len(corners | {tz_name}) > 1 and verbose:
            print(
                f"\x1b[93m⚠ ROI spans several time zones {sorted(corners | {tz_name})}; "
                f"using '{tz_name}' (ROI anchor). Pass override= to choose another.\x1b[0m"
            )
    if verbose:
        now = datetime.now(ZoneInfo(tz_name))
        print(
            f"🕒 ROI time zone: {tz_name} (currently UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}, {now.tzname()})"
        )
    return tz_name
