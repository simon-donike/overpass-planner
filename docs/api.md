# Python API

All public functions below are available from both `eo_overpass_predictor` and `eo_overpass_predictor.api`. Use `help(function)` for the annotated signature and full scientific parameter documentation. Record types `SatelliteInfo`, `RawTLE`, and `TLERecord` are TypedDicts: values remain ordinary dictionaries.

Satellite-selection parameters accept individual names/aliases, numeric NORAD IDs, and mission groups: `"S2"` expands to Sentinel-2A/B/C, `"S1"` to Sentinel-1C/D, and `"LS"` to Landsat 8/9. Full mission names also work. Pass a single group string (`fetch_tles(satellites="S1")`), a comma-separated string, or an iterable such as `["S1", "S2B"]`. Selections are case insensitive and deduplicated in first-selected order. Plan retrieval accepts Sentinel-1 and Sentinel-2 selections.

| Functions | Inputs and return values |
|---|---|
| `list_satellites(mission=None)` | Catalogue DataFrame, optionally filtered to Landsat, Sentinel-2 or Sentinel-1. Includes aliases and NORAD IDs. `SATELLITE_CATALOGUE` retains original instrument metadata. |
| `load_roi(source, use_bbox=False, buffer_km=0, name=None)` | Normalize geospatial inputs to one named EPSG:4326 GeoDataFrame row. |
| `resolve_roi_timezone(roi_gdf, override=None, verbose=True)` | IANA timezone at the ROI anchor; use an explicit override for multi-zone ROIs. |
| `normalise_tle_line(line)`, `parse_tle_text(text)` | Validate fixed-width/checksum TLEs, including collapsed spacing. Parsing returns a NORAD-keyed dictionary of `RawTLE` records. |
| `fetch_tles(satellites=None, tle_dir=None, max_age_hours=12, tle_text=None, timeout=30, verbose=True, *, offline=False)` | Named `TLERecord` dictionary with epoch/source metadata, ready for prediction. Performs explicit cache/network I/O. |
| `predict_overpasses(roi_gdf, tles, start_utc=None, days=16, ..., s1_mode="IW", imaging_only=None)` | Prediction GeoDataFrame. Optical defaults remain daytime descending only; Sentinel-1 uses nominal IW geometry and admits day/night ascending/descending passes. `imaging_only` overrides the optical filter; when omitted, legacy `daytime_descending_only` behavior is retained. |
| `summarise_overpasses(passes)`, `next_overpasses(passes, now_utc=None)` | Compact report and next-pass countdown DataFrames; set `now_utc` for reproducible countdowns. |
| `satellite_positions(tles, when_utc=None, satellites=None)` | Snapshot DataFrame with latitude, longitude, altitude, ground speed and direction. |
| `solar_elevation_deg(times_utc, lat, lon)`, `subsolar_point(when_utc)` | Original analytic solar calculations; elevations array or `(lat, lon)` subsolar point. |
| `fetch_acquisition_plans(...)`, `fetch_s2_acquisition_plans(...)`, `fetch_s1_acquisition_plans(...)` | Fetch and cache overlapping ESA plans for Sentinel-1 and/or Sentinel-2. Mission-specific functions remain compatibility wrappers. |
| `load_acquisition_plans(source=None)`, `load_s2_acquisition_plans(...)`, `load_s1_acquisition_plans(...)` | Read KML/KMZ files, directories or iterables into a common typed plan GeoDataFrame. Mission-specific loaders remain compatibility wrappers. |
| `crosscheck_acquisition_plan(passes, plans, roi_gdf, time_tolerance_s=120)`, `crosscheck_s2_acquisition_plan(...)` | Copy predictions with plan status, datatake ID, relative orbit, mode and polarisation. Sentinel-1 user-plan matches report `planned`; the S2 wrapper preserves historical labels. |
| `export_overpass_csv(passes, out_dir="OUTPUT", file_name=None, expected_only=False)` | Write an explicitly requested CSV and return its Path. |
| `export_overpass_footprints(passes, path)` | Write GeoPackage, GeoJSON or legacy shapefile footprints; return Path. |
| `export_live_tracker_html(tles, roi_gdf, out_path=..., passes=None, ...)` | Write standalone HTML using installed package resources; return Path. No visualization extra needed. |
| `visualise_roi_on_map`, `visualise_overpass_footprints`, `plot_overpass_timeline` | Optional Folium maps / Matplotlib Figure; explicit `save_path` writes an artifact. Install `[visualization]`. |
| `live_tracker`, `LiveTracker` | Optional notebook widget tracker with explicit start/stop lifecycle. Install `[notebooks]`; a running asyncio event loop is needed for autostart. |
| `process_timer(process_name="Process")` | Original notebook reporting context manager. |

Acquisition statuses are `planned (NOMINAL)` or `planned (<category>)` for Sentinel-2, `planned` for a matching Sentinel-1 user plan, `not planned` (loaded planning coverage but no match), `no plan loaded` (unknown), and `n/a` (Landsat, which this code does not check). Sentinel-1 plan records may include mode, polarisation, datatake and relative-orbit fields. A plan is evidence of intended acquisition, not delivered imagery; the input prediction frame is copied rather than modified.

Sentinel-1C and Sentinel-1D use nominal right-looking C-SAR geometry. IW is the default mode; pass `s1_mode="EW"` for EW. Near and far off-nadir angles are interpolated from the published 698–726 km altitude tables and clamped outside that interval. Terrain, burst timing, Stripmap and Wave mode are outside this model. `imaging_only` controls the eligibility filter; when omitted, the historical `daytime_descending_only` setting is retained for optical sensors, while Sentinel-1 remains eligible in daylight or darkness and on either orbit direction.

Naive timestamps mean UTC. Structured prediction time columns are timezone aware; every local offset follows the zone's DST rules at the pass date. CSV preserves the original whole-second formatting. For optical point ROIs, entry/exit retain the original sampled closest-approach instant and can differ from the refined `overpass_utc` by a fraction of a second. Sentinel-1 point entry/exit equal the refined closest-approach instant. SAR prediction times are clipped to the requested window.

Defaults that depend on the current time remain available for interactive use; supply explicit dates and element sets for reproducibility. `verbose=False` suppresses optional progress output where supported. Invalid inputs raise `ValueError` (or the specific missing-key/timezone exception); missing orbital data raise `RuntimeError`, and filesystem failures raise ordinary I/O exceptions.

Download storage follows the [cache policy](../README.md#offline-operation-and-caches). Default TLE files overwrite stable per-satellite names. Default ESA downloads are pruned after retrieval (30 days / 50 MiB), protecting the paths returned for that call. Load these paths before a later retrieval can prune them, or supply an explicit `plan_dir` to manage your own archive. Explicit cache directories and offline reads are never automatically pruned. Use `tempfile.TemporaryDirectory()` with `tle_dir`/`plan_dir` for temporary Python workflows; keep the context open until plan loading is complete. Computational functions never create cache or output files.
