"""Public API for swath-aware prediction, reporting and optional presentation.

Typical use: load_roi -> fetch_tles -> predict_overpasses -> explicit export.
Prediction does not download data or write files. Names exported here and at the
package root are public; underscored helpers and submodule organization are private.
"""

import pandas as pd

from .core.geometry import resolve_roi_timezone as resolve_roi_timezone
from .core.orbits import SATELLITE_CATALOGUE as SATELLITE_CATALOGUE
from .core.orbits import satellite_positions as satellite_positions
from .core.orbits import solar_elevation_deg as solar_elevation_deg
from .core.orbits import subsolar_point as subsolar_point
from .core.plans import crosscheck_s2_acquisition_plan as crosscheck_s2_acquisition_plan
from .core.prediction import predict_overpasses as predict_overpasses
from .core.reporting import next_overpasses as next_overpasses
from .core.reporting import process_timer as process_timer
from .core.reporting import summarise_overpasses as summarise_overpasses
from .io.export import export_live_tracker_html as export_live_tracker_html
from .io.export import export_overpass_csv as export_overpass_csv
from .io.export import export_overpass_footprints as export_overpass_footprints
from .io.plans import fetch_s2_acquisition_plans as fetch_s2_acquisition_plans
from .io.plans import load_s2_acquisition_plans as load_s2_acquisition_plans
from .io.roi import load_roi as load_roi
from .io.tles import fetch_tles as fetch_tles
from .io.tles import normalise_tle_line as normalise_tle_line
from .io.tles import parse_tle_text as parse_tle_text
from .tracking import LiveTracker as LiveTracker
from .tracking import live_tracker as live_tracker
from .types import RawTLE as RawTLE
from .types import SatelliteInfo as SatelliteInfo
from .types import TLERecord as TLERecord
from .visualization import plot_overpass_timeline as plot_overpass_timeline
from .visualization import visualise_overpass_footprints as visualise_overpass_footprints
from .visualization import visualise_roi_on_map as visualise_roi_on_map

__all__ = [
    "SATELLITE_CATALOGUE",
    "SatelliteInfo",
    "RawTLE",
    "TLERecord",
    "list_satellites",
    "load_roi",
    "resolve_roi_timezone",
    "fetch_tles",
    "normalise_tle_line",
    "parse_tle_text",
    "predict_overpasses",
    "summarise_overpasses",
    "next_overpasses",
    "solar_elevation_deg",
    "subsolar_point",
    "satellite_positions",
    "fetch_s2_acquisition_plans",
    "load_s2_acquisition_plans",
    "crosscheck_s2_acquisition_plan",
    "export_overpass_csv",
    "export_overpass_footprints",
    "export_live_tracker_html",
    "visualise_roi_on_map",
    "visualise_overpass_footprints",
    "plot_overpass_timeline",
    "LiveTracker",
    "live_tracker",
    "process_timer",
]


def list_satellites(mission: str | None = None) -> pd.DataFrame:
    """Return supported instruments, short aliases and NORAD identifiers.

    ``mission`` optionally selects ``Landsat`` or ``Sentinel-2`` (case insensitive).
    The returned table is independent of the catalogue dictionaries.
    """
    aliases = dict(zip(SATELLITE_CATALOGUE, ["L8", "L9", "S2A", "S2B", "S2C"], strict=True))
    if mission is not None and mission.lower() not in {"landsat", "sentinel-2"}:
        raise ValueError("mission must be Landsat or Sentinel-2.")
    return pd.DataFrame(
        [
            {"satellite": name, "alias": aliases[name], **record}
            for name, record in SATELLITE_CATALOGUE.items()
            if mission is None or record["mission"].lower() == mission.lower()
        ]
    )
