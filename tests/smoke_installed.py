"""Exercise a base-only installed wheel from outside the source checkout."""

import json
import sys
from pathlib import Path

import eo_overpass_predictor as eo


def main() -> None:
    fixtures = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    assert all(
        name not in sys.modules for name in ["folium", "matplotlib", "ipyleaflet", "IPython"]
    )
    area = eo.load_roi((144.35, -38.35, 145.15, -37.80))
    tles = eo.fetch_tles(
        satellites=["LS", "S2"],
        tle_text=(fixtures / "satellites.tle").read_text(),
        tle_dir=destination / "tle",
        offline=True,
        verbose=False,
    )
    passes = eo.predict_overpasses(
        area,
        tles,
        start_utc="2026-10-26",
        days=1,
        timezone_name="Australia/Melbourne",
        verbose=False,
    )
    expected = json.loads((fixtures / "baseline.json").read_text())["cases"]["polygon"]["rows"][0]
    assert len(passes) == 1
    assert passes["overpass_utc"].iloc[0].isoformat() == expected["overpass_utc"]
    eo.export_overpass_csv(passes, destination, file_name="wheel.csv")
    eo.export_overpass_footprints(passes, destination / "wheel.gpkg")
    eo.export_live_tracker_html(tles, area, destination / "wheel.html", passes=passes)
    assert "/*__PAYLOAD__*/null" not in (destination / "wheel.html").read_text()
    radar_tles = eo.fetch_tles(
        satellites="S1",
        tle_text=(fixtures / "sentinel1.tle").read_text(),
        tle_dir=destination / "tle",
        offline=True,
        verbose=False,
    )
    radar = eo.predict_overpasses(
        area,
        radar_tles,
        start_utc="2026-10-26",
        days=5,
        timezone_name="UTC",
        s1_mode="EW",
        verbose=False,
    )
    assert not radar.empty
    assert radar["mission"].eq("Sentinel-1").all()
    assert radar["mode"].eq("EW").all()
    assert radar["imaging_expected"].all()
    eo.export_live_tracker_html(radar_tles, area, destination / "wheel-sar.html", passes=radar)
    assert "EW" in (destination / "wheel-sar.html").read_text()
    # An optional call gives installation guidance without breaking normal imports.
    try:
        eo.visualise_roi_on_map(area)
    except ImportError as exc:
        assert "[visualization]" in str(exc)
    else:
        raise AssertionError("Wheel smoke environment unexpectedly contains Folium")
    print("Installed wheel:", eo.__version__, eo.__file__)


if __name__ == "__main__":
    main()
