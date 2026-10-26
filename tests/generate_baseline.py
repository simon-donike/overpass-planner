"""Regenerate references explicitly from the untouched upstream commit.

Run in an environment containing the dependencies recorded in baseline.json:
    python tests/generate_baseline.py
No network access is used. Review scientific differences before updating references.
"""

import hashlib
import importlib.metadata
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
COMMIT = "ec78fe6"
CASES = {
    "polygon": {"roi": [144.35, -38.35, 145.15, -37.80], "days": 16},
    "point": {"roi": [144.96, -37.82], "days": 16},
    "partial": {"roi": [143.5, -39.0, 146.0, -37.0], "days": 3},
    "empty": {"roi": [144.96, -37.82], "days": 0.001},
    "night_passes": {
        "roi": [144.35, -38.35, 145.15, -37.80],
        "days": 16,
        "daytime_descending_only": False,
    },
    "latitude_limit": {
        "roi": [0, -65],
        "days": 16,
        "satellites": ["S2B"],
        "daytime_descending_only": False,
    },
    "window_boundary": {
        "roi": [144.35, -38.35, 145.15, -37.80],
        "days": 1649 / 86400,
        "satellites": ["S2B"],
    },
    "unfiltered": {
        "roi": [144.35, -38.35, 145.15, -37.80],
        "days": 2,
        "daytime_descending_only": False,
    },
}


def main():
    source = subprocess.check_output(["git", "show", f"{COMMIT}:Tools/overpass_helper_utility.py"])
    namespace = {"__name__": "original_overpass", "__file__": "original_overpass.py"}
    exec(compile(source, "original_overpass.py", "exec"), namespace)
    output = {
        "commit": COMMIT,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "python": sys.version,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in [
                "numpy",
                "pandas",
                "geopandas",
                "shapely",
                "pyproj",
                "skyfield",
                "sgp4",
                "timezonefinder",
                "timezonefinder-data",
                "tzdata",
            ]
        },
        "cases": {},
    }
    with tempfile.TemporaryDirectory() as cache:
        tles = namespace["fetch_tles"](
            tle_text=(FIXTURES / "satellites.tle").read_text(), tle_dir=cache, verbose=False
        )
        output["tle_epochs"] = {key: value["epoch_utc"].isoformat() for key, value in tles.items()}
        for name, params in CASES.items():
            config = dict(params)
            roi = namespace["load_roi"](config.pop("roi"), name="Regression ROI")
            frame = namespace["predict_overpasses"](
                roi,
                tles,
                start_utc="2026-10-26",
                timezone_name="Australia/Melbourne",
                verbose=False,
                **config,
            )
            rows = []
            for row in frame.to_dict("records"):
                row["geometry"] = row["geometry"].wkt
                rows.append(
                    {
                        key: value.isoformat() if hasattr(value, "isoformat") else value
                        for key, value in row.items()
                    }
                )
            output["cases"][name] = {
                "config": params,
                "rows": rows,
                "attrs": {
                    k: v.isoformat() if hasattr(v, "isoformat") else v
                    for k, v in frame.attrs.items()
                },
            }
            print(name, len(frame), "passes; coverage:", frame["roi_coverage_pct"].tolist())
    (FIXTURES / "baseline.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
