# Migrating the original notebooks

The three `p00_…`, `p01_…`, and `p02_…` notebooks now live under `notebooks/`. They import `eo_overpass_predictor` instead of modifying `sys.path` or importing `overpass_helper_utility`. The old helper has been extracted, not retained as a second engine. Replace external `from overpass_helper_utility import …` statements with `from eo_overpass_predictor import …` after installing the package.

Use `uv sync --extra notebooks` and `uv run --extra notebooks jupyter lab` from the repository root. The setup notebook now diagnoses the current environment rather than creating Conda environments or registering user kernels. The legacy `Tools/` Conda definition and environment builder have been removed; their source remains in Git history. Install dependencies through `pyproject.toml` and `uv.lock`.

Repository discovery supports launching from the root or notebook directory without pre-existing `DATA/` or `OUTPUT/` folders. The sample shapefile formerly under `DATA/SHP/` is now the single WGS84 file `examples/data/port_phillip_bay_australia.geojson`, preserving the reprojected geometry and removing the shapefile sidecars. `madrid_spain.geojson` and `austin_usa.geojson` add approximate city study areas. All sample filenames follow `place_country.geojson`. `cabauw_netherlands.geojson` and `railroad_valley_usa.geojson` add sourced observation-site points. Set `ROI_FILE` in either notebook to choose a polygon or point sample; notebooks load it from the repository independently of the workspace. Set `EO_OVERPASS_WORKSPACE` to choose a directory for local `DATA/TLE`, `DATA/KML` and `OUTPUT` files, created as needed and ignored by Git in this checkout. To use your own ROI, edit the notebook's `load_roi` input. Interactive outputs were cleared to avoid carrying stale forecasts. Scientific markdown is preserved; former `Example/IMG/` assets now live in `docs/assets/`, and the original CSV in `examples/outputs/`.

API names and scientific arguments remain. Default API caches have moved from working-directory `DATA/TLE` and `DATA/KML` to OS user cache directories. Supply `tle_dir` and `plan_dir` explicitly to reuse an existing cache. CSV and HTML writer defaults still use `OUTPUT/`; `predict` and `tracker` require explicit CLI output paths; `next` needs no output file.

New validation rejects empty selections, malformed or mismatched TLE records, invalid dates, nonfinite/invalid step sizes, reversed bounding boxes, invalid coordinates and negative buffers. Empty predictions now have typed time/numeric columns and can be exported and plotted. GeoJSON mappings and explicit offline retrieval are additions. Algorithmic limitations, sampling, formulas, output rounding and scientific assumptions are unchanged.

Satellite selections now accept `S2` (all Sentinel-2), `S1` (Sentinel-1C/D) and `LS` (all Landsat), as well as lists after one CLI option: `--satellite S1 S2`. Comma-separated selections and the original repeated-option syntax remain supported. Omitting the selection includes all seven supported units. Overlapping group/member selections are deduplicated, preventing duplicate calculations or prediction rows. Sentinel-1 uses nominal right-looking IW geometry by default; `--s1-mode EW` selects EW.

The prediction API adds keyword-only Sentinel-1 controls: `s1_mode="IW"` and
`imaging_only`. When `imaging_only` is omitted, the legacy
`daytime_descending_only` behavior remains available for optical predictions.
Sentinel-1 geometry is eligible day/night and ascending/descending; geometry,
eligibility, ESA plan evidence and imagery availability are separate facts.
Sentinel-1 plans use the generic `fetch_acquisition_plans`,
`load_acquisition_plans` and `crosscheck_acquisition_plan` APIs; the existing
Sentinel-2 names remain compatibility wrappers. Plan matches can include mode,
polarisation, datatake and relative orbit fields.

The CLI JSON contract is version 2.0. New Sentinel-1 records include mode/look
side/incidence fields, and acquisition status has a `planned` code for matching
Sentinel-1 user plans. Consumers should continue to tolerate additional fields.

The HTML template and coastline data moved into installed package resources. Exported tracker behavior is preserved, with escaped page titles. Folium, Matplotlib and widget libraries are optional and loaded on demand.

The citation file deliberately retains the original archived release's version/date/DOI, independently of package version `1.1.0.dev0`. See [historical scientific documentation](original-notebook-workflow.md) for the original validation observations; packaging regression checks establish code parity, not new observational accuracy claims.

The CLI adds `--no-cache` for temporary downloads and `cache info` / `cache clear` for download management. Default ESA downloads now have 30-day / 50 MiB retention after online retrieval, protecting the current request's files. Explicit `plan_dir`, `--cache-dir` and notebook workspace directories retain user-managed lifetimes. TLE stale/offline fallback and explicit output locations are unchanged.
