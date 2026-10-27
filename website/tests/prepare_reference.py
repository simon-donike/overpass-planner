"""Generate ephemeral website test expectations from the unchanged installed package."""

import importlib.util
import json
import tempfile
from pathlib import Path

import pandas as pd

from eo_overpass_predictor import fetch_tles, satellite_positions, subsolar_point
from eo_overpass_predictor.presentation import json_value

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
spec = importlib.util.spec_from_file_location("website_adapter", SITE / "python/browser_adapter.py")
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
text = "\n".join(
    (ROOT / "tests/fixtures" / name).read_text() for name in ["satellites.tle", "sentinel1.tle"]
)
common = {
    "roi_name": "Historical browser parity test",
    "satellites": ["SENTINEL-2B"],
    "start_utc": "2026-10-26T00:00:00Z",
    "days": 1,
    "timezone": "",
    "imaging_only": True,
    "min_coverage": 0,
    "min_sun_elevation": 0,
    "s1_mode": "IW",
    "tle_text": text,
    "orbital_source": "Historical frozen test fixture",
    "roi": [144.35, -38.35, 145.15, -37.8],
}
configurations = [
    ("optical_polygon", {}),
    ("optical_point", {"roi": [144.96, -37.82]}),
    ("empty", {"roi": [144.96, -37.82], "days": 0.001}),
    (
        "optical_multi",
        {
            "days": 3,
            "satellites": ["SENTINEL-2B", "SENTINEL-2C", "LANDSAT 9"],
            "imaging_only": False,
        },
    ),
    (
        "sar_iw",
        {
            "start_utc": "2026-10-29T00:00:00Z",
            "days": 3,
            "satellites": ["SENTINEL-1C", "SENTINEL-1D"],
        },
    ),
    (
        "sar_ew",
        {
            "start_utc": "2026-10-29T00:00:00Z",
            "days": 3,
            "satellites": ["SENTINEL-1C", "SENTINEL-1D"],
            "s1_mode": "EW",
        },
    ),
    (
        "dst",
        {
            "start_utc": "2026-10-24T00:00:00Z",
            "days": 7,
            "roi": [-3.7038, 40.4168],
            "timezone": "Europe/Madrid",
        },
    ),
]
references = []
for name, overrides in configurations:
    request = {**common, **overrides}
    result = json.loads(adapter.run_request_json(json.dumps(request), lambda *args: None))
    references.append({"name": name, "request": request, "expected": result})
output = SITE / "public/test-data"
output.mkdir(exist_ok=True, parents=True)
(output / "references.json").write_text(json.dumps(references, indent=2) + "\n")
position_references = []
with tempfile.TemporaryDirectory() as directory:
    tles = fetch_tles(
        list(json.loads(adapter.catalogue_json())),
        tle_text=text,
        tle_dir=directory,
        offline=True,
        verbose=False,
    )
    for when in ["2026-10-29T12:34:56Z", "2026-11-30T12:34:56Z"]:
        request = {
            "satellites": list(tles),
            "tle_text": text,
            "when_utc": when,
            "orbital_source": "Frozen historical test elements",
        }
        position_references.append(
            {
                "request": request,
                "expected": json_value(satellite_positions(tles, when_utc=when).to_dict("records")),
                "batch": json.loads(adapter.tracks_json(json.dumps(request))),
                "interpolated": [
                    {
                        "when_utc": (
                            pd.Timestamp(when) + pd.Timedelta(milliseconds=offset)
                        ).isoformat(),
                        "positions": json_value(
                            satellite_positions(
                                tles,
                                when_utc=pd.Timestamp(when) + pd.Timedelta(milliseconds=offset),
                            ).to_dict("records")
                        ),
                    }
                    for offset in [0, 2500, 123456, 300001, 599999]
                ],
            }
        )
(output / "positions.json").write_text(json.dumps(position_references, indent=2) + "\n")
(output / "solar.json").write_text(
    json.dumps(
        [
            {"when_utc": when, "expected": subsolar_point(when)}
            for when in [
                "2026-03-20T14:46:00Z",
                "2026-06-21T08:24:00Z",
                "2026-09-23T00:06:00Z",
                "2026-12-21T20:50:00Z",
                "2026-10-27T00:00:00Z",
                "2026-10-27T12:00:00Z",
            ]
        ],
        indent=2,
    )
    + "\n"
)
print("Generated ephemeral expectations from native Python; existing baselines are unchanged.")
