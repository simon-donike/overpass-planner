"""Compare browser/native results with the repository's established parity tolerances."""

import json
import sys
from pathlib import Path

import pandas as pd
from shapely.geometry import shape
from shapely.ops import transform

from eo_overpass_predictor.core.geometry import _local_equal_area

site = Path(__file__).resolve().parents[1]
references = json.loads((site / "public/test-data/references.json").read_text())
actual = json.loads(Path(sys.argv[1]).read_text())
assert len(actual) == len(references)
for reference, result in zip(references, actual, strict=True):
    expected = reference["expected"]
    name = reference["name"]
    assert result["document"]["count"] == expected["document"]["count"], name
    assert result["document"]["request"] == expected["document"]["request"], name
    assert result["csv_header"] == expected["csv_header"], name
    for row, target in zip(
        result["document"]["passes"], expected["document"]["passes"], strict=True
    ):
        assert row.keys() == target.keys(), name
        for key, value in target.items():
            label = f"{name}/{key}"
            if key == "geometry":
                geometry = shape(value)
                forward, _ = _local_equal_area(geometry)
                assert (
                    transform(forward, shape(row[key])).hausdorff_distance(
                        transform(forward, geometry)
                    )
                    <= 0.1
                ), label
            elif key.endswith(("_utc", "_local")):
                assert pd.Timestamp(row[key]) == pd.Timestamp(value), label
            elif isinstance(value, float):
                precision = (
                    2
                    if key
                    in {
                        "view_angle_deg",
                        "near_look_angle_deg",
                        "far_look_angle_deg",
                        "incidence_angle_deg",
                    }
                    else 1
                )
                assert abs(row[key] - value) <= 0.5 * 10**-precision, label
            else:
                assert row[key] == value, label
    assert result["csv_rows"] == expected["csv_rows"], f"{name}/CSV"
    print(f"{name}: browser/native parity ({result['document']['count']} passes)")
