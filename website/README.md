# Overpass: browser-only overpass planner

A separate static website using Julian Manning's **eo-overpass-predictor** Python
package. The browser loads an unchanged wheel built from this checkout and runs
predictions in a Pyodide module worker. No application backend, database, Docker
service or hosted prediction computation is involved. The Python package and its configuration
are not modified by the website.

The map supports point, rectangle and polygon drawing, editing and deletion,
coordinate entry, and WGS84 GeoJSON uploads (Geometry, Feature or FeatureCollection).
The planner exposes satellite selection, UTC or device-local start time, duration, ROI timezone,
coverage/illumination filters and Sentinel-1 IW/EW. Results include selectable map
footprints, a sortable table, CSV preview and CSV/GeoJSON/JSON downloads. Complete
and filtered downloads are labelled separately.

## Run locally

From the repository root, with Python/uv and Node.js **22+** installed:

```bash
uv sync --all-extras
uv run python website/scripts/prepare.py
cd website
npm ci
npm run dev
```

Open the printed localhost URL. The development server only serves files; scientific
calculations and orbital-data retrieval run in the browser. Click Calculate to
download TLEs directly for the selected satellites, or paste/upload your own TLEs.
Never use the historical test TLEs as current elements.

`npm run prepare:python` is an equivalent preparation shortcut from `website/`.
First preparation needs internet access; pinned runtime assets and
downloads are subsequently reused. The first browser calculation loads Python and
the geospatial libraries. This is a sizeable download (roughly 45 MiB of static
assets); subsequent calculations reuse the worker. No speed or mobile-memory
guarantee is implied. Long calculations have no fixed deadline.

## Cloudflare deployment

The production address is **https://overpass.hyperalislabs.com**, deployed
as the `overpass` Worker using **Workers Static Assets**. There is no Worker script,
server-side calculation, database, R2 bucket, upload endpoint or scheduled job.
Cloudflare serves the website's static files; TLE requests go directly from the
browser to the orbital-data providers.

After preparing the Python assets above:

```bash
cd website
npm ci
npx wrangler login  # only needed if this machine is not already authenticated
npm run build
npm run deploy:check
npm run deploy
```

`wrangler.jsonc` declares the custom domain. The authenticated Cloudflare account
must own an active `hyperalislabs.com` zone; Wrangler provisions the subdomain and
TLS certificate. Change the route/name for another account. No GitHub integration
or Actions execution is needed for deployment or fresh TLEs. Publishing updates
is an explicit local command. Wrangler requires Node 22 or newer.

The website uses a **simplified timezone boundary dataset**, rather than splitting
large files or routing around the host's limits. `scripts/simplify_timezones.py`
decodes the pinned upstream database, reduces boundary vertices with GEOS coverage
simplification (tolerance parameter **0.01 degrees**), and rebuilds every polygon,
hole and spatial index with the checksummed upstream 9.0.0 compiler. It retains all
zones, polygons and holes. The tolerance is an algorithm parameter, not a guaranteed
maximum distance error. Near timezone borders or coastlines, automatic selection
can differ; enter a manual IANA timezone when that matters. UTC predictions,
propagation, coverage, illumination and footprint calculations are unchanged.

The derived data wheel is labelled `+overpass.1`, carries its source/compiler hashes,
vertex counts, simplification settings, sample diagnostics and original ODbL data
licence. Its build enforces a **10 MiB** size budget. The original source wheel and
Python package remain unchanged. No split-file loader or extra storage service is
used. The first preparation rebuilds the global spatial index and can take several
minutes; later builds reuse the checksummed result. Build-only Pydantic and Numba dependencies
are isolated under `website/.cache/build-deps/` and never shipped to browsers.

## Static preview

```bash
cd website
npm run build
npm run preview
```

Deploy only `website/dist/` to the static host. Python assets are copied during
preparation; the website's orbit logo is used in the header and browser tab.
No application server is involved.
Cloudflare is the deployment target. There is no GitHub Pages workflow, automatic
website publishing or scheduled orbital-data job. The relative Vite base also
supports hosting under a repository subpath. Use HTTP(S), not `file://`, for module
workers and resource loading.

## Start time and mobile use

Start time defaults to UTC. Select **Local time** to use the browser/device timezone,
which is shown with the UTC offset for the chosen date. Switching the display keeps
the same instant, including repeated autumn hours. Editing a repeated local hour
uses its first occurrence; the hint shows the selected offset and UTC can select
either occurrence. Nonexistent spring-forward times are rejected. The worker always
receives UTC. Result timezone remains a separate ROI-based setting.

On a narrow/mobile screen, an initial dialog explains the network, CPU and memory
cost. Dismiss it by tapping outside, pressing Escape or choosing Continue. Dismissal
lasts for that browser tab when session storage is available. No Python runtime is
loaded until a calculation starts or live satellites are enabled.

## Globe and live positions

Use **3D globe** to load the pinned MapLibre GL JS renderer and its worker from
the website. OpenStreetMap raster tiles cover the globe up to ±85° latitude;
the poles have a plain background. No API key, terrain, or imagery service is
required. The globe shows the same region and filtered, selectable footprints as
the 2D map. **Whole Earth** zooms out. Drawing or editing returns to the 2D map;
coordinates and GeoJSON uploads work in either view. The 2D map remains available
if WebGL fails. Arrow keys pan and +/− zoom; there is no automatic rotation.

**Live satellites** is optional and uses the selected satellites and supplied TLEs,
or retrieves elements directly in the browser with the existing two-hour cache.
The website adapter reuses vectorized SGP4 propagation from the bundled package.
It computes a thirty-minute buffer once per ten-minute validity window: ten
minutes behind the anchor and twenty minutes ahead, sampled every ten seconds.
This keeps **past ten minutes dotted** and **next ten minutes solid** available
throughout the window. All selected satellites are processed in one worker job.
The bundled package and prediction sampling remain unchanged; batch samples are
checked against its public `satellite_positions` API.

Markers interpolate geodetic unit normals with a cubic curve between these
samples, handling polar motion and the date line. Lightweight marker animation
runs four times per second; track clipping and labels update once per second.
Neither operation calls Python or propagates an orbit. An expired buffer is
replaced, never extrapolated; changing satellites or TLEs invalidates it. Cached
tracks survive pausing, tab visibility changes and toggling live view, provided
the window is still valid. Freshness continues to use each actual element epoch.
Positions are SGP4 estimates, not telemetry or acquisition evidence. Choose a
satellite in the list to center either view on it. The prediction start date and
pass filters do not change live time.

Live tracking shares one Pyodide interpreter with predictions and pauses while a
prediction runs or the tab is hidden. Paused markers and paths are cleared;
cancelled or superseded batches cannot reappear. Switching live view off stops
animation. Direct orbital inputs retain their existing two-hour cache and provider
backoff rules, with a cache check when a new ten-minute batch is needed.

**Day / night** is enabled by default in both views and works before Python loads.
It shades the current geometric night hemisphere using the same low-order solar
model as the package, updated once per minute and after a tab becomes visible.
It is independent of prediction dates and does not modify illumination filters.
The display does not model atmospheric twilight or refraction. Turn the overlay
off with its checkbox if desired.

## Direct browser orbital data

`src/orbital-data.ts` fetches only the selected NORAD IDs when a calculation needs
them, using ordinary CORS-enabled HTTPS GET requests to CelesTrak. There is no
download on page load, polling, proxy, credential or scheduled job. A failed primary
request tries the existing TLE API community mirror; if it is unavailable or its
CORS policy prevents access, the browser uses a previously downloaded valid cache
with an explicit warning, or asks for pasted/uploaded TLEs. It does not silently
drop unavailable satellites.

Successful downloads are cached in this browser's localStorage for two hours,
matching [CelesTrak's update/usage policy](https://celestrak.org/usage-policy.php).
If persistent storage is unavailable, an in-memory cache lasts for the page session.
Failures pause further requests to that provider for two hours, including across
reloads when storage is available, to avoid repeatedly querying a failing or
rate-limited service. Manually supplied elements bypass network requests and the
download cache. No hosting rebuild is needed to update orbital data.

The browser validates paired NORAD IDs and TLE checksums before caching, then the
unchanged Python package validates the text again. Source and original retrieval
time are recorded per satellite in downloaded JSON provenance. Reusing a cache
never changes its retrieval timestamp. Cache reuse time and per-pass orbital-epoch
freshness are distinct; the existing 7/14-day scientific labels are unchanged.

Cancel also aborts an in-progress orbital download. The providers receive satellite
IDs and the usual connection metadata, including the user's IP and browser Origin;
the region, settings, files and results are not sent to them. Direct requests depend
on providers being available and continuing to allow browser access. The manual TLE
input remains usable during outages, for historical elements and offline work once
the application's runtime assets have loaded.

There is no hosted TLE snapshot or automatic fallback to bundled historical inputs. Test data is excluded
from the production build even if present during development. No uploads or result
files are saved to the hosting service. Map tiles are fetched from OpenStreetMap;
the tile provider can infer the viewed map area. Change `VITE_TILE_URL` and
`VITE_TILE_ATTRIBUTION` together at build time to use another provider. Keep visible
attribution and follow the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Browser integration and dependency manifest

`python-dependencies.json` pins Pyodide **314.0.7** and additional Python dependencies.
`scripts/prepare.py` downloads the runtime and the transitive package set from its
checksummed lockfile, checks runtime and package hashes, builds the project's unchanged wheel with `uv
build`, and writes a generated `build.json` containing wheel hashes and the package
version. The worker uses those hashes when installing wheels. Build outputs,
caches and dependencies are ignored by Git.

Pyodide supplies NumPy, pandas, GeoPandas, Shapely, pyproj, H3 and CFFI. Its GeoPandas
distribution uses Fiona instead of pyogrio. Dependency resolution is disabled
when installing the separately pinned wheels because that browser distribution
already supplies its geospatial dependencies. Every calculation is tested through
the real package; no substitute propagation/geometry modules are installed.

SGP4's upstream pure-Python wheel uses its supported accelerator fallback.
timezonefinder 9.0.0 is packaged from its checksummed upstream source archive with
its Python modules, original metadata, schemas and licence; its optional C
accelerator is absent, using the upstream pure-Python implementation instead.
Timezone boundary data is a website-owned simplified derivative of the separately
pinned upstream data wheel, as described above. The upstream library code is unchanged.

`python/browser_adapter.py` contains the browser I/O orchestration. It calls the
package's ROI loader, offline TLE parser, full-window predictor, presentation
document builder and CSV exporter. Export files exist briefly in Pyodide's virtual
filesystem and are returned as download content. It never changes package files.

One scoped ABI compatibility bridge is required by timezonefinder 9.0: on wasm32,
NumPy `repeat`/`reduceat` require native-width indexes while timezonefinder constructs
explicit 64-bit counts. During automatic timezone lookup the adapter converts
bounded integer repeat counts and hole-index arrays to `np.intp`, then restores the
original NumPy functions in `finally`. Coordinates, polygon tests and orbital
calculations are unchanged. Browser/native parity tests cover automatic ROI lookup.

Progress reports runtime loading, dependencies, inputs, whole-satellite calculation
and export. Satellites are calculated individually over the **complete** requested
window, retaining the package's 30-second coarse / 1-second fine sampling, then
globally sorted. ETA is a rough estimate from completed satellites; one difficult
satellite can take longer. Cancel terminates the worker. A subsequent run creates
a new worker; previous completed downloads remain available. Request IDs prevent
late/cancelled responses from replacing results, and settings edited during a run
remain marked as requiring recalculation.

The main browser thread retrieves orbital data before starting the calculation
worker; its abort controller and request ID also prevent cancelled downloads from
starting obsolete calculations. Worker messages use `predict` with `{id, base, request}` and return `progress`,
`ready`, `result` or `error` with the same ID. `request` carries ROI GeoJSON, selected
catalogue names, UTC start, days, timezone override, filters, SAR mode and TLE text,
plus optional per-satellite orbital sources and retrieval warnings.
`result.document` retains the package's version 2.0 prediction response, alongside
CSV content/rows and elapsed time. This internal website protocol does not change
the package or CLI contracts.

## Validation

```bash
# From the repository root:
uv run python website/scripts/prepare.py --test-data
uv run python website/tests/prepare_reference.py
cd website
npm ci
npx playwright install chromium
npm run check
npm test
npm run build
```

Set `EO_CHROMIUM_PATH` to use an existing Chromium executable. Set `WEBSITE_PYTHON`
to an absolute Python executable if the root environment is not `.venv`. Native
Python expectations are generated into ignored **test artifacts**, not regression
baselines. Browser tests serve all interpreter/wheel assets locally and block or
mock external requests, including map tiles and orbital data.

Browser retrieval tests cover selected-satellite requests, CORS/network/HTTP failure,
community-mirror fallback, cache expiry/reuse, stale-cache warnings, corrupted
responses/cache entries, unavailable storage and cancellation during downloads.
All providers are mocked; the test suite does not depend on upstream services.
The suite exercises real Pyodide predictions against native Python for optical
point/polygon inputs, multi-satellite merging, empty results, Sentinel-1 IW/EW and
timezone/DST handling. It compares timestamps, rounded quantities, CSV output and
footprint geometry using the repository's 10 cm parity tolerance. This establishes
implementation parity, not independent observational accuracy. Separate UI tests
cover drawing/editing/deletion, uploads, validation, options, map/table selection,
complete and filtered downloads, cancellation, error recovery, stale responses,
settings changes, keyboard operation and narrow layouts.
Real WebGL tests cover lazy loading, view switching, live markers, mobile/keyboard
controls, graphics failure and recovery. Live-position checks compare all seven
satellites against direct native `satellite_positions` at explicit frozen times
(1e-7 absolute tolerance for batch samples); interpolation at intermediate times
is checked within 25 m on the ground and 10 m in altitude. These are parity
tolerances, not observational accuracy guarantees. Tests also cover ten-minute
cache rollover/reuse, dotted/solid trails, date-line clipping, seasonal solar
geometry, current-night toggling, stale-element warnings, selection changes,
hidden-tab pausing, shared-runtime cancellation and rejected late responses.

Also run the repository's standard Python and distribution checks from `AGENTS.md`.
CI runs the Python tests once on Ubuntu with Python 3.12. Website browser tests,
formatting, typing and distribution validation remain available as local commands;
there is no operating-system or Python-version matrix. Tagged publishing remains
a separate release workflow.

## Scientific interpretation

Geometric intersection, expected imaging, ESA plan evidence and available imagery
are distinct. This release does not retrieve ESA plans or imagery: Sentinel
acquisition is unknown and Landsat acquisition is not checked. Freshness labels
at 7/14 days are refresh reminders, not calibrated accuracy probabilities. The
package's swath-edge, SAR nominal-geometry and geographic limitations apply.
Display filters cannot recover passes excluded at calculation time.

Scientific implementation and package resources: **Julian Manning**, MIT licence.
The built wheel retains the original licence and author metadata. Frontend mapping
uses Leaflet (BSD-2-Clause), Leaflet.draw (MIT), MapLibre GL JS 6.16.0 (BSD-3-Clause)
and attributed OpenStreetMap data. `public/map-licenses.txt` retains the licences
of the pinned map packages and their transitive dependencies; update it when
upgrading those dependencies.
Python dependency wheels retain their upstream licences. The derived timezone
dataset has an explicit local version and provenance; it is distributed under the
original ODbL terms.
