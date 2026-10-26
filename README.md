# eo-overpass-predictor

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23629976.svg)](https://doi.org/10.5281/zenodo.23629976)

Predict when the instrument swath of **Landsat 8/9 or Sentinel-2A/B/C** intersects a region of interest. Results include ROI coverage, entry/exit and closest-approach times, UTC and DST-aware local times, illumination, viewing geometry, and TLE age. Sentinel-2 predictions can be checked against ESA acquisition plans.

The scientific implementation is by **Julian Manning**. This development version packages that implementation for Python, the command line, and notebooks. It preserves the original orbital methodology; packaging regression tests are separate from independent validation against real acquisitions.

![Example overpass timeline](Example/IMG/overpass_timeline_example.png)

*Historical example from the original notebook workflow, not output from the frozen test fixtures.*

## Installation

Python **3.11–3.14** is tested. Conda and Jupyter are optional. This checkout is a development version (`1.1.0.dev0`); these commands install the checkout, without assuming a published PyPI release exists.

With [uv](https://docs.astral.sh/uv/), from the repository root:

```bash
uv sync
uv run eo-overpass --help
```

For a runtime-only environment use `uv sync --no-dev` and `uv run --no-dev eo-overpass --help`. Alternatively, install into an existing virtual environment with pip:

```bash
python -m pip install .
# Optional maps and plots:
python -m pip install '.[visualization]'
# Optional notebook environment, including maps, plots and live widgets:
python -m pip install '.[notebooks]'
```

GeoPandas/pyogrio and pyproj normally install using binary wheels. Platforms without compatible wheels may need native geospatial build dependencies. No Conda-only dependency is required.

## Python quick start

```python
from eo_overpass_predictor import (
    load_roi,
    fetch_tles,
    predict_overpasses,
    summarise_overpasses,
    export_overpass_csv,
)

roi = load_roi((144.35, -38.35, 145.15, -37.80), name="Port_Phillip_Bay")
tles = fetch_tles(satellites=["S2B"])  # Downloads/caches public TLEs.
passes = predict_overpasses(
    roi,
    tles,
    start_utc="2026-11-01",
    days=14,
    timezone_name="Australia/Melbourne",
)
print(summarise_overpasses(passes))
export_overpass_csv(passes, out_dir="OUTPUT", file_name="predictions.csv")
```

`predict_overpasses` performs no downloads or file writes. Dates without an offset mean UTC. The API retains the original `start_utc` plus `days` interface; the CLI translates its start/end window to the same duration. Choose dates near the epoch of the supplied elements: historical predictions require suitable historical TLEs.

Runnable examples: [online prediction](examples/predict.py) and [frozen offline example](examples/offline_prediction.py).

## Command line

Find upcoming passes without creating an ROI file or choosing an export path:

```bash
uv run eo-overpass next --point 144.96 -37.82
uv run eo-overpass next --bbox 144.35 -38.35 145.15 -37.80 --days 7 --min-coverage 80
uv run eo-overpass next --point 144.96 -37.82 --satellite S2 --output planner.html
uv run eo-overpass next --point 144.96 -37.82 --days 7 --json
```

Coordinates are **longitude, latitude**. `next` takes exactly one of `--point`,
`--bbox`, or `--roi`; it defaults to the current UTC minute through the next 14
days. Use `--start` for an explicit date. Terminal reports show local/UTC times,
coverage, imaging eligibility, acquisition-plan evidence, and element freshness.
Export is optional; CSV, GeoJSON, GeoPackage and HTML are supported.

```bash
uv run eo-overpass satellites
uv run eo-overpass satellites --mission Sentinel-2

uv run eo-overpass predict \
  --roi area.geojson --start 2026-11-01 --end 2026-11-15 \
  --satellite S2 --output predictions.csv

uv run eo-overpass predict \
  --roi area.geojson --start 2026-11-01 --end 2026-11-15 \
  --satellite S2B S2C --min-coverage 25 --plans DATA/KML \
  --output footprints.gpkg

uv run eo-overpass tracker \
  --roi area.geojson --start 2026-11-01 --end 2026-11-15 \
  --satellite LS --output live_tracker.html
```

Use `next --help`, `predict --help` or `tracker --help` for all options. `--satellite S2` selects Sentinel-2A/B/C; `--satellite LS` selects Landsat 8/9. Multiple values can follow one option (`--satellite S2B S2C` or `--satellite S2 LS`), ending at the next option. Comma-separated values and repeated options also work. Names and group aliases are case insensitive; full names containing spaces must be quoted, and NORAD IDs are accepted. Overlapping selections are included only once, in first-selected order. Omitting the option selects all five satellites. `--timezone` overrides automatic ROI-local timezone selection. `--all-passes` includes night/ascending geometric passes. Output suffixes select CSV, GeoJSON, GeoPackage or HTML; explicit `--format` must agree with the suffix.

`--plans` uses local KML/KMZ files or a directory. `--fetch-plans` explicitly retrieves ESA plans and cannot be combined with `--plans` or `--offline`. Missing optional plans yield **no plan loaded**, which means unknown acquisition status. Exit codes are 0 for success (including an empty prediction), 2 for invalid inputs, and 1 for operational failures.

## Offline operation and caches

Installing from PyPI does not require storing data in `/tmp` or inside the installed package. Reusable downloads belong in the OS user cache, temporary work belongs in the system temporary directory, and reports belong at your chosen output path.

Default download roots follow [platformdirs](https://platformdirs.readthedocs.io/en/latest/api.html): typically `~/.cache/eo-overpass-predictor` on Linux (honouring `XDG_CACHE_HOME`), `~/Library/Caches/eo-overpass-predictor` on macOS, and the user local application cache on Windows. `cache info` prints the actual location. Override with CLI `--cache-dir`, or API `tle_dir`/`plan_dir` for individual directories.

- **TLEs:** one file per satellite, at most five stable filenames. New elements replace previous ones atomically. Fresh entries are reused for 12 hours; stale entries remain available with warnings for offline/network-failure fallback.
- **ESA plans:** after successful online retrieval, the default cache removes downloads older than 30 days, then the oldest remaining files toward a 50 MiB budget. Files returned for the current request are protected, including historical plans; they may temporarily exceed the budget. Offline loading does not prune files. Explicit `plan_dir` and `--cache-dir` overrides are not automatically pruned.
- **Temporary mode:** add `--no-cache` to `next`, `predict` or `tracker`. Downloads use a unique system temporary directory, removed on normal completion or Python errors. It ignores persistent caches, so `--offline --no-cache` requires supplied TLEs for every selected satellite. It cannot be combined with `--cache-dir`.
- **Outputs:** CSV, GeoJSON, GeoPackage and HTML remain at `--output` until you remove them. Supplied ROI/TLE/plan files are read as inputs. Cache management never searches output directories or unrelated folders.

```bash
uv run eo-overpass cache info
uv run eo-overpass cache clear
uv run eo-overpass cache clear --cache-dir /path/to/custom-cache
uv run eo-overpass predict \
  --roi area.geojson --start 2026-11-01 --end 2026-11-15 \
  --satellite S2 --no-cache --output predictions.csv
```

`cache clear` removes only recognised download filenames inside `tle/` and `plans/`, plus package temporary-write files; it preserves unrelated files and skips symlinks. Clearing removes the orbital inputs needed for cache-only offline runs. Cache commands do not create directories. Atomic writes clean up unfinished files after ordinary errors; managed-cache maintenance also removes abandoned write files older than a day. A forcibly killed process can leave a temporary directory, subject to the OS's normal temporary-file cleanup.

For Python calls without persistent caching, use `tempfile.TemporaryDirectory()` around retrieval and loading, passing its path to `tle_dir` and/or `plan_dir`. Keep the context open until the returned plan paths have been loaded. `predict_overpasses` itself only calculates from supplied inputs and creates no files.

```python
from pathlib import Path
from eo_overpass_predictor import fetch_tles

tles = fetch_tles(
    satellites=["S2B"],
    tle_text=Path("saved-elements.tle").read_text(),
    tle_dir="DATA/TLE",
    offline=True,
)
```

Offline mode never requests orbital data from a service. All selected satellites must be present in the supplied text or cache. Manually supplied elements override and update their cache entries. TLE checksums and paired NORAD identifiers are validated.

A fully offline command using the bundled *historical regression* inputs:

```bash
uv run eo-overpass predict \
  --roi DATA/SHP/Port_Phillip_Bay_Extent_EPSG_32755.shp \
  --start 2026-10-26 --end 2026-10-27 --satellite S2B \
  --tle-file tests/fixtures/satellites.tle --offline \
  --cache-dir DATA --output OUTPUT/offline-example.csv
```

The fixed inputs demonstrate reproducibility; do not use them as current orbital elements. The demonstration notebooks intentionally use their configured workspace's `DATA/TLE`, `DATA/KML` and `OUTPUT` directories; these explicit workspace paths are outside automatic cache retention.

## Supported instruments and scientific assumptions

| Satellite | Alias | NORAD | Instrument | Full swath |
|---|---|---:|---|---:|
| Landsat 8 | L8 | 39084 | OLI/TIRS | 185 km |
| Landsat 9 | L9 | 49260 | OLI-2/TIRS-2 | 185 km |
| Sentinel-2A | S2A | 40697 | MSI | 290 km |
| Sentinel-2B | S2B | 42063 | MSI | 290 km |
| Sentinel-2C | S2C | 60989 | MSI | 290 km |

The original engine uses Skyfield SGP4 propagation, WGS84 geodesic cross-track scan lines, an ROI-centred Lambert azimuthal equal-area projection, a 30-second coarse scan and 1-second refinement. Closest approach uses the original parabolic refinement and 0.1-second rounding. Expected imaging defaults to descending, sunlit passes inside the original mission latitude limits. The timescale uses bundled Skyfield data; no planetary ephemeris download is needed.

The original sampling convention includes window endpoints and may include a final fine-grid sample beyond a fractional endpoint. The refactor does not apply a new half-open-window filter. At least one fine time step is required; steps must be finite, positive, and fine must not exceed coarse.

Geometry indicates that an instrument **can** image an ROI. Acquisition, processing, delivery, and cloud-free imagery are not guaranteed. Landsat plans are not verified. ESA KML footprints are simplified; edge passes can appear unplanned despite an overlapping datatake. Fresh TLEs are important, especially after orbital manoeuvres. Treat swath-edge margins of approximately ±5 km as uncertain.

Use compact ROIs. Very large, polar, or antimeridian-spanning prediction ROIs retain the original projection/swath limitations; antimeridian handling in ESA plan matching does not establish general global prediction accuracy. The model supports nadir-centred optical sensors, not side-looking SAR. Mission-status notes in catalogue output are preserved from October 2026 and are dated context, not a continuously updated operational feed.

## Inputs, results and exports

`load_roi` accepts vector file paths, GeoDataFrames/GeoSeries in a declared CRS, Shapely geometries, supported WKT strings, `(lon, lat)` points, `(west, south, east, north)` bounds, and GeoJSON geometry/Feature/FeatureCollection mappings. In-memory geometry, WKT, tuples and GeoJSON mappings use WGS84 longitude/latitude. Missing CRS in a geospatial frame produces a warning and assumes EPSG:4326. Features are dissolved and invalid geometry is repaired; empty or out-of-range coordinates are rejected. An optional nonnegative buffer is applied in the original local equal-area projection.

Predictions are EPSG:4326 GeoDataFrames, one row per overpass, sorted by closest-approach time. Their `attrs` contain `roi_name`, `timezone`, `window_start_utc`, and `window_end_utc`. Empty results retain the schema and timezone-aware time columns.

| Columns | Meaning |
|---|---|
| `satellite`, `mission`, `sensor`, `norad_id` | Satellite and instrument identity. |
| `direction`, `imaging_expected`, `roi_coverage_pct` | Acquisition filter and fraction of the ROI covered; points report 100% when intersected. |
| `roi_entry_utc`, `overpass_utc`, `roi_exit_utc`, `duration_s` | Swath intersection window and refined closest approach. |
| `roi_entry_local`, `overpass_local`, `roi_exit_local`, `timezone`, `utc_offset` | The same instants in the ROI IANA zone with per-date DST offsets. |
| `nadir_offset_km`, `roi_side_of_track`, `edge_margin_km`, `view_angle_deg` | Viewing geometry relative to the ROI anchor. |
| `max_elevation_deg`, `sun_elevation_deg`, `sat_altitude_km` | Elevation, illumination and altitude. |
| `tle_epoch_utc`, `tle_age_days`, `note`, `geometry` | Orbital provenance, dated mission context and display swath footprint. |
| `esa_plan_status`, `esa_datatake_id`, `esa_relative_orbit` | Added by acquisition-plan cross-checking. |

CSV export omits geometry, preserves original whole-second ISO timestamp serialization (`Z` for UTC and explicit local offsets), and adds `local_date`, `local_time` and `local_weekday`. Structured API results retain subsecond closest-approach timestamps. GeoJSON/GeoPackage export includes footprints and serializes timezone-aware attributes to strings. The legacy shapefile exporter also remains available through the API, subject to that format's field-name and type limits.

See the [API reference](docs/api.md) for function groups and ESA status values.

## Notebooks and visualization

```bash
uv sync --extra notebooks
uv run --extra notebooks jupyter lab
```

Run [setup diagnostics](notebooks/p00_overpass_env_setup.ipynb), [prediction/reporting](notebooks/p01_overpass_prediction.ipynb), then [live tracking](notebooks/p02_overpass_live_tracker.ipynb). All import the installed package. Set `EO_OVERPASS_WORKSPACE` to a directory containing `DATA/` to override repository discovery; notebook exports go to its `OUTPUT/` directory.

For maps and timelines without Jupyter, install only the `visualization` extra. The widget tracker requires a running notebook event loop and supports `tracker.stop()` / `tracker.start()`. The standalone HTML tracker embeds TLEs, predictions and coastlines, requires no Python to view, and loads satellite.js and font styles from public CDNs. Folium/ipyleaflet basemaps require network access. Embedded TLEs and countdown windows age; re-export to refresh them.

The original scientific explanations, validation examples, images and [historical notebook documentation](docs/original-notebook-workflow.md) are retained. [Migration notes](docs/migration.md) describe moved paths and changed defaults.

## Development and testing

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
uv run twine check dist/*
uv run python tests/validate_distribution.py dist/eo_overpass_predictor-1.1.0.dev0-py3-none-any.whl
```

Plain `uv sync` installs base and development dependencies. Optional-feature tests skip when their extras are absent; use `--all-extras` for the complete suite. Tests use fixed, sourced TLEs, fixed dates and mocked HTTP services, with no live data dependency. Notebook tests start local Jupyter kernels and therefore need local socket permissions. Distribution validation installs dependencies first, then checks the installed API and CLI offline outside the checkout. `uv build` builds the wheel from its source distribution.

[Regression provenance](tests/fixtures/README.md) explains reference generation, versions and tolerances. Updating references requires review, not simply accepting changed outputs.

## Citation, attribution and license

**Original author:** Julian Manning — [ORCID](https://orcid.org/0000-0003-4481-5474), [email](mailto:julian.manning@outlook.com). The SGP4/swath scientific implementation and original notebooks are his work. Subsequent packaging contributions introduce the installable layout, CLI, regression suite, dependency lock, and automation without claiming authorship of the original science.

The original [CITATION.cff](CITATION.cff), version 1.0.0 citation and [Zenodo DOI](https://doi.org/10.5281/zenodo.23629976) remain intact. The development package version is distinct from that archived scientific release. The [MIT license](LICENSE) and original copyright notice are unchanged.

Data attribution: orbital elements from [CelesTrak](https://celestrak.org/); acquisition plans © ESA from its [Sentinel-2 plans page](https://sentinels.copernicus.eu/copernicus/sentinel-2/acquisition-plans); embedded Natural Earth 1:50m coastlines (public domain), via world-atlas. Key software includes [Skyfield](https://rhodesmill.org/skyfield/), [satellite.js](https://github.com/shashwatak/satellite-js), and [timezonefinder](https://github.com/jannikmi/timezonefinder).

Related original projects: [CDSE Sentinel-2 pipeline](https://github.com/julianManning/cdse-sentinel2-pipeline) and [CDSE Sentinel-1 pipeline](https://github.com/julianManning/cdse-sentinel1-pipeline).
