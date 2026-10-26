# Migrating the original notebooks

The three `p00_…`, `p01_…`, and `p02_…` notebooks now live under `notebooks/`. They import `eo_overpass_predictor` instead of modifying `sys.path` or importing `overpass_helper_utility`. The old helper has been extracted, not retained as a second engine. Replace external `from overpass_helper_utility import …` statements with `from eo_overpass_predictor import …` after installing the package.

Use `uv sync --extra notebooks` and `uv run --extra notebooks jupyter lab` from the repository root. The setup notebook now diagnoses the current environment rather than creating Conda environments or registering user kernels. The legacy `Tools/` Conda definition and environment builder have been removed; their source remains in Git history. Install dependencies through `pyproject.toml` and `uv.lock`.

Repository discovery supports launching from the root or notebook directory without pre-existing `DATA/` or `OUTPUT/` folders. The sample shapefile moved from `DATA/SHP/` to `examples/data/`; notebooks load it from the repository independently of the workspace. Set `EO_OVERPASS_WORKSPACE` to choose a directory for local `DATA/TLE`, `DATA/KML` and `OUTPUT` files, created as needed and ignored by Git in this checkout. To use your own ROI, edit the notebook's `load_roi` input. Interactive outputs were cleared to avoid carrying stale forecasts. Scientific markdown is preserved; former `Example/IMG/` assets now live in `docs/assets/`, and the original CSV in `examples/outputs/`.

API names and scientific arguments remain. Default API caches have moved from working-directory `DATA/TLE` and `DATA/KML` to OS user cache directories. Supply `tle_dir` and `plan_dir` explicitly to reuse an existing cache. CSV and HTML writer defaults still use `OUTPUT/`; `predict` and `tracker` require explicit CLI output paths; `next` needs no output file.

New validation rejects empty selections, malformed or mismatched TLE records, invalid dates, nonfinite/invalid step sizes, reversed bounding boxes, invalid coordinates and negative buffers. Empty predictions now have typed time/numeric columns and can be exported and plotted. GeoJSON mappings and explicit offline retrieval are additions. Algorithmic limitations, sampling, formulas, output rounding and scientific assumptions are unchanged.

Satellite selections now accept `S2` (all Sentinel-2) and `LS` (all Landsat), as well as lists after one CLI option: `--satellite S2B S2C`. Comma-separated selections and the original repeated-option syntax remain supported. Overlapping group/member selections are deduplicated, preventing duplicate calculations or prediction rows.

The HTML template and coastline data moved into installed package resources. Exported tracker behavior is preserved, with escaped page titles. Folium, Matplotlib and widget libraries are optional and loaded on demand.

The citation file deliberately retains the original archived release's version/date/DOI, independently of package version `1.1.0.dev0`. See [historical scientific documentation](original-notebook-workflow.md) for the original validation observations; packaging regression checks establish code parity, not new observational accuracy claims.

The CLI adds `--no-cache` for temporary downloads and `cache info` / `cache clear` for download management. Default ESA downloads now have 30-day / 50 MiB retention after online retrieval, protecting the current request's files. Explicit `plan_dir`, `--cache-dir` and notebook workspace directories retain user-managed lifetimes. TLE stale/offline fallback and explicit output locations are unchanged.
