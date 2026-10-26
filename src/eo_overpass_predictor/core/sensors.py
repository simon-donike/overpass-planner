"""Sensor geometry configuration and Sentinel-1 right-looking footprints.

The optical predictor uses a centred, nadir swath.  Sentinel-1 is a
right-looking SAR, so its footprint is represented by two look-angle edges
and an Earth-fixed ray/ellipsoid intersection.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from pyproj import Transformer

from .orbits import GEOD

WGS84_A_KM = 6378.137
WGS84_B_KM = 6356.752314245

_ECEF_TO_GEODETIC = Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True)
_GEODETIC_TO_ECEF = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)

# ESA Sentinel-1 Product Definition, tables 3-5 and 3-7.  The endpoints are
# the outer near/far edges of the operational IW/EW mode envelopes.
_S1_LOOK_LIMITS = {
    "IW": ((698.0, 27.53, 40.40), (726.0, 26.00, 38.88)),
    "EW": ((698.0, 17.94, 41.20), (726.0, 16.36, 39.62)),
}


@dataclass(frozen=True)
class SensorModel:
    """Geometry and eligibility policies independent of spacecraft identity."""

    geometry: str
    supported_modes: tuple[str, ...] = ()
    optical: bool = True

    def search_radius_km(self, swath_km: float) -> float:
        # 800 km conservatively encloses the nominal S1 far edge; the predictor
        # adds its existing ROI radius and along-track sampling allowance.
        return swath_km / 2 if self.optical else 800.0

    def imaging_eligible(self, direction, sun_elevation, minimum_sun, latitude, limits) -> bool:
        return bool(
            limits[0] <= latitude <= limits[1]
            and (not self.optical or (direction == "Descending" and sun_elevation >= minimum_sun))
        )

    def scan_lines(
        self,
        lon,
        lat,
        heading,
        swath_km,
        *,
        mode="IW",
        positions=None,
        velocities=None,
        altitudes=None,
    ):
        """Return near/far lon/lat arrays and optional off-nadir angle arrays."""
        if self.optical:
            # Preserve Julian Manning's optical formulas and vector operations.
            half_km = swath_km / 2
            llon, llat, _ = GEOD.fwd(lon, lat, heading - 90.0, np.full_like(lon, half_km * 1000.0))
            rlon, rlat, _ = GEOD.fwd(lon, lat, heading + 90.0, np.full_like(lon, half_km * 1000.0))
            return llon, llat, rlon, rlat, None, None
        if positions is None or velocities is None or altitudes is None:
            raise ValueError("SAR scan lines require Earth-fixed states and altitude.")
        edges = [
            sentinel1_footprint_edges(position, velocity, altitude, mode)
            for position, velocity, altitude in zip(positions, velocities, altitudes, strict=True)
        ]
        return tuple(
            np.asarray([edge[part][coordinate] for edge in edges])
            for part, coordinate in ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1))
        )


SENSOR_MODELS = {
    "optical_nadir": SensorModel("optical_nadir"),
    "right_looking_sar": SensorModel("right_looking_sar", ("IW", "EW"), optical=False),
}


def sensor_model(satellite: Mapping[str, Any]) -> SensorModel:
    """Resolve a registered instrument model from catalogue capabilities."""
    return SENSOR_MODELS[satellite.get("geometry", "optical_nadir")]


def sentinel1_mode_config(mode: str = "IW") -> dict[str, Any]:
    """Return serialisable nominal geometry for a Sentinel-1 acquisition mode."""
    key = str(mode).upper().strip()
    if key not in _S1_LOOK_LIMITS:
        raise ValueError("Sentinel-1 mode must be IW or EW.")
    return {
        "mode": key,
        "look_side": "right",
        "altitude_limits_km": [698.0, 726.0],
        "look_angle_limits_deg": {
            "near": [x[1] for x in _S1_LOOK_LIMITS[key]],
            "far": [x[2] for x in _S1_LOOK_LIMITS[key]],
        },
        "look_angle_limits_by_altitude_deg": [list(x[1:]) for x in _S1_LOOK_LIMITS[key]],
        "source": "ESA Sentinel-1 Product Definition, tables 3-5 and 3-7",
    }


def sensor_config(name: str, mode: str = "IW") -> dict[str, Any]:
    """Return the shared tracker/presentation geometry contract for a sensor."""
    key = str(name).upper().replace("_", " ").strip()
    if key in {"SENTINEL-1", "SENTINEL 1", "S1"}:
        return sentinel1_mode_config(mode)
    return {"mode": "nadir", "look_side": "centred"}


def resolve_s1_mode(passes: Any = None, requested: str | None = None) -> str:
    """Resolve one consistent mode for predictions and their live presentation."""
    modes: set[str] = set()
    if passes is not None:
        stored = passes.attrs.get("s1_mode")
        if stored is not None:
            modes.add(str(stored).strip().upper())
        for column in ("mode", "s1_mode"):
            if column in passes:
                values = passes[column]
                if "mission" in passes:
                    values = values[passes["mission"] == "Sentinel-1"]
                modes.update(str(value).strip().upper() for value in values.dropna())
    if len(modes) > 1:
        raise ValueError("Supplied pass predictions contain conflicting Sentinel-1 modes.")
    selected = str(requested).strip().upper() if requested is not None else next(iter(modes), "IW")
    sentinel1_mode_config(selected)  # Validate metadata as well as explicit requests.
    if modes and selected not in modes:
        raise ValueError("s1_mode conflicts with the supplied pass predictions.")
    return selected


def sentinel1_look_angles(altitude_km: float, mode: str = "IW") -> tuple[float, float]:
    """Interpolate the ESA near/far off-nadir envelope at spacecraft altitude."""
    limits = _S1_LOOK_LIMITS[str(mode).upper().strip()]
    h = float(np.clip(altitude_km, limits[0][0], limits[1][0]))
    f = (h - limits[0][0]) / (limits[1][0] - limits[0][0])
    return tuple((1.0 - f) * np.asarray(limits[0][1:]) + f * np.asarray(limits[1][1:]))


def _ray_to_ellipsoid(position_km: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Return the forward intersection of an ECEF ray with WGS84."""
    p = np.asarray(position_km, dtype=float)
    d = np.asarray(direction, dtype=float)
    a2, b2 = WGS84_A_KM**2, WGS84_B_KM**2
    q = np.array([1.0 / a2, 1.0 / a2, 1.0 / b2])
    A = float(np.sum(q * d * d))
    B = float(2.0 * np.sum(q * p * d))
    C = float(np.sum(q * p * p) - 1.0)
    disc = B * B - 4.0 * A * C
    if disc < 0:
        raise ValueError("Look ray does not intersect WGS84 ellipsoid.")
    roots = ((-B - math.sqrt(disc)) / (2.0 * A), (-B + math.sqrt(disc)) / (2.0 * A))
    positive = [r for r in roots if r >= 0]
    if not positive:
        raise ValueError("Look ray has no forward WGS84 intersection.")
    return p + min(positive) * d


def sentinel1_footprint_edges(
    position_km: np.ndarray,
    velocity_km_s: np.ndarray,
    altitude_km: float,
    mode: str = "IW",
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Return near/far (lon, lat) and their look angles for a right-looking SAR."""
    p = np.asarray(position_km, dtype=float)
    v = np.asarray(velocity_km_s, dtype=float)
    track = v / np.linalg.norm(v)
    # Zero-Doppler look vectors lie in the plane perpendicular to the
    # Earth-fixed velocity. Project the geocentric nadir into that plane.
    nadir = -p / np.linalg.norm(p)
    nadir -= np.dot(nadir, track) * track
    nadir /= np.linalg.norm(nadir)
    right = np.cross(nadir, track)
    right /= np.linalg.norm(right)
    near, far = sentinel1_look_angles(altitude_km, mode)
    points = []
    incidences = []
    for angle in (near, far):
        rad = math.radians(angle)
        ray = math.cos(rad) * nadir + math.sin(rad) * right
        x, y, z = _ray_to_ellipsoid(p, ray)
        lon, lat, _ = _ECEF_TO_GEODETIC.transform(x * 1000.0, y * 1000.0, z * 1000.0)
        points.append((float(lon), float(lat)))
        normal = np.array([x / WGS84_A_KM**2, y / WGS84_A_KM**2, z / WGS84_B_KM**2])
        normal /= np.linalg.norm(normal)
        incidences.append(math.degrees(math.acos(float(np.clip(np.dot(-ray, normal), -1.0, 1.0)))))
    return points[0], points[1], (near, far), tuple(incidences)


def incidence_angle_at_target(position_km: np.ndarray, lon: float, lat: float) -> float:
    """Incidence angle at a WGS84 target for an ECEF satellite position."""
    x, y, z = _GEODETIC_TO_ECEF.transform(lon, lat, 0.0)
    target = np.array([x, y, z], dtype=float) / 1000.0
    normal = np.array(
        [target[0] / WGS84_A_KM**2, target[1] / WGS84_A_KM**2, target[2] / WGS84_B_KM**2]
    )
    normal /= np.linalg.norm(normal)
    los = np.asarray(position_km, dtype=float) - target
    los /= np.linalg.norm(los)
    return math.degrees(math.acos(float(np.clip(np.dot(los, normal), -1.0, 1.0))))
