"""Browser I/O adapter for Julian Manning's unchanged eo-overpass-predictor package."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import warnings
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import numpy as np
import pandas as pd

from eo_overpass_predictor import (
    SATELLITE_CATALOGUE,
    export_overpass_csv,
    fetch_tles,
    load_roi,
    predict_overpasses,
    resolve_roi_timezone,
)
from eo_overpass_predictor.presentation import json_value, prediction_document


def catalogue_json() -> str:
    return json.dumps(json_value(SATELLITE_CATALOGUE), allow_nan=False)


@contextmanager
def wasm_timezone_indexes():
    """Bridge timezonefinder 9.0's int64 repeat counts to wasm32's np.intp.

    NumPy repeat accepts only native-width counts on 32-bit builds, while this
    dependency explicitly constructs int64 counts. Convert bounded integer counts,
    never coordinates or scientific values. Scope the bridge to timezone lookup
    and restore NumPy even after an error; no installed package files are patched.
    """
    if np.dtype(np.intp).itemsize >= 8:
        yield
        return
    original = np.repeat
    original_fromiter = np.fromiter

    def native_repeat(array, repeats, axis=None):
        counts = np.asarray(repeats)
        if counts.dtype.kind in "iu" and counts.dtype.itemsize > np.dtype(np.intp).itemsize:
            limits = np.iinfo(np.intp)
            if counts.size and (counts.min() < 0 or counts.max() > limits.max):
                raise ValueError("Timezone index counts exceed this browser's memory limits.")
            repeats = counts.astype(np.intp)
        return original(array, repeats, axis=axis)

    def native_fromiter(iterable, dtype, count=-1, **kwargs):
        array = original_fromiter(iterable, dtype, count, **kwargs)
        # timezonefinder's hole-owner/start arrays are explicit int64 indexes too.
        if array.dtype == np.dtype(np.int64) and (
            not array.size or (array.min() >= 0 and array.max() <= np.iinfo(np.intp).max)
        ):
            return array.astype(np.intp)
        return array

    np.repeat = native_repeat
    np.fromiter = native_fromiter
    try:
        yield
    finally:
        np.repeat = original
        np.fromiter = original_fromiter


def run_request_json(request_json: str, notify) -> str:
    """Calculate each whole satellite window without changing sampling conventions."""
    request = json.loads(request_json)
    started = perf_counter()
    names = request["satellites"]
    if not names or any(name not in SATELLITE_CATALOGUE for name in names):
        raise ValueError("Choose at least one supported satellite.")
    if len(set(names)) != len(names):
        raise ValueError("Satellite selections must be unique.")
    start = pd.Timestamp(request["start_utc"])
    if pd.isna(start):
        raise ValueError("Choose a valid start date.")
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    diagnostics = list(request.get("orbital_warnings", []))
    notify("inputs", "Preparing region and orbital elements", 0, len(names), 0)
    with warnings.catch_warnings(record=True) as caught, tempfile.TemporaryDirectory() as directory:
        warnings.simplefilter("ignore", DeprecationWarning)
        warnings.simplefilter("always", UserWarning)
        area = load_roi(request["roi"], name=request.get("roi_name") or "Browser ROI")
        with wasm_timezone_indexes():
            timezone = resolve_roi_timezone(
                area, override=request.get("timezone") or None, verbose=False
            )
        tles = fetch_tles(
            satellites=names,
            tle_text=request["tle_text"],
            tle_dir=Path(directory) / "tle",
            offline=True,
            verbose=False,
        )
        provenance = request.get("orbital_source", "User-supplied TLE")
        for name, record in tles.items():
            record["source"] = request.get("orbital_sources", {}).get(name, provenance)
        frames = []
        calculation_start = perf_counter()
        for index, name in enumerate(names):
            notify("prediction", name, index, len(names), perf_counter() - calculation_start)
            frames.append(
                predict_overpasses(
                    area,
                    tles,
                    start_utc=start,
                    days=request["days"],
                    satellites=[name],
                    timezone_name=timezone,
                    imaging_only=request["imaging_only"],
                    min_roi_coverage_pct=request["min_coverage"],
                    min_sun_elevation_deg=request["min_sun_elevation"],
                    s1_mode=request["s1_mode"],
                    verbose=False,
                )
            )
            notify("prediction", name, index + 1, len(names), perf_counter() - calculation_start)
        # Retain the package's empty-frame dtypes and window metadata.
        populated = [frame for frame in frames if not frame.empty]
        passes = (
            gpd.GeoDataFrame(pd.concat(populated, ignore_index=True), crs="EPSG:4326")
            if populated
            else frames[0].copy()
        )
        passes = passes.sort_values("overpass_utc").reset_index(drop=True)
        passes.attrs = frames[0].attrs.copy()
        for warning in caught:
            if issubclass(warning.category, UserWarning):
                diagnostics.append({"code": "input_warning", "message": str(warning.message)})
        notify(
            "export",
            "Preparing results and downloads",
            len(names),
            len(names),
            perf_counter() - calculation_start,
        )
        parameters = {
            "min_roi_coverage_pct": request["min_coverage"],
            "min_sun_elevation_deg": request["min_sun_elevation"],
            "imaging_only": request["imaging_only"],
            "s1_mode": request["s1_mode"],
            "coarse_step_s": 30,
            "fine_step_s": 1,
        }
        document = prediction_document("next", area, tles, passes, parameters, diagnostics)
        data_note = files("timezonefinder_data") / "OVERPASS_DATA_PROVENANCE.json"
        if data_note.is_file():
            document["provenance"]["timezone_lookup"] = {
                "method": "manual IANA override"
                if request.get("timezone")
                else "simplified boundaries",
                "dataset": json.loads(data_note.read_text()),
            }
            if not request.get("timezone"):
                document["assumptions"].append(
                    "Automatic timezone uses simplified boundaries and may differ near borders. "
                    "Set an explicit IANA timezone if needed; UTC predictions are unaffected."
                )
        path = export_overpass_csv(passes, out_dir=directory, file_name="overpasses.csv")
        csv_text = path.read_text()
        csv_records = list(csv.reader(io.StringIO(csv_text)))
        return json.dumps(
            {
                "document": document,
                "csv": csv_text,
                "csv_header": csv_records[0],
                "csv_rows": csv_records[1:],
                "elapsed_s": perf_counter() - started,
            },
            allow_nan=False,
        )
