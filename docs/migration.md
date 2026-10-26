# Migrating the original notebooks

The three `p00_…`, `p01_…`, and `p02_…` notebooks now live under `notebooks/`. They import `eo_overpass_predictor` instead of modifying `sys.path` or importing `overpass_helper_utility`. The old helper has been extracted, not retained as a second engine. Replace external `from overpass_helper_utility import …` statements with `from eo_overpass_predictor import …` after installing the package.

Use `uv sync --extra notebooks` and `uv run --extra notebooks jupyter lab` from the repository root. The setup notebook now diagnoses the current environment rather than creating Conda environments or registering user kernels. `Tools/env.base.yml` and `Tools/overpass_env_builder.py` remain optional historical resources, not part of package runtime or CI lint scope. That legacy Conda environment alone does not install the new package; install this checkout into it before using the migrated notebooks.

Repository discovery supports launching from the root or notebook directory. Set `EO_OVERPASS_WORKSPACE` to override the directory containing `DATA/`; notebooks explicitly use its `DATA/TLE`, `DATA/KML`, sample `DATA/SHP` and `OUTPUT` locations. Interactive outputs were cleared to avoid carrying stale forecasts. Scientific markdown, the sample shapefile, `Example/` assets and the original CSV are preserved.

API names and scientific arguments remain. Default API caches have moved from working-directory `DATA/TLE` and `DATA/KML` to OS user cache directories. Supply `tle_dir` and `plan_dir` explicitly to reuse an existing cache. CSV and HTML writer defaults still use `OUTPUT/`.

New validation rejects empty selections, malformed or mismatched TLE records, invalid dates, nonfinite/invalid step sizes, reversed bounding boxes, invalid coordinates and negative buffers. Empty predictions now have typed time/numeric columns and can be exported and plotted. GeoJSON mappings and explicit offline retrieval are additions. Algorithmic limitations, sampling, formulas, output rounding and scientific assumptions are unchanged.

The HTML template and coastline data moved into installed package resources. Exported tracker behavior is preserved, with escaped page titles. Folium, Matplotlib and widget libraries are optional and loaded on demand.

The citation file deliberately retains the original archived release's version/date/DOI, independently of package version `1.1.0.dev0`. See [historical scientific documentation](original-notebook-workflow.md) for the original validation observations; packaging regression checks establish code parity, not new observational accuracy claims.