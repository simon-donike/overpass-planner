"""Generate ephemeral website test expectations from the unchanged installed package."""

import importlib.util
import json
from pathlib import Path

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
print("Generated ephemeral expectations from native Python; existing baselines are unchanged.")
