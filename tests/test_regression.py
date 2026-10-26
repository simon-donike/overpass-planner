"""Compare against outputs generated before extracting the original implementation."""

import hashlib
import json

import pandas as pd
import pytest
import shapely
from conftest import FIXTURES
from pyproj import Transformer
from shapely.ops import transform


@pytest.mark.parametrize(
    "case",
    [
        "polygon",
        "point",
        "partial",
        "empty",
        "unfiltered",
        "night_passes",
        "latitude_limit",
        "window_boundary",
    ],
)
def test_original_scientific_outputs(case, baseline, results):
    actual = results[case]
    reference = baseline["cases"][case]
    assert actual.crs.to_epsg() == 4326
    assert len(actual) == len(reference["rows"])
    metric_crs = 32731 if case == "latitude_limit" else 32755
    project = Transformer.from_crs(4326, metric_crs, always_xy=True).transform
    for row, expected in zip(actual.to_dict("records"), reference["rows"], strict=True):
        assert list(row) == list(expected)
        for column, value in expected.items():
            if column == "geometry":
                # 10 cm is negligible beside the 1-second (~7 km) orbit sampling;
                # compare in metres rather than using degree-dependent tolerances.
                assert (
                    transform(project, row[column]).hausdorff_distance(
                        transform(project, shapely.from_wkt(value))
                    )
                    <= 0.1
                )
            elif column.endswith(("_utc", "_local")):
                assert row[column] == pd.Timestamp(value)
            elif isinstance(value, float):
                precision = 2 if column == "view_angle_deg" else 1
                assert row[column] == pytest.approx(value, abs=0.5 * 10**-precision, rel=0)
            else:
                assert row[column] == value
    for key, value in reference["attrs"].items():
        assert actual.attrs[key] == (pd.Timestamp(value) if key.endswith("_utc") else value)


def test_frozen_tles_have_provenance():
    lines = (FIXTURES / "satellites.tle").read_text().splitlines()
    records = json.loads((FIXTURES / "tle_provenance.json").read_text())
    assert len(records) == 5
    for i, record in enumerate(records):
        text = "\n".join(lines[i * 3 : i * 3 + 3]) + "\n"
        assert hashlib.sha256(text.encode()).hexdigest() == record["sha256"]
        assert record["source"].startswith("https://celestrak.org/")
        assert record["tle_epoch_field"] == lines[i * 3 + 1][18:32]
