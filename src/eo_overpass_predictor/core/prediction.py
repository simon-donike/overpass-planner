"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import math
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Polygon
from shapely.ops import transform
from skyfield.api import wgs84

from ..types import DateInput, SatelliteSelection, TLERecord
from .geometry import _local_equal_area, _roi_anchor, resolve_roi_timezone
from .orbits import (
    EARTH_RADIUS_KM,
    GEOD,
    SATELLITE_CATALOGUE,
    _build_satellite,
    _propagate,
    _resolve_satellites,
    _to_utc_timestamp,
    _ts,
    solar_elevation_deg,
)


def predict_overpasses(
    roi_gdf: gpd.GeoDataFrame,
    tles: dict[str, TLERecord],
    start_utc: DateInput | None = None,
    days: float = 16.0,
    satellites: SatelliteSelection = None,
    timezone_name: str | None = None,
    daytime_descending_only: bool = True,
    min_sun_elevation_deg: float = 0.0,
    min_roi_coverage_pct: float = 0.0,
    coarse_step_s: float = 30.0,
    fine_step_s: float = 1.0,
    verbose: bool = True,
) -> gpd.GeoDataFrame:
    """
    Predict when each satellite's instrument swath will image the ROI.

    This is the core engine. Unlike horizon-based pass predictors (n2yo, Heavens-
    Above), which report when a satellite is merely *above the horizon*, this
    function asks whether the ROI falls inside the *instrument footprint*:

        1. **Coarse scan** — the orbit is propagated (SGP4) every ``coarse_step_s``
           seconds across the window and the geodesic distance from each sub-satellite
           point to the ROI anchor is computed. Samples closer than
           half-swath + ROI radius + 150 km are grouped into candidate windows (the
           margin guarantees no imaging pass can fall between two coarse samples).
        2. **Fine refinement** — each candidate is re-propagated every ``fine_step_s``
           seconds. The ground-track heading is derived from successive sub-points and
           the instantaneous cross-track scan line (±half swath, perpendicular to the
           ground track, as for yaw-steered pushbroom imagers) is built geodesically.
        3. **Intersection** — scan lines and the swath strip are projected into an
           ROI-centred Lambert azimuthal equal-area CRS and intersected with the ROI,
           giving ROI entry/exit times and the true fraction of the ROI imaged.
        4. **Attribution** — direction (ascending/descending), nadir offset, view angle,
           peak elevation seen from the ROI, solar elevation, TLE age, and an
           ``imaging_expected`` flag (descending + sun above threshold + inside the
           mission's latitude limits) are attached; times are converted to local time.

    Args:
        roi_gdf (gpd.GeoDataFrame): ROI from ``load_roi``.
        tles (dict): Output of ``fetch_tles``.
        start_utc (str | datetime, optional): Window start (naive = UTC). Defaults to now.
        days (float, optional): Window length in days. Defaults to 16 (one Landsat
            repeat cycle; Sentinel-2 repeats every 10 days per unit).
        satellites (SatelliteSelection, optional): Names, aliases, NORAD ids or groups
            ("S2" for Sentinel-2A/B/C, "LS" for Landsat 8/9). Defaults to all in ``tles``.
        timezone_name (str, optional): IANA zone for local times. Defaults to the
            zone resolved from the ROI.
        daytime_descending_only (bool, optional): Keep only passes where imaging is
            expected (descending, sunlit). Set False to also list night/ascending
            geometric passes. Defaults to True.
        min_sun_elevation_deg (float, optional): Sun-elevation threshold at the ROI for
            ``imaging_expected``. Defaults to 0.
        min_roi_coverage_pct (float, optional): Drop passes covering less of the ROI.
            Defaults to 0 (any intersection).
        coarse_step_s (float, optional): Coarse search step. Defaults to 30.
        fine_step_s (float, optional): Refinement step (time resolution). Defaults to 1.
        verbose (bool, optional): Print a per-satellite summary. Defaults to True.

    Returns:
        gpd.GeoDataFrame: One row per overpass, sorted by time, EPSG:4326 swath strip
        geometry (clipped to ±~150 km around the ROI) and the columns:
        ``satellite, mission, sensor, norad_id, direction, imaging_expected,
        roi_coverage_pct, roi_entry_utc, overpass_utc, roi_exit_utc, duration_s,
        roi_entry_local, overpass_local, roi_exit_local, timezone, utc_offset,
        nadir_offset_km, roi_side_of_track, edge_margin_km, view_angle_deg, max_elevation_deg,
        sun_elevation_deg, sat_altitude_km, tle_epoch_utc, tle_age_days, note``.

    Notes:
        - ``overpass_utc`` is the closest approach to the ROI anchor point
          (≈ scene centre time for a small ROI), refined below the time step.
        - ``edge_margin_km`` = half swath − nadir offset of the ROI anchor. Values
          within ~±5 km of zero are uncertain: the real swath edge differs slightly
          from the nominal width (it varies with altitude and detector layout).
        - SGP4 + fresh TLEs typically place a sun-synchronous EO satellite within a
          few km / ~1 s along-track; accuracy degrades with TLE age (see
          ``tle_age_days``) and around orbit-maintenance manoeuvres. Refresh TLEs
          before relying on predictions more than ~2 weeks ahead.
        - Geometry says *can* image. Whether data are actually *acquired* depends on
          the mission plan — use ``crosscheck_s2_acquisition_plan`` for Sentinel-2.
    """

    for label, value in [
        ("days", days),
        ("coarse_step_s", coarse_step_s),
        ("fine_step_s", fine_step_s),
    ]:
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be finite and positive.")
    if fine_step_s > coarse_step_s or days * 86400 < fine_step_s:
        raise ValueError(
            "Require fine_step_s <= coarse_step_s and a window of at least fine_step_s."
        )
    if not np.isfinite(min_roi_coverage_pct) or not 0 <= min_roi_coverage_pct <= 100:
        raise ValueError("min_roi_coverage_pct must be between 0 and 100.")
    if not np.isfinite(min_sun_elevation_deg) or not -90 <= min_sun_elevation_deg <= 90:
        raise ValueError("min_sun_elevation_deg must be between -90 and 90.")
    if (
        not isinstance(roi_gdf, gpd.GeoDataFrame)
        or len(roi_gdf) != 1
        or roi_gdf.crs is None
        or roi_gdf.crs.to_epsg() != 4326
    ):
        raise ValueError("roi_gdf must be a single WGS84 ROI; normalize it with load_roi().")
    roi = roi_gdf.geometry.iloc[0]
    if roi is None or roi.is_empty or not roi.is_valid:
        raise ValueError("ROI must be valid and nonempty; normalize it with load_roi().")
    anchor = _roi_anchor(roi)
    lat0, lon0 = anchor.y, anchor.x
    fwd, inv = _local_equal_area(roi)
    roi_eq = transform(fwd, roi)
    shapely.prepare(roi_eq)
    roi_area = roi_eq.area
    xs, ys = (
        np.asarray(roi_eq.exterior.coords).T
        if roi_eq.geom_type == "Polygon"
        else np.asarray(shapely.get_coordinates(roi_eq)).T
    )
    roi_radius_km = float(np.max(np.hypot(xs, ys))) / 1000.0 if len(xs) else 0.0

    tz_name = timezone_name or resolve_roi_timezone(roi_gdf, verbose=verbose)
    tz = ZoneInfo(tz_name)
    t0 = (
        _to_utc_timestamp(start_utc)
        if start_utc is not None
        else pd.Timestamp.now(tz="UTC").floor("min")
    )
    total_s = float(days) * 86400.0
    topos = wgs84.latlon(lat0, lon0)

    names = (
        _resolve_satellites(satellites)
        if satellites is not None
        else _resolve_satellites(list(tles))
    )
    rows = []
    for name in names:
        if name not in tles:
            raise KeyError(f"No TLE loaded for {name}; include it in fetch_tles().")
        cat = SATELLITE_CATALOGUE[name]
        sat = _build_satellite(name, tles[name])
        half_km = cat["swath_km"] / 2.0
        thresh_km = half_km + roi_radius_km + 150.0

        # ---- 1. coarse scan
        off = np.arange(0.0, total_s + coarse_step_s, coarse_step_s)
        clat, clon, _ = _propagate(sat, t0, off)
        _, _, d = GEOD.inv(np.full_like(clon, lon0), np.full_like(clat, lat0), clon, clat)
        idx = np.flatnonzero(d / 1000.0 < thresh_km)
        if idx.size == 0:
            if verbose:
                print(f"  {name:<12} 0 candidate passes")
            continue
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)

        kept = 0
        for run in runs:
            # ---- 2. fine refinement
            f_off = np.arange(
                max(off[run[0]] - 2 * coarse_step_s, 0.0),
                min(off[run[-1]] + 2 * coarse_step_s, total_s) + fine_step_s,
                fine_step_s,
            )
            lat, lon, alt = _propagate(sat, t0, f_off)
            az, _, _ = GEOD.inv(lon[:-1], lat[:-1], lon[1:], lat[1:])
            az = np.append(az, az[-1])
            llon, llat, _ = GEOD.fwd(lon, lat, az - 90.0, np.full_like(lon, half_km * 1000.0))
            rlon, rlat, _ = GEOD.fwd(lon, lat, az + 90.0, np.full_like(lon, half_km * 1000.0))
            lx, ly = fwd(llon, llat)
            rx, ry = fwd(rlon, rlat)
            nx, ny = fwd(lon, lat)

            # ---- 3. intersection
            # swath cells: quadrilateral between consecutive scan lines (gap-free, works for point ROIs)
            L_ = np.column_stack([lx, ly])
            R_ = np.column_stack([rx, ry])
            quads = shapely.polygons(np.stack([L_[:-1], L_[1:], R_[1:], R_[:-1], L_[:-1]], axis=1))
            hits = np.flatnonzero(shapely.intersects(quads, roi_eq))
            if hits.size == 0:
                continue
            i_in, i_out = hits[0], hits[-1] + 1
            d2 = nx**2 + ny**2
            i_ca = int(np.argmin(d2))
            frac = 0.0  # sub-step closest approach (parabolic fit on d²)
            if 0 < i_ca < len(d2) - 1:
                den = d2[i_ca - 1] - 2 * d2[i_ca] + d2[i_ca + 1]
                if den > 0:
                    frac = float(np.clip(0.5 * (d2[i_ca - 1] - d2[i_ca + 1]) / den, -0.5, 0.5))
            t_ca_off = float(f_off[i_ca]) + frac * fine_step_s
            if roi_eq.geom_type == "Point":  # a point is imaged at a single instant
                i_in = i_out = i_ca

            strip = Polygon(np.vstack([np.column_stack([lx, ly]), np.column_stack([rx, ry])[::-1]]))
            strip = shapely.make_valid(strip)
            if roi_area > 0:
                coverage = 100.0 * strip.intersection(roi_eq).area / roi_area
            else:
                coverage = 100.0
            if coverage < min_roi_coverage_pct:
                continue

            # ---- 4. attribution
            i_prev, i_next = max(i_ca - 1, 0), min(i_ca + 1, len(lat) - 1)
            direction = "Ascending" if lat[i_next] > lat[i_prev] else "Descending"
            az_to_roi, _, d_ca = GEOD.inv(lon[i_ca], lat[i_ca], lon0, lat0)
            d_ca_km = d_ca / 1000.0
            gamma = d_ca_km / EARTH_RADIUS_KM
            view = math.degrees(
                math.atan2(
                    EARTH_RADIUS_KM * math.sin(gamma),
                    EARTH_RADIUS_KM + alt[i_ca] - EARTH_RADIUS_KM * math.cos(gamma),
                )
            )
            side = "nadir" if d_ca_km < 1.0 else ("E" if 0.0 < (az_to_roi % 360.0) < 180.0 else "W")

            t_in = t0 + pd.Timedelta(seconds=float(f_off[i_in]))
            t_ca = t0 + pd.Timedelta(milliseconds=int(round(t_ca_off * 10)) * 100)
            t_out = t0 + pd.Timedelta(seconds=float(f_off[i_out]))
            sun_el = float(solar_elevation_deg([t_ca], lat0, lon0)[0])
            t_sf = _ts().from_datetime(t_ca.to_pydatetime())
            max_el = float((sat - topos).at(t_sf).altaz()[0].degrees)
            lo, hi = cat["lat_limits"]
            expected = bool(
                direction == "Descending" and sun_el >= min_sun_elevation_deg and lo <= lat0 <= hi
            )
            if daytime_descending_only and not expected:
                continue

            # display strip: ROI crossing ±~150 km along track
            pad = int(round(20.0 / fine_step_s))
            j0, j1 = max(i_in - pad, 0), min(i_out + pad, len(lat) - 1)
            disp = Polygon(
                np.vstack(
                    [
                        np.column_stack([lx[j0 : j1 + 1], ly[j0 : j1 + 1]]),
                        np.column_stack([rx[j0 : j1 + 1], ry[j0 : j1 + 1]])[::-1],
                    ]
                )
            )
            disp = transform(inv, shapely.make_valid(disp))

            epoch = tles[name]["epoch_utc"]
            loc_ca = t_ca.tz_convert(tz)
            off_s = loc_ca.utcoffset().total_seconds()
            rows.append(
                {
                    "satellite": name,
                    "mission": cat["mission"],
                    "sensor": cat["sensor"],
                    "norad_id": cat["norad_id"],
                    "direction": direction,
                    "imaging_expected": expected,
                    "roi_coverage_pct": round(coverage, 1),
                    "roi_entry_utc": t_in,
                    "overpass_utc": t_ca,
                    "roi_exit_utc": t_out,
                    "duration_s": round(float(f_off[i_out] - f_off[i_in]), 1),
                    "roi_entry_local": t_in.tz_convert(tz),
                    "overpass_local": loc_ca,
                    "roi_exit_local": t_out.tz_convert(tz),
                    "timezone": tz_name,
                    "utc_offset": f"{'+' if off_s >= 0 else '-'}{int(abs(off_s) // 3600):02d}:{int(abs(off_s) % 3600 // 60):02d}",
                    "nadir_offset_km": round(d_ca_km, 1),
                    "roi_side_of_track": side,
                    "edge_margin_km": round(half_km - d_ca_km, 1),
                    "view_angle_deg": round(view, 2),
                    "max_elevation_deg": round(max_el, 1),
                    "sun_elevation_deg": round(sun_el, 1),
                    "sat_altitude_km": round(float(alt[i_ca]), 1),
                    "tle_epoch_utc": epoch,
                    "tle_age_days": round((t_ca - epoch).total_seconds() / 86400.0, 1),
                    "note": cat["note"],
                    "geometry": disp,
                }
            )
            kept += 1
        if verbose:
            print(f"  {name:<12} {kept} overpass(es)")

    cols = [
        "satellite",
        "mission",
        "sensor",
        "norad_id",
        "direction",
        "imaging_expected",
        "roi_coverage_pct",
        "roi_entry_utc",
        "overpass_utc",
        "roi_exit_utc",
        "duration_s",
        "roi_entry_local",
        "overpass_local",
        "roi_exit_local",
        "timezone",
        "utc_offset",
        "nadir_offset_km",
        "roi_side_of_track",
        "edge_margin_km",
        "view_angle_deg",
        "max_elevation_deg",
        "sun_elevation_deg",
        "sat_altitude_km",
        "tle_epoch_utc",
        "tle_age_days",
        "note",
        "geometry",
    ]
    gdf = gpd.GeoDataFrame(rows, columns=cols, geometry="geometry", crs="EPSG:4326")
    if gdf.empty:
        for column in ["roi_entry_utc", "overpass_utc", "roi_exit_utc", "tle_epoch_utc"]:
            gdf[column] = pd.Series(dtype="datetime64[ns, UTC]")
        for column in ["roi_entry_local", "overpass_local", "roi_exit_local"]:
            gdf[column] = pd.Series(dtype=pd.DatetimeTZDtype(tz=tz_name, unit="ns"))
        for column in [
            "roi_coverage_pct",
            "duration_s",
            "nadir_offset_km",
            "edge_margin_km",
            "view_angle_deg",
            "max_elevation_deg",
            "sun_elevation_deg",
            "sat_altitude_km",
            "tle_age_days",
        ]:
            gdf[column] = pd.Series(dtype="float64")
        gdf["imaging_expected"] = pd.Series(dtype="bool")
        gdf["norad_id"] = pd.Series(dtype="int64")
    gdf = gdf.sort_values("overpass_utc").reset_index(drop=True)
    gdf.attrs.update(
        {
            "roi_name": roi_gdf["name"].iloc[0] if "name" in roi_gdf else "ROI",
            "timezone": tz_name,
            "window_start_utc": t0,
            "window_end_utc": t0 + pd.Timedelta(seconds=total_s),
        }
    )
    if verbose:
        print(
            f"\n🛰️  {len(gdf)} overpass(es) between {t0:%Y-%m-%d %H:%M} and "
            f"{gdf.attrs['window_end_utc']:%Y-%m-%d %H:%M} UTC"
        )
    return gdf
