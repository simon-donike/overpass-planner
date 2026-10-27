"""Preregistered sampling, historical input selection and reproducible artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely import make_valid, union_all
from shapely.geometry import Point, Polygon
from skyfield.api import EarthSatellite

from eo_overpass_predictor import SATELLITE_CATALOGUE, __version__, parse_tle_text
from eo_overpass_predictor.core.orbits import _ts

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "1.0"
MAX_EVENT_SPAN_S = 2400.0


def utc(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise ValueError(f"An explicit UTC offset is required: {value!r}")
    return result.tz_convert("UTC")


def iso(value: str | pd.Timestamp) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


@dataclass
class Study:
    # No dates are inferred from the wall clock, and no live TLE endpoint is used.
    origins: list[str]
    satellites: list[str]
    seed: int = 20261026
    horizon_days: int = 60
    latitude_cells: int = 6
    longitude_cells: int = 12
    sites_per_cell: int = 2
    max_initial_tle_age_days: float = 3.0
    element_cutoff_lags_days: list[float] = field(default_factory=lambda: [0.0])
    horizon_edges_days: list[int] = field(default_factory=lambda: [0, 1, 3, 7, 14, 30, 45, 60])
    match_gate_s: float = 1800.0
    event_merge_gap_s: float = 120.0
    # An instant STAC datetime is NOT necessarily the sensing time at the ROI.
    timestamp_padding_s: dict[str, float] = field(
        default_factory=lambda: {"Landsat": 30.0, "Sentinel-2": 300.0, "Sentinel-1": 60.0}
    )
    maturity_days: int = 30
    holdout_fraction: float = 0.3
    bootstrap_replicates: int = 1000
    interval_level: float = 0.9
    min_calibration_origins: int = 10
    min_holdout_origins: int = 6
    min_spatial_blocks: int = 20
    min_matches: int = 200
    max_unknown_fraction: float = 0.05
    coverage_tolerance: float = 0.05
    request_interval_s: float = 1.0
    probe_assets: bool = True
    skip_repeated_access_denials: bool = True
    s1_mode: str = "IW"

    def validate(self) -> Study:
        if (
            not self.origins
            or not self.satellites
            or len(set(self.satellites)) != len(self.satellites)
        ):
            raise ValueError("Require origins and distinct canonical satellite names.")
        if set(self.satellites) - SATELLITE_CATALOGUE.keys():
            raise ValueError("satellites must use names from SATELLITE_CATALOGUE.")
        for name in (
            "horizon_days",
            "latitude_cells",
            "longitude_cells",
            "sites_per_cell",
            "bootstrap_replicates",
            "min_calibration_origins",
            "min_holdout_origins",
            "min_spatial_blocks",
            "min_matches",
            "maturity_days",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if self.horizon_days > 60 or self.bootstrap_replicates < 100:
            raise ValueError("Require horizon <= 60 days and >= 100 bootstrap replicates.")
        for name in ("match_gate_s", "event_merge_gap_s", "max_initial_tle_age_days"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive.")
        lags = self.element_cutoff_lags_days
        if (
            not lags
            or lags != sorted(set(lags))
            or any(
                not math.isfinite(lag) or lag < 0 or lag >= self.max_initial_tle_age_days
                for lag in lags
            )
        ):
            raise ValueError(
                "Element cutoff lags must increase from nonnegative values below maximum initial age."
            )
        if not 0 <= self.request_interval_s <= 60:
            raise ValueError("request_interval_s must be between 0 and 60.")
        if not 0 < self.match_gate_s < 2400 or self.event_merge_gap_s >= self.match_gate_s:
            raise ValueError("Use a matching gate below 40 minutes and a smaller merge gap.")
        for mission in {SATELLITE_CATALOGUE[s]["mission"] for s in self.satellites}:
            padding = self.timestamp_padding_s.get(mission, -1)
            if not math.isfinite(padding) or not 0 <= padding < self.match_gate_s:
                raise ValueError(f"Require timestamp padding below the gate for {mission}.")
        for name in ("holdout_fraction", "interval_level"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be between 0 and 1.")
        for name in ("max_unknown_fraction", "coverage_tolerance"):
            if not 0 <= getattr(self, name) < 1:
                raise ValueError(f"{name} must be in [0, 1).")
        edges = self.horizon_edges_days
        if edges != sorted(set(edges)) or edges[0] != 0 or edges[-1] != self.horizon_days:
            raise ValueError("Horizon edges must increase from zero through horizon_days.")
        self.origins = sorted((iso(o) for o in self.origins), key=utc)
        if len(self.origins) < 2:
            raise ValueError("Use at least two separated origins for calibration and holdout.")
        separation = pd.Timedelta(
            days=self.horizon_days, seconds=2 * (self.match_gate_s + MAX_EVENT_SPAN_S)
        )
        if any(utc(b) - utc(a) <= separation for a, b in zip(self.origins, self.origins[1:])):
            raise ValueError("Origins must have non-overlapping windows including matching guards.")
        if self.s1_mode not in {"IW", "EW"}:
            raise ValueError("s1_mode must be IW or EW.")
        return self


def horizon_bin(study: Study, lead_days: float) -> str:
    for low, high in zip(study.horizon_edges_days, study.horizon_edges_days[1:]):
        if low <= lead_days < high or lead_days == high == study.horizon_days:
            return f"{low}-{high}d"
    raise ValueError(f"Lead time outside the study: {lead_days}")


def sample_sites(study: Study) -> list[dict]:
    """Equal-area strata: uniform longitude and uniform sine(latitude)."""
    rng = np.random.default_rng(study.seed)
    mask = json.loads(
        files("eo_overpass_predictor.resources").joinpath("world_land_50m.json").read_text()
    )
    land = union_all(
        [
            make_valid(Polygon(np.asarray(ring).reshape(-1, 2) * mask["scale"]))
            for ring in mask["rings"]
        ]
    )
    sites = []
    for a in range(study.latitude_cells):
        for b in range(study.longitude_cells):
            for j in range(study.sites_per_cell):
                lon = -180 + 360 * (b + rng.random()) / study.longitude_cells
                sine_lat = -1 + 2 * (a + rng.random()) / study.latitude_cells
                lat = math.degrees(math.asin(sine_lat))
                sites.append(
                    {
                        "site_id": f"s{a:02}-{b:02}-{j:02}",
                        "spatial_block": f"{a:02}-{b:02}",
                        "longitude": lon,
                        "latitude": lat,
                        # The bundled drawing rings are already unwrapped and
                        # may place Eurasia/Africa one full turn west of [-180,180].
                        "surface": "land"
                        if any(land.covers(Point(lon + shift, lat)) for shift in (0, -360, 360))
                        else "ocean",
                        "latitude_band": "tropical"
                        if abs(lat) < 23.5
                        else ("temperate" if abs(lat) < 60 else "polar"),
                        "sampling_weight": 1.0,
                    }
                )
    return sites


def load_archive(path: Path) -> list[dict]:
    """Require independently evidenced publication times, not TLE epoch alone."""
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        name = record["satellite"]
        if name not in SATELLITE_CATALOGUE:
            raise ValueError(f"Unsupported satellite in archive: {name}")
        for key in ("source", "publication_evidence"):
            if not isinstance(record[key], str) or not record[key].strip():
                raise ValueError(f"Missing archive {key}.")
        published, retrieved = utc(record["published_at_utc"]), utc(record["retrieved_at_utc"])
        if published > retrieved:
            raise ValueError("Publication cannot follow retrieval.")
        norad = SATELLITE_CATALOGUE[name]["norad_id"]
        parsed = parse_tle_text(record["line1"] + "\n" + record["line2"])
        if set(parsed) != {norad}:
            raise ValueError("Archive satellite and TLE identifiers disagree.")
        tle = parsed[norad]
        epoch = pd.Timestamp(
            EarthSatellite(tle["line1"], tle["line2"], name, _ts()).epoch.utc_datetime()
        )
        normalized = {
            **record,
            **tle,
            "satellite": name,
            "norad_id": norad,
            "epoch_utc": iso(epoch),
            "published_at_utc": iso(published),
            "retrieved_at_utc": iso(retrieved),
            "orbital_input_id": digest([norad, tle["line1"], tle["line2"]]),
        }
        normalized["element_id"] = digest(normalized)
        records.append(normalized)
    if not records:
        raise ValueError("Historical archive is empty.")
    return records


def select_element(
    records: list[dict],
    satellite: str,
    origin: str,
    max_age: float,
    available_by: str | None = None,
) -> dict | None:
    issue = utc(origin)
    cutoff = utc(available_by) if available_by else issue
    candidates = [
        r
        for r in records
        if r["satellite"] == satellite
        and utc(r["published_at_utc"]) <= cutoff
        and utc(r["epoch_utc"]) <= cutoff
        and pd.Timedelta(0) <= issue - utc(r["epoch_utc"]) <= pd.Timedelta(days=max_age)
    ]
    return (
        max(
            candidates,
            key=lambda r: (utc(r["epoch_utc"]), utc(r["published_at_utc"]), r["element_id"]),
        )
        if candidates
        else None
    )


def code_fingerprint() -> str:
    paths = sorted((ROOT / "src/eo_overpass_predictor").rglob("*.py"))
    paths += sorted((ROOT / "benchmark").glob("*.py"))
    paths += [
        ROOT / "uv.lock",
        ROOT / "pyproject.toml",
        ROOT / "src/eo_overpass_predictor/resources/world_land_50m.json",
    ]
    return digest(
        {
            "files": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
            },
            "environment": runtime_environment(),
        }
    )


def runtime_environment() -> dict:
    return {
        "python": platform.python_version(),
        **{
            name: version(name)
            for name in [
                "numpy",
                "pandas",
                "geopandas",
                "shapely",
                "pyproj",
                "skyfield",
                "sgp4",
                "requests",
            ]
        },
    }


def plan(config: Path, archive: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Use a new output directory for each preregistered study.")
    study = Study(**read_json(config)).validate()
    elements = load_archive(archive)
    snapshots = []
    for origin in study.origins:
        for satellite in study.satellites:
            for lag in study.element_cutoff_lags_days:
                cutoff = iso(utc(origin) - pd.Timedelta(days=lag))
                element = select_element(
                    elements, satellite, origin, study.max_initial_tle_age_days, cutoff
                )
                snapshots.append(
                    {
                        "origin": origin,
                        "satellite": satellite,
                        "element": element,
                        "element_cutoff_lag_days": lag,
                        "available_by_utc": cutoff,
                    }
                )
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "study": asdict(study),
        "sites": sample_sites(study),
        "snapshots": snapshots,
        "package_version": __version__,
        "git_revision": revision,
        "code_fingerprint": code_fingerprint(),
        "environment": runtime_environment(),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "scientific_attribution": "Julian Manning",
    }
    manifest["study_id"] = digest(manifest)
    write_json(output / "manifest.json", manifest)
    return manifest


def load_run(directory: Path) -> tuple[dict, Study]:
    manifest = read_json(directory / "manifest.json")
    original = {k: v for k, v in manifest.items() if k != "study_id"}
    if digest(original) != manifest["study_id"]:
        raise ValueError("Manifest changed after preregistration.")
    if manifest["code_fingerprint"] != code_fingerprint():
        raise ValueError(
            "Code/dependencies changed; create a new study instead of mixing versions."
        )
    return manifest, Study(**manifest["study"]).validate()


def cases(manifest: dict):
    for snapshot in manifest["snapshots"]:
        for site in manifest["sites"]:
            case = {**site, **snapshot}
            case["case_id"] = digest(
                [manifest["study_id"], snapshot["origin"], snapshot["satellite"], site["site_id"]]
                + (
                    [snapshot["element_cutoff_lag_days"]]
                    if snapshot.get("element_cutoff_lag_days")
                    else []
                )
            )[:24]
            yield case
