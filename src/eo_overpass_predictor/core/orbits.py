"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pyproj import Geod
from skyfield.api import EarthSatellite, load, wgs84

from ..types import DateInput, SatelliteInfo, SatelliteSelection, TLERecord

EARTH_RADIUS_KM = 6371.0088
GEOD = Geod(ellps="WGS84")

# Mission catalogue. Add rows to track other nadir-looking optical sensors
# (swath_km is the full cross-track width of the instrument footprint).
SATELLITE_CATALOGUE: dict[str, SatelliteInfo] = {
    "LANDSAT 8": {
        "norad_id": 39084,
        "mission": "Landsat",
        "sensor": "OLI/TIRS",
        "swath_km": 185.0,
        "lat_limits": (-81.8, 81.8),
        "colour": "#F28E2B",
        "note": "USGS acquires ~all daytime land and near-coastal WRS-2 scenes.",
    },
    "LANDSAT 9": {
        "norad_id": 49260,
        "mission": "Landsat",
        "sensor": "OLI-2/TIRS-2",
        "swath_km": 185.0,
        "lat_limits": (-81.8, 81.8),
        "colour": "#E15759",
        "note": "USGS acquires ~all daytime land and near-coastal WRS-2 scenes.",
    },
    "SENTINEL-2A": {
        "norad_id": 40697,
        "mission": "Sentinel-2",
        "sensor": "MSI",
        "swath_km": 290.0,
        "lat_limits": (-56.0, 82.8),
        "colour": "#76B7B2",
        "note": "Extension campaign (to 31 Dec 2026): selected regions only - check the ESA plan.",
    },
    "SENTINEL-2B": {
        "norad_id": 42063,
        "mission": "Sentinel-2",
        "sensor": "MSI",
        "swath_km": 290.0,
        "lat_limits": (-56.0, 82.8),
        "colour": "#4E79A7",
        "note": "Nominal constellation (with S2C). Open ocean is not acquired.",
    },
    "SENTINEL-2C": {
        "norad_id": 60989,
        "mission": "Sentinel-2",
        "sensor": "MSI",
        "swath_km": 290.0,
        "lat_limits": (-56.0, 82.8),
        "colour": "#59A14F",
        "note": "Nominal constellation (with S2B). Open ocean is not acquired.",
    },
}

_TIMESCALE = None


def _ts():
    """Return a cached Skyfield timescale built from bundled data (no network needed)."""
    global _TIMESCALE
    if _TIMESCALE is None:
        _TIMESCALE = load.timescale(builtin=True)
    return _TIMESCALE


def _to_utc_timestamp(value: DateInput) -> pd.Timestamp:
    """Coerce str / datetime / Timestamp (naive = UTC) to a tz-aware UTC Timestamp."""
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError("Date must be a finite timestamp, not NaT.")
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _resolve_satellites(satellites: SatelliteSelection) -> list[str]:
    """Expand names, NORAD IDs and mission groups, keeping each satellite once.

    S2 selects Sentinel-2A/B/C; LS selects Landsat 8/9. Strings may contain
    comma-separated selections. Full mission names are also accepted.
    """
    if satellites is None:
        return list(SATELLITE_CATALOGUE)
    if isinstance(satellites, (str, int, np.integer)):
        satellites = [satellites]
    satellites = list(satellites)
    if not satellites:
        raise ValueError("Select at least one satellite.")
    by_id = {v["norad_id"]: k for k, v in SATELLITE_CATALOGUE.items()}
    groups = {
        "S2": "Sentinel-2",
        "SENTINEL-2": "Sentinel-2",
        "LS": "Landsat",
        "LANDSAT": "Landsat",
    }
    tokens = [
        token
        for selection in satellites
        for token in (selection.split(",") if isinstance(selection, str) else [selection])
    ]
    out = []
    for s in tokens:
        if isinstance(s, str) and s.strip().isdigit():
            s = int(s.strip())
        if isinstance(s, (int, np.integer)) and int(s) in by_id:
            out.append(by_id[int(s)])
            continue
        key = str(s).upper().replace("_", " ").strip()
        if not key:
            raise ValueError("Satellite selection contains an empty name.")
        if key in groups:
            out.extend(
                name
                for name, entry in SATELLITE_CATALOGUE.items()
                if entry["mission"] == groups[key]
            )
            continue
        key = {
            "L8": "LANDSAT 8",
            "L9": "LANDSAT 9",
            "S2A": "SENTINEL-2A",
            "S2B": "SENTINEL-2B",
            "S2C": "SENTINEL-2C",
        }.get(key, key)
        if key not in SATELLITE_CATALOGUE:
            raise ValueError(
                f"Unknown satellite '{s}'. Known: {list(SATELLITE_CATALOGUE)}. "
                "Use S2 for all Sentinel-2 satellites or LS for all Landsat satellites."
            )
        out.append(key)
    return list(dict.fromkeys(out))


def _solar_ra_dec_gmst(times_utc):
    """Return (right ascension °, declination °, GMST °) for an array of UTC datetimes."""
    t = pd.DatetimeIndex(pd.to_datetime(times_utc, utc=True))
    # resolution-agnostic (pandas ≥2 may store s/ms/us/ns): seconds since the Unix epoch
    secs = (t - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds().to_numpy()
    n = secs / 86400.0 + 2440587.5 - 2451545.0
    L = np.deg2rad((280.460 + 0.9856474 * n) % 360)
    g = np.deg2rad((357.528 + 0.9856003 * n) % 360)
    lam = L + np.deg2rad(1.915) * np.sin(g) + np.deg2rad(0.020) * np.sin(2 * g)
    eps = np.deg2rad(23.439 - 0.0000004 * n)
    ra = np.rad2deg(np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam)))
    dec = np.rad2deg(np.arcsin(np.sin(eps) * np.sin(lam)))
    gmst = (280.46061837 + 360.98564736629 * n) % 360
    return np.asarray(ra), np.asarray(dec), np.asarray(gmst)


def solar_elevation_deg(times_utc, lat: float, lon: float) -> np.ndarray:
    """Solar elevation angle (degrees, geometric) at (lat, lon) for one or many UTC times."""
    ra, dec, gmst = _solar_ra_dec_gmst(np.atleast_1d(times_utc))
    H = np.deg2rad(gmst + lon - ra)
    la, de = np.deg2rad(lat), np.deg2rad(dec)
    return np.rad2deg(np.arcsin(np.sin(la) * np.sin(de) + np.cos(la) * np.cos(de) * np.cos(H)))


def subsolar_point(when_utc: DateInput) -> tuple[float, float]:
    """(lat, lon) of the sub-solar point at a UTC time."""
    ra, dec, gmst = _solar_ra_dec_gmst([when_utc])
    lon = ((ra[0] - gmst[0] + 180) % 360) - 180
    return float(dec[0]), float(lon)


def _build_satellite(name: str, tle: TLERecord) -> EarthSatellite:
    return EarthSatellite(tle["line1"], tle["line2"], name, _ts())


def _propagate(sat: EarthSatellite, t0: pd.Timestamp, offsets_s: np.ndarray):
    """Vectorised SGP4 propagation → geodetic sub-satellite lat, lon (deg) and altitude (km)."""
    t = _ts().utc(
        t0.year, t0.month, t0.day, t0.hour, t0.minute, t0.second + t0.microsecond / 1e6 + offsets_s
    )
    geo = sat.at(t)
    sp = wgs84.subpoint_of(geo)
    return sp.latitude.degrees, sp.longitude.degrees, wgs84.height_of(geo).km


def satellite_positions(
    tles: dict[str, TLERecord],
    when_utc: DateInput | None = None,
    satellites: SatelliteSelection = None,
) -> pd.DataFrame:
    """Instantaneous sub-satellite position, altitude and ground speed for each satellite."""
    t = _to_utc_timestamp(when_utc) if when_utc is not None else pd.Timestamp.now(tz="UTC")
    names = _resolve_satellites(satellites) if satellites is not None else list(tles)
    rows = []
    for name in names:
        sat = _build_satellite(name, tles[name])
        lat, lon, alt = _propagate(sat, t, np.array([0.0, 1.0]))
        _, _, d = GEOD.inv(lon[0], lat[0], lon[1], lat[1])
        rows.append(
            {
                "satellite": name,
                "lat": float(lat[0]),
                "lon": float(lon[0]),
                "alt_km": float(alt[0]),
                "ground_speed_kms": d / 1000.0,
                "direction": "Ascending" if lat[1] > lat[0] else "Descending",
            }
        )
    return pd.DataFrame(rows)
